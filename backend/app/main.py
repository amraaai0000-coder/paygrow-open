"""The HTTP surface: sessions, the agent, PayPal, speech, and the browser demo.
"""
from pathlib import Path

from typing import List, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db
from .agent import router as agent_router
from .config import settings
from .models import (
    AuthCodeResponse,
    AuthRequestCode,
    AuthUser,
    AuthVerify,
    SessionDetail,
    SessionSummary,
    SttResponse,
    CaptureRequest,
    CaptureResponse,
    OrderRequest,
    OrderResponse,
    OrderStatusResponse,
    ParseRequest,
    ParseResponse,
    ProductDetail,
    SearchRequest,
    SearchResponse,
)
from .parser import parse_text
from .services import paypal, sarvam, spoonacular

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="SpeakCart API",
    version="1.0.0",
    description="Voice/text grocery intent -> Spoonacular products -> PayPal sandbox checkout.",
)

# The Flutter app and the browser demo both call this from a different origin.
app.include_router(agent_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

NO_ITEMS = "No grocery items found in that sentence. Try: '2 litre milk and one bread'."


@app.get("/", include_in_schema=False)
def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"service": "SpeakCart API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "spoonacular": settings.spoonacular_mode,
        "paypal": settings.paypal_mode,
        "sarvam": settings.voice_mode,
        "groq": settings.llm_mode,
        "database": db.mode(),
        "database_source": db.source(),
        "login": "email-code",
        "mock_allowed": settings.ALLOW_MOCK,
        "spoonacular_cache": spoonacular.cache_size(),
        "spoonacular_quota": spoonacular.quota_snapshot(),
        "currency": settings.CURRENCY,
    }


@app.post("/api/parse", response_model=ParseResponse)
def parse(request: ParseRequest):
    items = parse_text(request.text)
    if not items:
        raise HTTPException(status_code=422, detail=NO_ITEMS)
    return ParseResponse(items=items, engine="rules")


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    items = parse_text(request.text)
    if not items:
        raise HTTPException(status_code=422, detail=NO_ITEMS)

    try:
        resolved, mode = await spoonacular.resolve_items(items, number=request.number)
    except spoonacular.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SearchResponse(items=resolved, mode=mode)


@app.get("/api/product/{product_id}", response_model=ProductDetail)
async def product_detail(product_id: str):
    """Full Spoonacular product information for one product id."""
    try:
        return await spoonacular.product_detail(product_id)
    except spoonacular.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/checkout/paypal/order", response_model=OrderResponse)
async def create_paypal_order(request: OrderRequest):
    if not request.lines:
        raise HTTPException(status_code=422, detail="Cart is empty.")
    try:
        data = await paypal.create_order(request.lines, request.currency)
    except paypal.PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OrderResponse(**data)


@app.get("/api/checkout/paypal/order/{order_id}", response_model=OrderStatusResponse)
async def paypal_order_status(order_id: str):
    """Is this order still waiting for approval, or ready to capture?"""
    try:
        data = await paypal.order_status(order_id)
    except paypal.PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OrderStatusResponse(**data)


@app.post("/api/checkout/paypal/capture", response_model=CaptureResponse)
async def capture_paypal_order(request: CaptureRequest):
    try:
        data = await paypal.capture_order(request.order_id)
    except paypal.PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CaptureResponse(**data)


def _owner(authorization: Optional[str], device_id: Optional[str]) -> str:
    """Signed-in user id, else the device id - so guests keep their history."""
    user = auth.user_for_token(authorization)
    if user:
        return user["id"]
    return (device_id or "").strip()


@app.post("/api/auth/request-code", response_model=AuthCodeResponse)
def auth_request_code(body: AuthRequestCode):
    email = auth.normalise_email(body.email)
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="That does not look like an email address.")
    channel, code = auth.request_code(email)
    return AuthCodeResponse(
        sent=True,
        channel=channel,
        # Shown when no mail provider is configured, or while DEBUG_OTP is on.
        dev_code=code if (channel == "dev" or auth.debug_otp()) else None,
    )


@app.post("/api/auth/verify", response_model=AuthUser)
def auth_verify(body: AuthVerify):
    ok, reason, user = auth.verify_code(body.email, body.code)
    if not ok or user is None:
        raise HTTPException(status_code=401, detail=reason or "That code is not right.")
    return AuthUser(**user)


@app.get("/api/auth/me", response_model=AuthUser)
def auth_me(authorization: Optional[str] = Header(None)):
    user = auth.user_for_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return AuthUser(**user)


@app.post("/api/sessions", response_model=SessionSummary)
def create_session(
    authorization: Optional[str] = Header(None),
    x_device_id: Optional[str] = Header(None),
):
    """Start a new conversation. Every session keeps its own history."""
    return db.create_session(owner=_owner(authorization, x_device_id))


@app.get("/api/sessions", response_model=List[SessionSummary])
def list_sessions(
    authorization: Optional[str] = Header(None),
    x_device_id: Optional[str] = Header(None),
):
    return db.list_sessions(owner=_owner(authorization, x_device_id))


@app.get("/api/sessions/{session_id}", response_model=SessionDetail)
def read_session(session_id: str):
    found = db.get_session(session_id)
    if not found:
        raise HTTPException(status_code=404, detail="Session not found.")
    return found


@app.post("/api/stt", response_model=SttResponse)
async def speech_to_text(
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    """Transcribe a chunk of audio and nothing else - the dictation path."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=422, detail="The recording was empty.")
    spoken = None if language.strip().lower() in ("auto", "", "unknown") else language
    try:
        result = await sarvam.speech_to_text(
            audio, file.filename or "voice.wav", language_code=spoken
        )
    except sarvam.SarvamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SttResponse(
        transcript=result.get("transcript", ""),
        language_code=result.get("language_code"),
    )


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
