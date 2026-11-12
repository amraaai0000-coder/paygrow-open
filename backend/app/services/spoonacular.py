"""Product search, with a small catalogue of its own for when there is no key.

The awkward part of this API: search returns a name and an image but no price,
and the details endpoint has a price whose unit nobody documents. So every
product carries where its price came from, and the app shows that.
"""
import hashlib
import math
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import quote

import httpx

from ..config import settings
from ..models import ParsedItem, Product, ProductDetail, ResolvedItem

BASE_URL = "https://api.spoonacular.com"
TIMEOUT = 20.0


class UpstreamError(Exception):
    """Raised when Spoonacular fails; the API layer turns this into a 502."""


# Repeat searches are common in a demo (the same sentence over and over), and
# each live call costs a point out of the 150/day free allowance. Cache them.
_search_cache: "dict[str, tuple[float, List[Product]]]" = {}


def _cache_get(query: str) -> Optional[List[Product]]:
    key = (query or "").strip().lower()
    entry = _search_cache.get(key)
    if not entry:
        return None
    stored_at, products = entry
    if time.time() - stored_at > settings.SPOONACULAR_CACHE_TTL_SECONDS:
        _search_cache.pop(key, None)
        return None
    return products


def _cache_set(query: str, products: List[Product]) -> None:
    key = (query or "").strip().lower()
    if not key or not products:
        return
    if len(_search_cache) > 500:
        _search_cache.clear()
    _search_cache[key] = (time.time(), products)


def cache_size() -> int:
    return len(_search_cache)


# Spoonacular reports usage in every response:
#   X-API-Quota-Used    points used today (resets at midnight UTC)
#   X-API-Quota-Request points this call cost
_quota: dict = {"used_today": None, "last_request_cost": None, "seen_at": None}


def _record_quota(headers) -> None:
    try:
        used = headers.get("x-api-quota-used")
        cost = headers.get("x-api-quota-request")
    except AttributeError:
        return
    if used is None and cost is None:
        return
    _quota["used_today"] = float(used) if used is not None else _quota["used_today"]
    _quota["last_request_cost"] = float(cost) if cost is not None else _quota["last_request_cost"]
    _quota["seen_at"] = time.time()


def quota_snapshot() -> dict:
    return dict(_quota)


def _is_quota_error(exc: "UpstreamError") -> bool:
    text = str(exc).lower()
    return "402" in text or "quota" in text


# (keyword, title, price, aisle) - used only when no API key is configured
# (or when ALLOW_MOCK=false is not set) and in the automated tests.
_MOCK_CATALOG: List[Tuple[str, str, float, str]] = [
    ("milk", "Amul Taaza Toned Milk 1 L", 1.29, "Dairy"),
    ("bread", "Whole Wheat Sandwich Bread 400 g", 2.49, "Bakery"),
    ("egg", "Farm Fresh Brown Eggs (6 pack)", 3.99, "Dairy"),
    ("rice", "Basmati Rice 1 kg", 4.49, "Grains"),
    ("flour", "Whole Wheat Atta 1 kg", 3.19, "Grains"),
    ("sugar", "Refined Sugar 500 g", 1.99, "Pantry"),
    ("oil", "Sunflower Cooking Oil 1 L", 4.99, "Pantry"),
    ("tea", "Masala Chai Tea 250 g", 3.49, "Beverages"),
    ("coffee", "Instant Coffee 100 g", 5.99, "Beverages"),
    ("butter", "Salted Butter 100 g", 2.29, "Dairy"),
    ("ghee", "Pure Cow Ghee 500 ml", 7.49, "Dairy"),
    ("yogurt", "Plain Curd 400 g", 1.89, "Dairy"),
    ("paneer", "Fresh Paneer 200 g", 3.99, "Dairy"),
    ("potato", "Potato 1 kg", 1.49, "Produce"),
    ("onion", "Onion 1 kg", 1.59, "Produce"),
    ("tomato", "Tomato 500 g", 1.39, "Produce"),
    ("salt", "Iodised Salt 1 kg", 0.99, "Pantry"),
    ("lentil", "Toor Dal 1 kg", 3.79, "Pantry"),
    ("soap", "Bathing Soap (4 pack)", 2.99, "Personal Care"),
    ("shampoo", "Anti-Dandruff Shampoo 180 ml", 4.59, "Personal Care"),
    ("biscuit", "Marie Biscuits 250 g", 1.29, "Snacks"),
    ("noodle", "Instant Noodles (6 pack)", 2.79, "Snacks"),
    ("chocolate", "Milk Chocolate Bar 100 g", 1.99, "Snacks"),
    ("water", "Mineral Water 1 L", 0.79, "Beverages"),
]


