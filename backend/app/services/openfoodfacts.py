"""A fallback catalogue that costs nothing and needs no key.

Names and images, no prices. Indian grocery coverage is thin, which is exactly
why this sits behind Spoonacular instead of in front of it.
"""
import hashlib
import re
from typing import List
from urllib.parse import quote

import httpx

from ..config import settings
from ..models import Product

BASE_URL = "https://world.openfoodfacts.org/api/v2/search"
TIMEOUT = 20.0
FIELDS = "code,product_name,product_name_en,brands,image_front_small_url,image_front_url,quantity,categories"

# Open Food Facts asks clients to identify themselves.
USER_AGENT = "PayGrow-Hackathon/1.0 (https://github.com/amraaai0000-coder/paygrow)"


class OffError(Exception):
    """Raised when Open Food Facts cannot be reached or refuses the request."""


def _stable_price(seed: str, low: float = 1.29, high: float = 5.99) -> float:
    digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return round(low + (digest % 400) / 100.0, 2)


def _placeholder_image(title: str) -> str:
    return f"https://placehold.co/400x400/f4f4f5/52525b"


async def search_products(query: str, number: int = 6) -> List[Product]:
    """Search Open Food Facts. Returns [] when nothing usable comes back."""
    params = {
        "search_terms": query,
        "fields": FIELDS,
        "page_size": number,
        "sort_by": "popularity_key",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(BASE_URL, params=params, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        raise OffError(f"Could not reach Open Food Facts: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        raise OffError(f"Open Food Facts returned HTTP {response.status_code}.")

    products: List[Product] = []
    for row in (response.json().get("products") or [])[:number]:
        title = (row.get("product_name") or row.get("product_name_en") or "").strip()
        if not title:
            continue
        code = str(row.get("code") or "").strip()
        image = row.get("image_front_small_url") or row.get("image_front_url")
        quantity = (row.get("quantity") or "").strip()
        if quantity:
            title = f"{title} {quantity}"

        products.append(
            Product(
                id=f"off-{code}" if code else "off-" + re.sub(r"\W+", "-", title.lower())[:40],
                title=title,
                image=image or _placeholder_image(title),
                price=_stable_price(title),
                currency=settings.CURRENCY,
                price_source="simulated",
                aisle=(row.get("categories") or "").split(",")[0].strip() or None,
                availability="in_stock",
                source="openfoodfacts",
            )
        )
    return products
