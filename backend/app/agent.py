"""One turn of the conversation.

Audio or text in, cards and narration out. The intent decides what happens:
search the catalogue, book a card, repeat the list, cancel, or just answer.
Only the first two ever create an order.
"""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .config import settings
from .models import (
    AgentAudioChunk,
    AgentCard,
    AgentResponse,
    AgentTTSRequest,
    AgentTTSResponse,
    AgentTextRequest,
    CartLine,
    OrderResponse,
    ParsedItem,
)
from . import db, session
from .services import llm, paypal, sarvam, spoonacular

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger("paygrow.agent")


def _cards_from_resolved(resolved) -> List[AgentCard]:
    cards: List[AgentCard] = []
    for group in resolved:
        for product in group.products:
            cards.append(
                AgentCard(
                    card_no=len(cards) + 1,
                    product_id=product.id,
                    title=product.title,
                    image=product.image,
                    price=product.price,
                    currency=product.currency,
                    price_source=product.price_source,
                    availability=product.availability,
                    quantity=group.cart_quantity,
                    pack_note=group.cart_note,
                    asked_for=group.item.raw,
                )
            )
    return cards


async def _audio_chunks(narration: str, language: str) -> List[AgentAudioChunk]:
    try:
        raw = await sarvam.text_to_speech_chunks(narration, language)
    except sarvam.SarvamError as exc:
        # Never fail the whole request because speech failed - the text is still
        # useful, and the reason is passed along for the UI to show.
        return [
            AgentAudioChunk(
                index=0,
                text=narration,
                audio=None,
                error=str(exc),
            )
        ]
    return [AgentAudioChunk(**chunk) for chunk in raw]


AUTO = {"", "auto", "unknown", "detect", "none"}


async def resolve_language(
    text: str,
    requested: Optional[str] = None,
    detected: Optional[str] = None,
) -> str:
    """Work out which language to answer in - never assume one.

    Order of evidence: an explicit request from the client, then the language
    Sarvam STT heard in the audio, then Sarvam /text-lid on the transcript, then
    a local script check. Only if all of that fails does the configured default
    apply.
    """
    if requested and requested.strip().lower() not in AUTO:
        return requested.strip()

    if detected and detected not in AUTO:
        return sarvam._normalise(detected, settings.SARVAM_TTS_LANGUAGE)

    found = await sarvam.identify_language(text, settings.SARVAM_TTS_LANGUAGE)
    return found["language_code"]


async def _speak(text: str, language: str, enabled: bool = True) -> List[AgentAudioChunk]:
    """Narration audio. The streaming endpoint synthesises chunk by chunk itself,
    so it passes enabled=False instead of paying for a duplicate TTS round."""
    if not enabled:
        return []
    return await _audio_chunks(text, language)


def _pick(cards: List[AgentCard], card_no: Optional[int]) -> Optional[AgentCard]:
    if card_no is None:
        return None
    return next((card for card in cards if card.card_no == card_no), None)


def _line_from_card(card: AgentCard) -> CartLine:
    return CartLine(
        product_id=card.product_id,
        title=card.title,
        image=card.image,
        unit_price=card.price,
        quantity=card.quantity,
    )