def _stable_price(seed: str, low: float = 1.29, high: float = 5.99) -> float:
    digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return round(low + (digest % 400) / 100.0, 2)


def _placeholder_image(title: str) -> str:
    return f"https://placehold.co/400x400/f4f4f5/52525b?text={quote(title)}"


def simulated_availability(product_id: str) -> str:
    """Deterministic stand-in for stock status (Spoonacular has no such field)."""
    digest = int(hashlib.sha256(f"availability:{product_id}".encode("utf-8")).hexdigest()[:8], 16)
    bucket = digest % 10
    if bucket == 0:
        return "out_of_stock"
    if bucket <= 2:
        return "low_stock"
    return "in_stock"


# --------------------------------------------------------------------------- #
# Pack maths
#
# A shopper says "500 g paneer", but a product is priced per pack ("200 g").
# Multiplying the request straight into the cart turns 500 into 500 packs, so
# gram/litre requests are converted into a number of packs instead.
# --------------------------------------------------------------------------- #
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|grams?|ml|l|ltr|litres?|liters?|oz|lb)\b")

_BASE_FACTORS = {
    "kg": 1000.0, "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0,
    "ml": 1.0, "l": 1000.0, "ltr": 1000.0, "litre": 1000.0, "litres": 1000.0,
    "liter": 1000.0, "liters": 1000.0, "oz": 28.35, "lb": 453.6,
}

# Units that already count packs/pieces, so no conversion is needed.
_COUNT_UNITS = {"packet", "piece", "dozen", "box", "bottle", "bunch"}


def _pack_size(title: str) -> Optional[Tuple[float, str]]:
    match = _SIZE_RE.search((title or "").lower())
    if not match:
        return None
    return float(match.group(1)), match.group(2)


def cart_quantity_for(item: ParsedItem, product: Optional[Product]) -> Tuple[float, Optional[str]]:
    """Return (number of packs to buy, note explaining the conversion)."""
    requested = float(item.quantity or 1)

    if product is None or not item.unit or item.unit in _COUNT_UNITS:
        return requested, None

    unit_factor = _BASE_FACTORS.get(item.unit)
    if unit_factor is None:
        return requested, None

    size = _pack_size(product.title)
    if size is None:
        # Pack size unknown - buy a single pack rather than 500 of them.
        return 1.0, None

    size_value, size_unit = size
    size_factor = _BASE_FACTORS.get(size_unit)
    if not size_factor or size_value <= 0:
        return 1.0, None

    needed = requested * unit_factor
    available = size_value * size_factor
    packs = max(1, math.ceil(round(needed / available, 6)))
    note = f"{packs} x {size_value:g} {size_unit} pack(s) for {requested:g} {item.unit}"
    return float(packs), note


def price_from_info(info: dict) -> Tuple[float, str, Optional[float]]:
    """Map Spoonacular's undocumented `price` field onto (price, source, raw)."""
    raw = info.get("price")
    if isinstance(raw, (int, float)) and raw > 0:
        if raw >= 100:
            # Looks like cents (e.g. 324 for a 10.5 oz bag).
            return round(float(raw) / 100.0, 2), "api_assumed_cents", float(raw)
        return round(float(raw), 2), "api", float(raw)
    seed = info.get("title") or str(info.get("id"))
    return _stable_price(seed), "simulated", None


