"""PayPal Orders v2, sandbox only.

Creating an order, having the buyer approve it and capturing it are three
separate calls, and money moves on the last one. The helpers here keep that
order straight so the app cannot skip the middle step.
"""
import base64
import uuid
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from ..models import CartLine

TIMEOUT = 25.0


class PayPalError(Exception):
    """Raised when PayPal fails; the API layer turns this into a 502."""


def order_total(lines: List[CartLine]) -> float:
    return round(sum(round(line.unit_price * line.quantity, 2) for line in lines), 2)


async def _access_token(client: httpx.AsyncClient) -> str:
    raw = f"{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_CLIENT_SECRET}"
    auth = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    try:
        response = await client.post(
            f"{settings.PAYPAL_BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except httpx.HTTPError as exc:
        raise PayPalError(f"Could not reach PayPal: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        raise PayPalError(
            "PayPal authentication failed. Check PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET "
            f"(HTTP {response.status_code})."
        )
    return response.json()["access_token"]


def _approve_url(data: Dict[str, Any]) -> Optional[str]:
    for link in data.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            return link.get("href")
    return None


async def create_order(lines: List[CartLine], currency: Optional[str] = None) -> Dict[str, Any]:
    if not lines:
        raise PayPalError("Cart is empty.")

    code = (currency or settings.CURRENCY).upper()
    total = order_total(lines)

    if not settings.paypal_enabled:
        return {
            "order_id": f"MOCK-{uuid.uuid4().hex[:10].upper()}",
            "approve_url": None,
            "status": "CREATED",
            "mode": "mock",
            "amount": total,
            "currency": code,
        }

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": f"speakcart-{uuid.uuid4().hex[:8]}",
                "description": f"{settings.BRAND_NAME} grocery order",
                # Only the total is sent. PayPal item quantities must be whole
                # numbers, and a grocery list easily contains "2.5 kg", which
                # PayPal rejects; an itemised breakdown whose sum drifts from
                # the total by a paisa is rejected too. The total is exact, so
                # the payment always goes through.
                "amount": {"currency_code": code, "value": f"{total:.2f}"},
            }
        ],
        "application_context": {
            "brand_name": settings.BRAND_NAME,
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": settings.RETURN_URL,
            "cancel_url": settings.CANCEL_URL,
        },
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        token = await _access_token(client)
        try:
            response = await client.post(
                f"{settings.PAYPAL_BASE}/v2/checkout/orders",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise PayPalError(f"Could not create the PayPal order: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        raise PayPalError(f"PayPal rejected the order (HTTP {response.status_code}): {response.text[:300]}")

    data = response.json()
    return {
        "order_id": data.get("id", ""),
        "approve_url": _approve_url(data),
        "status": data.get("status", "CREATED"),
        "mode": "live",
        "amount": total,
        "currency": code,
    }


async def order_status(order_id: str) -> Dict[str, Any]:
    """Read an order's state: CREATED, APPROVED, COMPLETED, VOIDED..."""
    if not order_id:
        raise PayPalError("order_id is required.")

    if not settings.paypal_enabled or order_id.startswith("MOCK-"):
        return {"order_id": order_id, "status": "CREATED", "mode": "mock"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        token = await _access_token(client)
        try:
            response = await client.get(
                f"{settings.PAYPAL_BASE}/v2/checkout/orders/{order_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise PayPalError(f"Could not read the PayPal order: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        raise PayPalError(
            f"PayPal could not return the order (HTTP {response.status_code}): {response.text[:200]}"
        )

    data = response.json()
    return {"order_id": data.get("id", order_id), "status": data.get("status", "UNKNOWN"), "mode": "live"}


async def capture_order(order_id: str) -> Dict[str, Any]:
    if not order_id:
        raise PayPalError("order_id is required.")

    if not settings.paypal_enabled or order_id.startswith("MOCK-"):
        return {
            "order_id": order_id,
            "status": "COMPLETED",
            "capture_id": f"MOCK-CAPTURE-{uuid.uuid4().hex[:10].upper()}",
            "payer_email": "sb-buyer@personal.example.com",
            "amount": None,
            "currency": settings.CURRENCY,
            "mode": "mock",
        }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        token = await _access_token(client)
        try:
            response = await client.post(
                f"{settings.PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture",
                json={},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise PayPalError(f"Could not capture the PayPal order: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        body = response.text or ""
        if "ORDER_NOT_APPROVED" in body:
            raise PayPalError(
                "This order has not been approved in PayPal yet. Open the approve link, "
                "log in as the buyer, press Complete Purchase, then capture again."
            )
        if "ORDER_ALREADY_CAPTURED" in body:
            raise PayPalError("This order was already captured - nothing more to collect.")
        if "ORDER_EXPIRED" in body:
            raise PayPalError("This order expired. Create a new one and approve it promptly.")
        raise PayPalError(f"PayPal capture failed (HTTP {response.status_code}): {body[:300]}")

    data = response.json()
    capture_id = None
    amount = None
    currency = None
    try:
        capture = data["purchase_units"][0]["payments"]["captures"][0]
        capture_id = capture.get("id")
        amount = float(capture["amount"]["value"])
        currency = capture["amount"]["currency_code"]
    except (KeyError, IndexError, TypeError, ValueError):
        pass

    return {
        "order_id": data.get("id", order_id),
        "status": data.get("status", "UNKNOWN"),
        "capture_id": capture_id,
        "payer_email": (data.get("payer") or {}).get("email_address"),
        "amount": amount,
        "currency": currency,
        "mode": "live",
    }