async def _select_card(
    text: str,
    card_no: Optional[int],
    cards: List[AgentCard],
    language: str,
    engine: str,
    with_audio: bool = True,
) -> AgentResponse:
    """The shopper picked a card - book it instead of searching again."""
    hindi = language.startswith("hi")

    if not cards:
        spoken = (
            "अभी मेरे पास कोई कार्ड नहीं है। पहले बताइए क्या खरीदना है।"
            if hindi
            else "I do not have any cards yet. Tell me what you would like to buy first."
        )
        return AgentResponse(
            transcript=text, intent="select", cards=[], narration=spoken,
            narration_engine="template", audio=await _speak(spoken, language, with_audio),
            language=language, search_mode=settings.spoonacular_mode, llm_engine=engine,
        )

    if card_no is None:
        spoken = (
            "कौन सा कार्ड बुक करना है? कार्ड नंबर बताइए।"
            if hindi
            else "Which card should I book? Tell me the card number."
        )
        return AgentResponse(
            transcript=text, intent="select", cards=cards, narration=spoken,
            narration_engine="template", audio=await _speak(spoken, language, with_audio),
            language=language, search_mode=settings.spoonacular_mode, llm_engine=engine,
        )

    card = _pick(cards, card_no)
    if card is None:
        spoken = (
            f"कार्ड {card_no} मेरी सूची में नहीं है। कृपया 1 से {len(cards)} के बीच कोई नंबर बताइए।"
            if hindi
            else f"Card {card_no} is not on my list. Please pick a number between 1 and {len(cards)}."
        )
        return AgentResponse(
            transcript=text, intent="select", cards=cards, narration=spoken,
            narration_engine="template", audio=await _speak(spoken, language, with_audio),
            language=language, search_mode=settings.spoonacular_mode, llm_engine=engine,
        )

    try:
        order = await paypal.create_order([_line_from_card(card)])
    except paypal.PayPalError as exc:
        spoken = (
            f"कार्ड {card_no} का ऑर्डर नहीं बन सका। {exc}"
            if hindi
            else f"I could not create the order for card {card_no}. {exc}"
        )
        return AgentResponse(
            transcript=text, intent="select", cards=cards, narration=spoken,
            narration_engine="template", audio=await _speak(spoken, language, with_audio),
            language=language, search_mode=settings.spoonacular_mode, llm_engine=engine,
        )

    symbol = "₹" if card.currency.upper() == "INR" else "$"
    spoken = (
        f"कार्ड {card_no}, {card.title}, कीमत {symbol}{card.price:.2f}। "
        "ऑर्डर बन गया है, PayPal का पेज खुल रहा है। वहाँ अप्रूव कीजिए, फिर ऐप पर capture दबाइए।"
        if hindi
        else f"Card {card_no}, {card.title}, {symbol}{card.price:.2f}. "
        "The order is created and the PayPal page is opening. Approve it there, then press capture."
    )

    return AgentResponse(
        transcript=text,
        intent="select",
        order=OrderResponse(**order),
        cards=cards,
        narration=spoken,
        narration_engine="template",
        audio=await _speak(spoken, language, with_audio),
        language=language,
        search_mode=settings.spoonacular_mode,
        llm_engine=engine,
    )


