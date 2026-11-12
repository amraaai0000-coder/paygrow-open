"""The shapes the API speaks, in one place.
"""
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Intent parsing
# --------------------------------------------------------------------------- #
class ParsedItem(BaseModel):
    raw: str = Field(description="The exact chunk of the sentence this came from")
    name: str = Field(description="Item name as the user said it")
    query: str = Field(description="English search query used for Spoonacular")
    quantity: float = 1.0
    unit: Optional[str] = None


class ParseRequest(BaseModel):
    text: str
    language: str = "auto"


class ParseResponse(BaseModel):
    items: List[ParsedItem]
    engine: str = "rules"


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
class Product(BaseModel):
    id: str
    title: str
    image: Optional[str] = None
    price: float
    currency: str
    price_source: str = Field(
        default="simulated",
        description="'api' when Spoonacular returned a price, 'simulated' when derived",
    )
    aisle: Optional[str] = None
    availability: str = Field(
        default="in_stock",
        description="in_stock / low_stock / out_of_stock - SIMULATED, Spoonacular has no stock data",
    )
    availability_source: str = "simulated"
    source: str = Field(
        default="spoonacular",
        description="Which catalogue answered: spoonacular | openfoodfacts | mock",
    )


class ProductDetail(BaseModel):
    """Spoonacular's Get Product Information, with honest source flags."""

    id: str
    title: str
    image: Optional[str] = None
    brand: Optional[str] = None
    aisle: Optional[str] = None
    badges: List[str] = []
    price: float
    price_raw: Optional[float] = Field(
        default=None, description="The untouched `price` value Spoonacular returned"
    )
    price_source: str
    currency: str
    availability: str = "in_stock"
    availability_source: str = "simulated"
    servings: Optional[str] = None
    calories: Optional[float] = None
    protein: Optional[str] = None
    fat: Optional[str] = None
    carbs: Optional[str] = None
    spoonacular_score: Optional[float] = None
    ingredient_count: Optional[int] = None


class ResolvedItem(BaseModel):
    item: ParsedItem
    products: List[Product] = []
    chosen: Optional[Product] = None
    cart_quantity: float = Field(
        default=1.0,
        description="How many packs to put in the cart (gram/litre requests are converted to packs)",
    )
    cart_note: Optional[str] = Field(
        default=None, description="Human readable explanation of the pack conversion"
    )


class SearchRequest(BaseModel):
    text: str
    number: int = Field(default=6, ge=1, le=20)


class SearchResponse(BaseModel):
    items: List[ResolvedItem]
    mode: str


# --------------------------------------------------------------------------- #
# Cart / checkout
# --------------------------------------------------------------------------- #
class CartLine(BaseModel):
    product_id: str
    title: str
    image: Optional[str] = None
    unit_price: float = Field(ge=0)
    quantity: float = Field(default=1, gt=0)
    line_total: Optional[float] = None

    @model_validator(mode="after")
    def _fill_line_total(self):
        if self.line_total is None:
            self.line_total = round(self.unit_price * self.quantity, 2)
        return self


class OrderRequest(BaseModel):
    lines: List[CartLine]
    currency: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    approve_url: Optional[str] = None
    status: str
    mode: str
    amount: float
    currency: str


class OrderStatusResponse(BaseModel):
    order_id: str
    status: str
    mode: str


class CaptureRequest(BaseModel):
    order_id: str


# --------------------------------------------------------------------------- #
# Agent (voice + LLM) flow
# --------------------------------------------------------------------------- #
class AgentCard(BaseModel):
    card_no: int
    product_id: str
    title: str
    image: Optional[str] = None
    price: float
    currency: str
    price_source: str = "simulated"
    availability: str = "in_stock"
    quantity: float = 1
    pack_note: Optional[str] = None
    asked_for: Optional[str] = None


class AgentAudioChunk(BaseModel):
    index: int
    text: str
    audio: Optional[str] = Field(default=None, description="base64 WAV, null when TTS is off")
    error: Optional[str] = None


class AuthRequestCode(BaseModel):
    email: str


class AuthVerify(BaseModel):
    email: str
    code: str


class AuthUser(BaseModel):
    id: str
    email: str
    token: Optional[str] = None


class AuthCodeResponse(BaseModel):
    sent: bool
    channel: str
    dev_code: Optional[str] = None


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: str
    message_count: int = 0
    order_count: int = 0


class SessionDetail(BaseModel):
    id: str
    title: str
    created_at: str
    messages: List[dict] = []
    orders: List[dict] = []


class SttResponse(BaseModel):
    transcript: str
    language_code: Optional[str] = None


class AgentTextRequest(BaseModel):
    text: str
    language: Optional[str] = None
    # Cards from the previous turn, so "book card 2" can be resolved without
    # searching the catalog again. Optional - the server also remembers them
    # per session_id.
    cards: Optional[List["AgentCard"]] = None
    session_id: Optional[str] = None


class AgentResponse(BaseModel):
    transcript: str
    reply: Optional[str] = None
    intent: str = "search"
    order: Optional[OrderResponse] = None
    cards: List[AgentCard] = []
    narration: str
    narration_engine: str
    audio: List[AgentAudioChunk] = []
    language: str
    search_mode: str
    llm_engine: str
    llm_note: Optional[str] = None


class AgentTTSRequest(BaseModel):
    text: str
    language: Optional[str] = None


class AgentTTSResponse(BaseModel):
    chunks: List[AgentAudioChunk]
    language: str


class CaptureResponse(BaseModel):
    order_id: str
    status: str
    capture_id: Optional[str] = None
    payer_email: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    mode: str
