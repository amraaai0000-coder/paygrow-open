"""Exercise the live (real-API) code paths without touching the network.

test_api.py only ever runs in mock mode, so these branches - the ones that
actually run on Render - would otherwise never be executed before the demo.
A fake httpx client is injected into each service module instead.
"""
import asyncio
import os

os.environ.pop("SPOONACULAR_API_KEY", None)
os.environ.pop("PAYPAL_CLIENT_ID", None)
os.environ.pop("PAYPAL_CLIENT_SECRET", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CartLine  # noqa: E402
from app.services import paypal, spoonacular  # noqa: E402


class FakeResponse:
    def __init__(self, url, status_code=200, payload=None):
        self.url = url
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", self.url)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("upstream error", request=request, response=response)


class FakeClient:
    """Minimal stand-in for httpx.AsyncClient, driven by URL fragments."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _match(self, url):
        self.calls.append(url)
        for fragment, status, payload in self.routes:
            if fragment in url:
                return FakeResponse(url, status, payload)
        raise AssertionError("unexpected URL: " + url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, params=None, **kwargs):
        return self._match(url)

    async def post(self, url, json=None, data=None, headers=None, **kwargs):
        return self._match(url)


class _Shim:
    """Module-level httpx replacement: swaps AsyncClient, forwards everything else."""

    def __init__(self, fake):
        self.AsyncClient = lambda **kwargs: fake

    def __getattr__(self, item):
        return getattr(httpx, item)


def patch_httpx(monkeypatch, module, routes):
    fake = FakeClient(routes)
    monkeypatch.setattr(module, "httpx", _Shim(fake))
    return fake


# --------------------------------------------------------------------------- #
# Spoonacular live path
# --------------------------------------------------------------------------- #
def test_live_search_uses_api_price_and_flags_the_rest(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    patch_httpx(
        monkeypatch,
        spoonacular,
        [
            (
                "/food/products/search",
                200,
                {
                    "products": [
                        {"id": 1, "title": "Amul Milk 1 L", "image": "https://img/1.jpg", "price": 1.35},
                        {"id": 2, "title": "Mother Dairy Milk", "image": "https://img/2.jpg"},
                    ]
                },
            )
        ],
    )

    products, mode = asyncio.run(spoonacular.search_products("milk", 2))

    assert mode == "live"
    assert products[0].price == 1.35
    assert products[0].price_source == "api"
    assert products[0].image == "https://img/1.jpg"
    assert products[1].price_source == "simulated"
    assert products[1].price > 0


def test_live_search_bad_key_becomes_upstream_error(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "bad-key")
    patch_httpx(monkeypatch, spoonacular, [("/food/products/search", 401, {})])

    with pytest.raises(spoonacular.UpstreamError) as excinfo:
        asyncio.run(spoonacular.search_products("milk", 2))
    assert "SPOONACULAR_API_KEY" in str(excinfo.value)


def test_live_search_quota_exhausted_falls_back_to_open_food_facts(monkeypatch):
    """A 402 must not end the demo - another catalogue answers instead."""
    from app.services import openfoodfacts

    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    patch_httpx(monkeypatch, spoonacular, [("/food/products/search", 402, {})])
    patch_httpx(
        monkeypatch,
        openfoodfacts,
        [
            (
                "/api/v2/search",
                200,
                {
                    "products": [
                        {
                            "code": "8901058000001",
                            "product_name": "Amul Taaza Toned Milk",
                            "quantity": "1 L",
                            "image_front_small_url": "https://img.example/1.jpg",
                        }
                    ]
                },
            )
        ],
    )

    products, mode = asyncio.run(spoonacular.search_products("milk", 2))

    assert mode == "openfoodfacts"
    assert products[0].source == "openfoodfacts"
    assert products[0].title == "Amul Taaza Toned Milk 1 L"
    assert products[0].price_source == "simulated"


def test_quota_falls_back_to_the_sample_catalog_when_off_has_nothing(monkeypatch):
    from app.services import openfoodfacts

    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ALLOW_MOCK", True)
    patch_httpx(monkeypatch, spoonacular, [("/food/products/search", 402, {})])
    patch_httpx(monkeypatch, openfoodfacts, [("/api/v2/search", 200, {"products": []})])

    products, mode = asyncio.run(spoonacular.search_products("milk", 2))

    assert mode == "mock-quota"
    assert products and products[0].source == "mock"


def test_quota_error_surfaces_when_fallbacks_are_off(monkeypatch):
    from app.services import openfoodfacts

    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ALLOW_MOCK", False)
    patch_httpx(monkeypatch, spoonacular, [("/food/products/search", 402, {})])
    patch_httpx(monkeypatch, openfoodfacts, [("/api/v2/search", 500, {})])

    with pytest.raises(spoonacular.UpstreamError) as excinfo:
        asyncio.run(spoonacular.search_products("milk", 2))
    assert "quota" in str(excinfo.value).lower()


def test_quota_headers_are_recorded(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    spoonacular._quota.update({"used_today": None, "last_request_cost": None, "seen_at": None})

    class HeaderedResponse(FakeResponse):
        def __init__(self, url, status_code=200, payload=None):
            super().__init__(url, status_code, payload)
            self.headers = {"x-api-quota-used": "17.5", "x-api-quota-request": "1"}

    class HeaderedClient(FakeClient):
        def _match(self, url):
            for fragment, status, payload in self.routes:
                if fragment in url:
                    return HeaderedResponse(url, status, payload)
            raise AssertionError("unexpected URL: " + url)

    monkeypatch.setattr(
        spoonacular,
        "httpx",
        _Shim(HeaderedClient([("/food/products/search", 200, {"products": [{"id": 1, "title": "Milk"}]})])),
    )

    asyncio.run(spoonacular.search_products("milk", 1))

    snapshot = spoonacular.quota_snapshot()
    assert snapshot["used_today"] == 17.5
    assert snapshot["last_request_cost"] == 1.0


def test_search_endpoint_surfaces_upstream_error_as_502(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "bad-key")
    patch_httpx(monkeypatch, spoonacular, [("/food/products/search", 401, {})])

    response = TestClient(app).post("/api/search", json={"text": "milk"})
    assert response.status_code == 502
    assert "SPOONACULAR_API_KEY" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# PayPal live path
# --------------------------------------------------------------------------- #
TOKEN_ROUTE = ("/v1/oauth2/token", 200, {"access_token": "A21AA-test-token"})


def test_live_create_order_extracts_approve_link(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "secret")
    patch_httpx(
        monkeypatch,
        paypal,
        [
            TOKEN_ROUTE,
            (
                "/v2/checkout/orders",
                201,
                {
                    "id": "ORDER123",
                    "status": "CREATED",
                    "links": [
                        {"rel": "self", "href": "https://api/self"},
                        {
                            "rel": "approve",
                            "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER123",
                        },
                    ],
                },
            ),
        ],
    )

    lines = [CartLine(product_id="1", title="Milk", unit_price=1.35, quantity=2)]
    data = asyncio.run(paypal.create_order(lines))

    assert data["mode"] == "live"
    assert data["order_id"] == "ORDER123"
    assert data["approve_url"].endswith("token=ORDER123")
    assert data["amount"] == 2.7
    assert data["currency"] == settings.CURRENCY


def test_live_capture_reads_capture_id_and_payer(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "secret")
    patch_httpx(
        monkeypatch,
        paypal,
        [
            TOKEN_ROUTE,
            (
                "/capture",
                201,
                {
                    "id": "ORDER123",
                    "status": "COMPLETED",
                    "payer": {"email_address": "buyer@example.com"},
                    "purchase_units": [
                        {
                            "payments": {
                                "captures": [
                                    {"id": "CAPTURE9", "amount": {"value": "2.70", "currency_code": "USD"}}
                                ]
                            }
                        }
                    ],
                },
            ),
        ],
    )

    data = asyncio.run(paypal.capture_order("ORDER123"))

    assert data["status"] == "COMPLETED"
    assert data["capture_id"] == "CAPTURE9"
    assert data["payer_email"] == "buyer@example.com"
    assert data["amount"] == 2.7
    assert data["mode"] == "live"


def test_live_bad_credentials_are_explained(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "wrong")
    patch_httpx(monkeypatch, paypal, [("/v1/oauth2/token", 401, {})])

    lines = [CartLine(product_id="1", title="Milk", unit_price=1.0)]
    with pytest.raises(paypal.PayPalError) as excinfo:
        asyncio.run(paypal.create_order(lines))
    assert "PAYPAL_CLIENT_ID" in str(excinfo.value)


def test_capture_endpoint_returns_502_on_paypal_failure(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "secret")
    patch_httpx(
        monkeypatch,
        paypal,
        [TOKEN_ROUTE, ("/capture", 422, {"name": "ORDER_NOT_APPROVED"})],
    )

    response = TestClient(app).post("/api/checkout/paypal/capture", json={"order_id": "ORDER123"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "not been approved" in detail.lower()
    assert "complete purchase" in detail.lower()


def test_order_status_endpoint_reports_the_state(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "secret")
    patch_httpx(
        monkeypatch,
        paypal,
        [TOKEN_ROUTE, ("/v2/checkout/orders/ORDER123", 200, {"id": "ORDER123", "status": "APPROVED"})],
    )

    body = TestClient(app).get("/api/checkout/paypal/order/ORDER123").json()
    assert body["status"] == "APPROVED"
    assert body["mode"] == "live"


def test_order_status_works_in_mock_mode():
    body = TestClient(app).get("/api/checkout/paypal/order/MOCK-ABC123").json()
    assert body["status"] == "CREATED"
    assert body["mode"] == "mock"


# --------------------------------------------------------------------------- #
# Product information (live)
# --------------------------------------------------------------------------- #
def test_live_product_detail_maps_price_brand_and_nutrition(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    patch_httpx(
        monkeypatch,
        spoonacular,
        [
            (
                "/food/products/22347",
                200,
                {
                    "id": 22347,
                    "title": "SNICKERS Minis Size Chocolate Candy Bars 10.5-oz. Bag",
                    "brand": "Snickers",
                    "aisle": None,
                    "importantBadges": ["gluten_free", "msg_free"],
                    "price": 324,
                    "spoonacularScore": 12.5,
                    "ingredientCount": 32,
                    "images": ["https://img.spoonacular.com/products/22347-312x231.jpeg"],
                    "servings": {"number": 1, "size": 4, "unit": "pieces", "raw": "4.0 pieces (38 g)"},
                    "nutrition": {
                        "nutrients": [
                            {"name": "Calories", "amount": 180, "unit": "cal", "percentOfDailyNeeds": 9},
                            {"name": "Protein", "amount": 2, "unit": "g", "percentOfDailyNeeds": 4},
                            {"name": "Fat", "amount": 8, "unit": "g", "percentOfDailyNeeds": 12},
                            {"name": "Carbohydrates", "amount": 25, "unit": "g", "percentOfDailyNeeds": 8},
                        ]
                    },
                },
            )
        ],
    )

    detail = asyncio.run(spoonacular.product_detail("22347"))

    assert detail.price == 3.24
    assert detail.price_raw == 324
    assert detail.price_source == "api_assumed_cents"
    assert detail.brand == "Snickers"
    assert detail.calories == 180
    assert detail.protein == "2 g"
    assert detail.carbs == "25 g"
    assert detail.servings == "4.0 pieces (38 g)"
    assert detail.image.startswith("https://img.spoonacular.com")
    assert detail.availability_source == "simulated"


def test_live_product_detail_zero_price_falls_back_to_simulated(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    patch_httpx(
        monkeypatch,
        spoonacular,
        [("/food/products/30004", 200, {"id": 30004, "title": "Swan Flour", "price": 0})],
    )

    detail = asyncio.run(spoonacular.product_detail("30004"))

    assert detail.price_source == "simulated"
    assert detail.price_raw is None
    assert detail.price > 0


def test_live_product_detail_404_is_explained(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    patch_httpx(monkeypatch, spoonacular, [("/food/products/999", 404, {})])

    with pytest.raises(spoonacular.UpstreamError) as excinfo:
        asyncio.run(spoonacular.product_detail("999"))
    assert "no product" in str(excinfo.value).lower()


def test_mock_mode_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "")
    monkeypatch.setattr(settings, "ALLOW_MOCK", False)

    with pytest.raises(spoonacular.UpstreamError) as excinfo:
        asyncio.run(spoonacular.search_products("milk", 1))
    assert "ALLOW_MOCK" in str(excinfo.value)
