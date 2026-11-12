"""Smoke tests - they run with no API keys at all (mock mode)."""
import os

os.environ.pop("SPOONACULAR_API_KEY", None)
os.environ.pop("PAYPAL_CLIENT_ID", None)
os.environ.pop("PAYPAL_CLIENT_SECRET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.parser import parse_text  # noqa: E402

client = TestClient(app)


def test_health_reports_mock_mode():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["spoonacular"] == "mock"
    assert body["paypal"] == "mock"


def test_parse_english():
    items = parse_text("2 litre milk and one bread")
    assert [i.query for i in items] == ["milk", "bread"]
    assert items[0].quantity == 2
    assert items[0].unit == "l"
    assert items[1].quantity == 1


def test_parse_hinglish():
    items = parse_text("mujhe 1 kg doodh aur 2 packet bread chahiye")
    assert [i.query for i in items] == ["milk", "bread"]
    assert items[0].unit == "kg"
    assert items[1].unit == "packet"
    assert items[1].quantity == 2


def test_parse_devanagari():
    items = parse_text("दो किलो आलू और एक लीटर दूध")
    assert [i.query for i in items] == ["potato", "milk"]
    assert items[0].quantity == 2
    assert items[1].unit == "l"


def test_parse_attached_quantity():
    items = parse_text("500g paneer, 1ltr oil")
    assert items[0].quantity == 500
    assert items[0].unit == "g"
    assert items[1].unit == "l"


def test_parse_hinglish_numbers():
    items = parse_text("ek bread")
    assert items[0].query == "bread"
    assert items[0].quantity == 1

    items = parse_text("do kilo doodh")
    assert items[0].query == "milk"
    assert items[0].quantity == 2
    assert items[0].unit == "kg"


def test_verb_do_is_not_a_quantity():
    items = parse_text("1 kg doodh kar do")
    assert items[0].quantity == 1
    assert items[0].unit == "kg"
    assert "do" not in items[0].query


def test_parse_rejects_empty():
    assert parse_text("") == []
    assert parse_text("please") == []


def test_parse_endpoint_422_on_nonsense():
    response = client.post("/api/parse", json={"text": "  "})
    assert response.status_code == 422


def test_search_returns_products_in_mock_mode():
    response = client.post("/api/search", json={"text": "2 litre milk", "number": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["items"][0]["item"]["query"] == "milk"
    products = body["items"][0]["products"]
    assert products and products[0]["price"] > 0
    assert products[0]["price_source"] == "simulated"
    assert products[0]["image"].startswith("http")


def test_pack_math_converts_grams_to_packs():
    body = client.post("/api/search", json={"text": "500 g paneer", "number": 1}).json()
    group = body["items"][0]
    assert group["chosen"]["title"] == "Fresh Paneer 200 g"
    assert group["cart_quantity"] == 3  # 3 x 200 g covers 500 g
    assert group["cart_note"]


def test_pack_math_converts_litres_to_packs():
    body = client.post("/api/search", json={"text": "2 litre milk", "number": 1}).json()
    assert body["items"][0]["cart_quantity"] == 2  # 2 x 1 L


def test_count_units_pass_through_unchanged():
    body = client.post("/api/search", json={"text": "3 bread", "number": 1}).json()
    assert body["items"][0]["cart_quantity"] == 3


def test_checkout_mock_flow():
    order = client.post(
        "/api/checkout/paypal/order",
        json={
            "lines": [
                {"product_id": "mock-milk", "title": "Milk", "unit_price": 1.29, "quantity": 2},
                {"product_id": "mock-bread", "title": "Bread", "unit_price": 2.49, "quantity": 1},
            ]
        },
    )
    assert order.status_code == 200
    body = order.json()
    assert body["mode"] == "mock"
    assert body["order_id"].startswith("MOCK-")
    assert body["amount"] == 5.07  # 1.29*2 + 2.49

    capture = client.post("/api/checkout/paypal/capture", json={"order_id": body["order_id"]})
    assert capture.status_code == 200
    captured = capture.json()
    assert captured["status"] == "COMPLETED"
    assert captured["capture_id"]


def test_empty_cart_is_rejected():
    response = client.post("/api/checkout/paypal/order", json={"lines": []})
    assert response.status_code == 422


def test_search_products_carry_availability_flag():
    body = client.post("/api/search", json={"text": "milk", "number": 1}).json()
    product = body["items"][0]["products"][0]
    assert product["availability"] in ("in_stock", "low_stock", "out_of_stock")
    assert product["availability_source"] == "simulated"


def test_product_detail_in_mock_mode():
    body = client.get("/api/product/mock-milk").json()
    assert body["title"] == "Amul Taaza Toned Milk 1 L"
    assert body["price_source"] == "simulated"
    assert body["availability_source"] == "simulated"
    assert body["aisle"] == "Dairy"