def _image_url(info: dict) -> Optional[str]:
    if info.get("image"):
        return info["image"]
    images = info.get("images") or []
    if images:
        return images[-1]
    if info.get("id") and info.get("imageType"):
        return f"https://img.spoonacular.com/products/{info['id']}-312x231.{info['imageType']}"
    return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
async def _get_json(path: str, params: dict) -> dict:
    query = {**params, "apiKey": settings.SPOONACULAR_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{BASE_URL}{path}", params=query)
            response.raise_for_status()
            _record_quota(getattr(response, "headers", {}) or {})
            return response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise UpstreamError(
                "Spoonacular rejected the API key (401/403). Check SPOONACULAR_API_KEY."
            ) from exc
        if status == 402:
            raise UpstreamError(
                "Spoonacular daily quota exhausted (402). Wait for the reset or upgrade the plan."
            ) from exc
        if status == 404:
            raise UpstreamError("Spoonacular has no product with that id (404).") from exc
        raise UpstreamError(f"Spoonacular returned HTTP {status}.") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Could not reach Spoonacular: {exc.__class__.__name__}.") from exc


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def _mock_search(query: str, number: int) -> List[Product]:
    q = (query or "").lower().strip()
    hits = [row for row in _MOCK_CATALOG if q and (q in row[0] or q in row[1].lower())]
    if not hits:
        words = [w for w in re.split(r"\W+", q) if len(w) > 2]
        hits = [
            row for row in _MOCK_CATALOG
            if any(w in row[0] or w in row[1].lower() for w in words)
        ]

    products = []
    for keyword, title, price, aisle in hits[:number]:
        product_id = f"mock-{keyword}"
        products.append(
            Product(
                id=product_id,
                title=title,
                image=_placeholder_image(title),
                price=price,
                currency=settings.CURRENCY,
                price_source="simulated",
                aisle=aisle,
                availability=simulated_availability(product_id),
                source="mock",
            )
        )

    if not products:
        title = (query or "grocery item").strip().title()
        slug = re.sub(r"\W+", "-", q).strip("-") or "item"
        product_id = f"mock-{slug}"
        products.append(
            Product(
                id=product_id,
                title=title,
                image=_placeholder_image(title),
                price=_stable_price(q or slug),
                currency=settings.CURRENCY,
                price_source="simulated",
                aisle="Grocery",
                availability=simulated_availability(product_id),
                source="mock",
            )
        )
    return products


async def _live_search(query: str, number: int) -> List[Product]:
    data = await _get_json("/food/products/search", {"query": query, "number": number})

    products: List[Product] = []
    for row in data.get("products", [])[:number]:
        title = row.get("title") or query
        product_id = str(row.get("id", ""))
        raw_price = row.get("price")
        if isinstance(raw_price, (int, float)) and raw_price > 0:
            price, source = round(float(raw_price), 2), "api"
        else:
            price, source = _stable_price(title), "simulated"
        products.append(
            Product(
                id=product_id,
                title=title,
                image=_image_url(row),
                price=price,
                currency=settings.CURRENCY,
                price_source=source,
                aisle=row.get("aisle"),
                availability=simulated_availability(product_id or title),
            )
        )
    return products


async def _quota_fallback(query: str, number: int) -> Optional[Tuple[List[Product], str]]:
    """Spoonacular is out of points for today - try another catalogue."""
    try:
        from . import openfoodfacts

        products = await openfoodfacts.search_products(query, number)
        if products:
            return products, "openfoodfacts"
    except Exception:  # noqa: BLE001 - a fallback must never break the request
        pass

    if settings.ALLOW_MOCK:
        return _mock_search(query, number), "mock-quota"
    return None


async def search_products(query: str, number: int = 6) -> Tuple[List[Product], str]:
    """Search one item. Returns (products, mode) where mode is 'live' or 'mock'."""
    if not settings.spoonacular_enabled:
        if not settings.ALLOW_MOCK:
            raise UpstreamError(
                "SPOONACULAR_API_KEY is not set and mock mode is disabled (ALLOW_MOCK=false)."
            )
        return _mock_search(query, number), "mock"

    cached = _cache_get(query)
    if cached:
        return cached, "live-cached"

    try:
        products = await _live_search(query, number)
    except UpstreamError as exc:
        if _is_quota_error(exc):
            fallback = await _quota_fallback(query, number)
            if fallback is not None:
                _cache_set(query, fallback[0])
                return fallback
        raise

    _cache_set(query, products)
    return products, "live"


# --------------------------------------------------------------------------- #
# Product information
# --------------------------------------------------------------------------- #
def _mock_product_detail(product_id: str) -> ProductDetail:
    key = product_id.replace("mock-", "")
    for keyword, title, price, aisle in _MOCK_CATALOG:
        if keyword == key:
            return ProductDetail(
                id=product_id,
                title=title,
                image=_placeholder_image(title),
                aisle=aisle,
                price=price,
                price_source="simulated",
                currency=settings.CURRENCY,
                availability=simulated_availability(product_id),
            )
    title = key.replace("-", " ").title() or "Grocery item"
    return ProductDetail(
        id=product_id,
        title=title,
        image=_placeholder_image(title),
        price=_stable_price(key or title),
        price_source="simulated",
        currency=settings.CURRENCY,
        availability=simulated_availability(product_id),
    )


def _nutrient(info: dict, name: str) -> Optional[float]:
    for nutrient in (info.get("nutrition") or {}).get("nutrients", []) or []:
        if str(nutrient.get("name", "")).lower() == name.lower():
            amount = nutrient.get("amount")
            return float(amount) if isinstance(amount, (int, float)) else None
    return None


async def product_detail(product_id: str) -> ProductDetail:
    if not settings.spoonacular_enabled or str(product_id).startswith("mock-"):
        if not settings.spoonacular_enabled and not settings.ALLOW_MOCK:
            raise UpstreamError(
                "SPOONACULAR_API_KEY is not set and mock mode is disabled (ALLOW_MOCK=false)."
            )
        return _mock_product_detail(product_id)

    info = await _get_json(f"/food/products/{product_id}", {})
    price, price_source, price_raw = price_from_info(info)

    servings = info.get("servings") or {}
    servings_text = servings.get("raw")
    if not servings_text and servings.get("number") and servings.get("unit"):
        servings_text = f"{servings.get('number')} {servings.get('unit')}"

    calories = _nutrient(info, "Calories")

    def _macro(name: str) -> Optional[str]:
        amount = _nutrient(info, name)
        return f"{amount:g} g" if amount is not None else None

    return ProductDetail(
        id=str(info.get("id", product_id)),
        title=info.get("title") or "Unknown product",
        image=_image_url(info),
        brand=info.get("brand"),
        aisle=info.get("aisle"),
        badges=list(info.get("importantBadges") or info.get("badges") or [])[:6],
        price=price,
        price_raw=price_raw,
        price_source=price_source,
        currency=settings.CURRENCY,
        availability=simulated_availability(str(info.get("id", product_id))),
        servings=servings_text,
        calories=calories,
        protein=_macro("Protein"),
        fat=_macro("Fat"),
        carbs=_macro("Carbohydrates"),
        spoonacular_score=info.get("spoonacularScore"),
        ingredient_count=info.get("ingredientCount"),
    )


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
async def resolve_items(items: List[ParsedItem], number: int = 6) -> Tuple[List[ResolvedItem], str]:
    """Turn parsed intents into product lists ready for the product cards."""
    resolved: List[ResolvedItem] = []
    mode = settings.spoonacular_mode

    for item in items:
        products, item_mode = await search_products(item.query or item.name, number)
        if item_mode == "live":
            mode = "live"

        chosen = products[0] if products else None
        cart_quantity, cart_note = cart_quantity_for(item, chosen)

        resolved.append(
            ResolvedItem(
                item=item,
                products=products,
                chosen=chosen,
                cart_quantity=cart_quantity,
                cart_note=cart_note,
            )
        )
    return resolved, mode