async def run_agent(
    text: str,
    language: Optional[str] = None,
    detected: Optional[str] = None,
    last_cards: Optional[List[AgentCard]] = None,
    session_id: Optional[str] = None,
    with_audio: bool = True,
) -> AgentResponse:
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Nothing was said or typed.")

    language = await resolve_language(text, language, detected)

    # Cards from this request, or from the session store if the client did not
    # send them (page reload, cached page, older client).
    cards_in = last_cards or []
    if not cards_in:
        remembered = session.recall_cards(session_id) or []
        cards_in = [AgentCard(**card) for card in remembered]

    # 1. LLM (Groq) turns the sentence into items, or into a selection.
    plan = await llm.plan(text)
    intent = plan.get("intent", "search")
    logger.info(
        "agent intent=%s card_no=%s session=%s supplied_cards=%d remembered_cards=%d text=%r",
        intent, plan.get("card_no"), session_id, len(last_cards or []), len(cards_in), text,
    )

    # 2. A selection is about the cards already on screen - never search again.
    if intent == "chat":
        # A greeting or a question is conversation, not a shopping list:
        # answer it, search nothing, create no order.
        reply = await llm.chat_reply(text, language)
        audio = await _speak(reply["text"], language, with_audio)
        db.record_turn(
            session_id or "",
            text,
            reply["text"],
            cards=[],
            meta=f"chat \u00b7 {reply['engine']}",
        )
        return AgentResponse(
            transcript=text,
            reply=reply["text"],
            intent="chat",
            cards=[],
            narration=reply["text"],
            narration_engine=reply["engine"],
            audio=audio,
            language=language,
            llm_engine=reply["engine"],
            search_mode=settings.spoonacular_mode,
        )

    if intent == "select":
        return await _select_card(
            text, plan.get("card_no"), cards_in, language, plan.get("engine", "rules"),
            with_audio=with_audio,
        )

    if intent == "repeat" and cards_in:
        narration = await llm.narrate([card.model_dump() for card in cards_in], language)
        return AgentResponse(
            transcript=text, intent="repeat", cards=cards_in,
            narration=narration["text"], narration_engine=narration["engine"],
            audio=await _speak(narration["text"], language, with_audio), language=language,
            search_mode=settings.spoonacular_mode, llm_engine=plan.get("engine", "rules"),
        )

    if intent == "cancel":
        spoken = "ठीक है, रद्द कर दिया।" if language.startswith("hi") else "Okay, cancelled."
        return AgentResponse(
            transcript=text, intent="cancel", cards=cards_in, narration=spoken,
            narration_engine="template", audio=await _speak(spoken, language, with_audio),
            language=language, search_mode=settings.spoonacular_mode,
            llm_engine=plan.get("engine", "rules"), llm_note=plan.get("note"),
        )

    # 3. Otherwise: a normal shopping search.
    items: List[ParsedItem] = plan["items"]
    if not items:
        raise HTTPException(
            status_code=422,
            detail="No product was understood. Try: 'mujhe do kilo doodh aur ek bread chahiye'.",
        )

    # 2. Spoonacular resolves them into real products.
    try:
        resolved, search_mode = await spoonacular.resolve_items(
            items, number=settings.AGENT_CARDS_PER_ITEM
        )
    except spoonacular.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    cards = _cards_from_resolved(resolved)
    session.remember_cards(session_id, [card.model_dump() for card in cards])

    # 3. The LLM writes what PayGrow should say out loud.
    narration = await llm.narrate(
        [card.model_dump() for card in cards], language, plan.get("reply") or ""
    )

    # 4. Sarvam turns that narration into speech, chunked for playback.
    audio = await _speak(narration["text"], language, with_audio)

    db.record_turn(
        session_id or "",
        text,
        narration["text"],
        cards=[card.model_dump() for card in cards],
        meta=f"search \u00b7 {narration['engine']}",
    )

    return AgentResponse(
        transcript=text,
        reply=plan.get("reply") or None,
        intent="search",
        cards=cards,
        narration=narration["text"],
        narration_engine=narration["engine"],
        audio=audio,
        language=language,
        search_mode=search_mode,
        llm_engine=plan.get("engine", "rules"),
        llm_note=plan.get("note"),
    )


@router.post("/understand", response_model=AgentResponse)
async def understand(request: AgentTextRequest):
    """Text in, cards + narration + audio out (or a booked order)."""
    return await run_agent(
        request.text,
        request.language,
        last_cards=request.cards,
        session_id=request.session_id,
    )


@router.post("/voice", response_model=AgentResponse)
async def voice(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    cards: Optional[str] = Form(default=None),
    session_id: Optional[str] = Form(default=None),
):
    """Voice in (Sarvam Saaras), cards + narration + audio out."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="The recording was empty.")

    filename = file.filename or "voice.webm"
    # language=None lets Saaras auto-detect from the audio itself.
    requested = language if language and language.strip().lower() not in AUTO else None
    try:
        transcription = await sarvam.speech_to_text(audio_bytes, filename, language_code=requested)
    except sarvam.SarvamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    transcript = transcription.get("transcript") or ""
    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="Sarvam could not make out any words. Please say it again a little slower.",
        )

    previous: Optional[List[AgentCard]] = None
    if cards:
        try:
            parsed_cards = json.loads(cards)
            previous = [AgentCard(**card) for card in parsed_cards]
        except (json.JSONDecodeError, TypeError, ValueError):
            previous = None

    return await run_agent(
        transcript,
        requested,
        detected=transcription.get("language_code"),
        last_cards=previous,
        session_id=session_id,
    )


@router.post("/tts", response_model=AgentTTSResponse)
async def tts(request: AgentTTSRequest):
    """Speak any text (used for 'say that again' and for follow-up questions)."""
    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Nothing to speak.")
    language = request.language or settings.SARVAM_TTS_LANGUAGE
    return AgentTTSResponse(chunks=await _audio_chunks(text, language), language=language)
