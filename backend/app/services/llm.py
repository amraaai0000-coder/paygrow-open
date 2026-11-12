"""The thinking part.

Two jobs. Work out what the shopper wants, and write what PayGrow should say.
Sarvam's chat model answers first, Groq is the backup, plain rules are the last
resort. A missing key slows it down, it does not stop it.
"""
import json
import re
from typing import List, Optional

import httpx

from ..config import settings
from ..models import ParsedItem
from ..parser import parse_text

TIMEOUT = 30.0

# Sarvam chat models. sarvam-m was retired upstream, so it is only tried first
# in case an older account still has access; sarvam-105b is the current model.
SARVAM_MODEL_CANDIDATES = [
    "sarvam-m",
    "sarvam-105b",
    "sarvam-105b-conversations",
]

# Groq retires models regularly, and an account may not have access to one even
# if it still exists. The configured model is tried first, then these in order,
# and whichever answers is remembered for the rest of the process.
MODEL_CANDIDATES = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "gemma2-9b-it",
]

_working_sarvam_model: str | None = None
_working_model: str | None = None

PLAN_SYSTEM = (
    "You are PayGrow, a voice-first shopping agent for an Indian grocery store. "
    "You understand Hindi, Hinglish and English. "
    "Turn what the shopper said into a shopping plan and reply with JSON only."
)

PLAN_INSTRUCTIONS = (
    "Reply with JSON shaped exactly like this:\n"
    '{"intent": "search|select|repeat|cancel", '
    '"card_no": 1, '
    '"items": [{"name": "as the shopper said it", '
    '"query": "short English catalog search term", '
    '"quantity": 1, "unit": "kg|g|l|ml|packet|piece or null"}], '
    '"reply": "one short sentence acknowledging the request"}\n'
    "Intents:\n"
    "- search: the shopper wants products (a new shopping list).\n"
    "- select: the shopper is picking one of the cards you already read out, "
    "for example 'card number 1 book karo', 'book 2', 'pehla wala', 'doosra'. "
    "Put the card number in card_no and leave items empty.\n"
    "- repeat: the shopper wants to hear the list again ('phir se bolo', 'repeat').\n"
    "- cancel: the shopper wants to stop ('cancel', 'nahi', 'rehne do').\n"
    "Rules: one entry per distinct product. quantity is a number. "
    "Translate Hindi/Hinglish product words into English for `query` "
    "(doodh -> milk, atta -> flour, sabun -> soap). "
    "If no product is mentioned in a search, return an empty items array."
)

NARRATION_SYSTEM = (
    "You are PayGrow, speaking out loud to a shopper in India. "
    "You are given product cards that a catalog search returned. "
    "Read them out as a short spoken answer."
)

NARRATION_INSTRUCTIONS = (
    "Write only the words to be spoken - no markdown, no bullet symbols, no emojis.\n"
    "Include, in this order:\n"
    "1. how many cards you found in total;\n"
    "2. every card, read by its card number, with a SHORT name (a few words, "
    "never the full catalogue title) and its price;\n"
    "3. one short sentence helping the shopper choose;\n"
    "4. the final line, asking which item to book and telling them to say the "
    "card number.\n"
    "Keep it under 120 words. Numbers above 999 should use commas."
)


class LLMError(Exception):
    """Raised when Groq fails in a way worth surfacing."""


def _model_candidates() -> List[str]:
    names: List[str] = []
    if _working_model:
        names.append(_working_model)
    if settings.GROQ_MODEL:
        names.append(settings.GROQ_MODEL)
    names.extend(MODEL_CANDIDATES)

    ordered: List[str] = []
    for name in names:
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def _is_model_not_found(status_code: int, body: str) -> bool:
    if status_code == 404:
        return True
    lowered = (body or "").lower()
    return "model_not_found" in lowered or "does not exist" in lowered


def _sarvam_model_candidates() -> List[str]:
    names: List[str] = []
    if _working_sarvam_model:
        names.append(_working_sarvam_model)
    if settings.SARVAM_LLM_MODEL:
        names.append(settings.SARVAM_LLM_MODEL)
    names.extend(SARVAM_MODEL_CANDIDATES)

    ordered: List[str] = []
    for name in names:
        if name and name not in ordered:
            ordered.append(name)
    return ordered


async def _sarvam_chat(payload: dict) -> str:
    """Sarvam Chat Completions - sarvam-105b (or sarvam-m if still available)."""
    global _working_sarvam_model

    headers = {
        "api-subscription-key": settings.SARVAM_API_KEY,
        "Authorization": f"Bearer {settings.SARVAM_API_KEY}",
        "Content-Type": "application/json",
    }
    tried: List[str] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for model in _sarvam_model_candidates():
            tried.append(model)
            try:
                response = await client.post(
                    f"{settings.SARVAM_LLM_BASE}/v1/chat/completions",
                    json={**payload, "model": model},
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"Could not reach Sarvam: {exc.__class__.__name__}.") from exc

            if _is_model_not_found(response.status_code, response.text):
                continue  # retired or not enabled for this account

            if response.status_code >= 400:
                raise LLMError(
                    f"Sarvam returned HTTP {response.status_code}: {response.text[:200]}"
                )

            data = response.json()
            try:
                content = data["choices"][0]["message"].get("content")
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                raise LLMError("Sarvam returned an unexpected response shape.") from exc

            if not isinstance(content, str) or not content.strip():
                # A reasoning model sometimes puts everything in
                # `reasoning_content` and leaves `content` null. Do not hand a
                # None back to the caller - move on to the next model.
                continue

            _working_sarvam_model = model
            return content

    raise LLMError(
        "No Sarvam chat model was available. Tried: " + ", ".join(tried)
    )


async def _groq_chat(payload: dict) -> str:
    """Call Groq, walking the model list until one of them answers."""
    global _working_model

    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    tried: List[str] = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for model in _model_candidates():
            tried.append(model)
            try:
                response = await client.post(
                    f"{settings.GROQ_BASE}/chat/completions",
                    json={**payload, "model": model},
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"Could not reach Groq: {exc.__class__.__name__}.") from exc

            if _is_model_not_found(response.status_code, response.text):
                continue  # this account cannot use that model - try the next

            if response.status_code >= 400:
                raise LLMError(
                    f"Groq returned HTTP {response.status_code}: {response.text[:200]}"
                )

            data = response.json()
            try:
                content = data["choices"][0]["message"].get("content")
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                raise LLMError("Groq returned an unexpected response shape.") from exc

            if not isinstance(content, str) or not content.strip():
                continue

            _working_model = model
            return content

    raise LLMError(
        "No Groq model was available for this account. Tried: " + ", ".join(tried)
    )


def _provider_order() -> List[str]:
    choice = (settings.LLM_PROVIDER or "sarvam").lower()
    if choice == "groq":
        return ["groq", "sarvam"]
    if choice == "auto":
        return ["sarvam", "groq"]
    return ["sarvam", "groq"]


async def _chat(payload: dict) -> tuple:
    """Ask the configured LLM, falling back to the other provider, then rules.

    Returns (content, engine) so the response can say which model answered.
    """
    problems: List[str] = []

    for provider in _provider_order():
        if provider == "sarvam":
            if not settings.sarvam_enabled:
                problems.append("sarvam: SARVAM_API_KEY is not set")
                continue
            try:
                return await _sarvam_chat(payload), "sarvam"
            except LLMError as exc:
                problems.append(str(exc))
        else:
            if not settings.groq_enabled:
                problems.append("groq: GROQ_API_KEY is not set")
                continue
            try:
                return await _groq_chat(payload), "groq"
            except LLMError as exc:
                problems.append(str(exc))

    raise LLMError(" | ".join(problems) or "No LLM provider is configured.")


# --------------------------------------------------------------------------- #
# Step 1 - plan
# --------------------------------------------------------------------------- #
def _item_from_dict(raw: dict, fallback_text: str) -> Optional[ParsedItem]:
    name = str(raw.get("name") or raw.get("query") or "").strip()
    query = str(raw.get("query") or name).strip()
    if not query:
        return None
    try:
        quantity = float(raw.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 1.0
    unit = raw.get("unit")
    unit = str(unit).strip().lower() if unit and str(unit).lower() != "null" else None
    return ParsedItem(
        raw=name or fallback_text,
        name=name or query,
        query=query,
        quantity=quantity if quantity > 0 else 1.0,
        unit=unit,
    )


def fallback_plan(text: str) -> dict:
    """Rule-based plan, used when Groq is unavailable."""
    return {
        "items": parse_text(text),
        "reply": "",
        "engine": "rules",
        "intent": "search",
        "card_no": None,
    }


_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Speech-to-text often writes numbers as words ("card number four",
# "कार्ड नंबर चार"), so both digits and words are accepted.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "panch": 5,
    "paanch": 5, "chhah": 6, "chah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
}

_SELECT_PATTERNS = [
    r"(?:card|कार्ड)\s*(?:number|नंबर|no\.?|#)?\s*([a-z\u0900-\u097F]+|\d+)",
    r"(?:book|बुक)\s*(?:card\s*)?(?:number\s*|नंबर\s*)?([a-z\u0900-\u097F]+|\d+)",
    r"(\d+)\s*(?:नंबर\s*)?(?:book|बुक)",
]

_REPEAT_WORDS = ("repeat", "again", "dobara", "dubara", "phir se", "फिर से", "दोबारा", "सुना")
_CANCEL_WORDS = ("cancel", "stop", "rehne do", "रहने दो", "रद्द", "band karo", "बंद करो")
# A greeting or a plain question is conversation, not a shopping list.
_CHAT_WORDS = {
    "hi", "hii", "hello", "hey", "namaste", "namaskar", "yo", "thanks",
    "thank", "thx", "ok", "okay", "bye", "goodbye", "help", "salam",
}
_QUESTION_WORDS = {
    "what", "who", "how", "why", "when", "where", "which", "can", "could",
    "is", "are", "kya", "kaise", "kaun", "kab", "kahan", "kitna", "kitne",
    "batao", "bata", "your", "you",
}


# Hindi ordinals people actually say out loud.
_ORDINALS = {
    "pehla": 1, "पहला": 1, "पहले": 1, "first": 1,
    "doosra": 2, "दूसरा": 2, "second": 2,
    "teesra": 3, "तीसरा": 3, "third": 3,
    "chautha": 4, "चौथा": 4, "fourth": 4,
}


def _as_card_number(token: str):
    token = (token or "").strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def detect_intent(text: str) -> dict:
    """Work out whether the shopper wants a search, a selection, a repeat or a stop.

    Runs on the raw sentence, so it still works with no LLM key.
    """
    lowered = (text or "").lower().translate(_DEVANAGARI_DIGITS)

    for pattern in _SELECT_PATTERNS:
        for match in re.finditer(pattern, lowered):
            number = _as_card_number(match.group(1))
            if number is not None:
                return {"intent": "select", "card_no": number}

    for word, number in _ORDINALS.items():
        if word in lowered:
            return {"intent": "select", "card_no": number}

    if any(word in lowered for word in _REPEAT_WORDS):
        return {"intent": "repeat", "card_no": None}

    if any(word in lowered for word in _CANCEL_WORDS):
        return {"intent": "cancel", "card_no": None}

    words = set(re.findall(r"[a-z]+", lowered))
    asks_something = lowered.strip().endswith("?") or bool(words & _QUESTION_WORDS)
    if (words & _CHAT_WORDS) or (asks_something and not re.search(r"\d", lowered)):
        return {"intent": "chat", "card_no": None}

    return {"intent": "search", "card_no": None}


async def plan(text: str) -> dict:
    heuristic = detect_intent(text)

    if not settings.groq_enabled and not settings.sarvam_enabled:
        return {**fallback_plan(text), **heuristic, "note": "No LLM key is set"}

    payload = {
        "messages": [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": f'Shopper said: "{text}"\n\n{PLAN_INSTRUCTIONS}'},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    try:
        content, engine = await _chat(payload)
        if not isinstance(content, str) or not content.strip():
            return {
                **fallback_plan(text),
                **heuristic,
                "note": "the LLM returned an empty answer",
            }
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("the LLM did not return a JSON object")
    except LLMError as exc:
        return {**fallback_plan(text), **heuristic, "note": str(exc)}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {**fallback_plan(text), **heuristic, "note": "the LLM returned invalid JSON"}

    intent = str(parsed.get("intent") or heuristic["intent"]).lower()
    if intent not in ("search", "select", "repeat", "cancel"):
        intent = heuristic["intent"]

    card_no = parsed.get("card_no") or heuristic["card_no"]
    try:
        card_no = int(card_no) if card_no not in (None, "") else None
    except (TypeError, ValueError):
        card_no = heuristic["card_no"]

    # If the model says "select" without a number, the regex may still know it.
    if intent == "select" and card_no is None:
        card_no = heuristic["card_no"]
        if card_no is None:
            intent = heuristic["intent"]

    items: List[ParsedItem] = []
    for raw in parsed.get("items") or []:
        if isinstance(raw, dict):
            item = _item_from_dict(raw, text)
            if item:
                items.append(item)

    if intent == "search" and not items:
        return {**fallback_plan(text), "intent": "search", "card_no": None}

    return {
        "items": items,
        "reply": str(parsed.get("reply") or "").strip(),
        "engine": engine,
        "intent": intent,
        "card_no": card_no,
    }


# --------------------------------------------------------------------------- #
# Step 2 - narrate
# --------------------------------------------------------------------------- #
def _short_title(title: str, limit: int = 42) -> str:
    """Product titles from the catalogue are paragraphs. Read out a few words."""
    text = " ".join((title or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def _currency_symbol(currency: str) -> str:
    return "₹" if currency.upper() == "INR" else "$"


def fallback_narration(cards: List[dict], language: str) -> str:
    """Deterministic spoken summary - same shape the LLM is asked to produce."""
    if not cards:
        if (language or "").startswith("hi"):
            return "मुझे कोई प्रोडक्ट नहीं मिला। कृपया दूसरा नाम बोलिए।"
        return "I could not find any matching products. Please say the item again."

    hindi = (language or "").startswith("hi")
    symbol = _currency_symbol(cards[0].get("currency", "USD"))

    lines: List[str] = []
    if hindi:
        lines.append(f"मैंने आपके लिए कुल {len(cards)} कार्ड ढूँढे हैं।")
        for card in cards:
            lines.append(
                f"कार्ड {card['card_no']}: {_short_title(card['title'])}, "
                f"कीमत {symbol}{card['price']:.2f}।"
            )
        lines.append("आपको कौन सा आइटम बुक करना है? कार्ड नंबर बताइए।")
    else:
        lines.append(f"I found {len(cards)} card(s) for you.")
        for card in cards:
            lines.append(
                f"Card {card['card_no']}: {_short_title(card['title'])}, "
                f"priced at {symbol}{card['price']:.2f}."
            )
        lines.append("Which item would you like to book? Tell me the card number.")
    return " ".join(lines)


async def narrate(cards: List[dict], language: str, reply: str = "") -> dict:
    if not cards:
        return {"text": fallback_narration(cards, language), "engine": "template"}

    if not settings.groq_enabled and not settings.sarvam_enabled:
        return {"text": fallback_narration(cards, language), "engine": "template"}

    language_name = "Hindi (Devanagari script)" if (language or "").startswith("hi") else "English (India)"
    symbol = _currency_symbol(cards[0].get("currency", "USD"))
    card_lines = "\n".join(
        f"Card {card['card_no']}: {_short_title(card['title'], 34)} - "
        f"{symbol}{card['price']:.2f}"
        for card in cards
    )
    user_content = (
        f"Speak in {language_name}.\n"
        f"{NARRATION_INSTRUCTIONS}\n\n"
        f"Cards:\n{card_lines}"
    )
    if reply:
        user_content += f"\n\nYour earlier acknowledgement: {reply}"

    payload = {
        "messages": [
            {"role": "system", "content": NARRATION_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.4,
        "max_tokens": 400,
    }

    try:
        content, engine = await _chat(payload)
    except LLMError:
        return {"text": fallback_narration(cards, language), "engine": "template"}

    content = content.strip() if isinstance(content, str) else ""
    if not content:
        return {"text": fallback_narration(cards, language), "engine": "template"}
    return {"text": content, "engine": engine}


_CHAT_SYSTEM = (
    "You are PayGrow, a friendly grocery shopping assistant for India. "
    "Answer in one or two short sentences, in the shopper's language, plain "
    "spoken words only. Never invent prices. If they are just saying hello, "
    "invite them to say what they need. You cannot place an order in this "
    "message - if they want to buy something, tell them to say the items."
)


def _fallback_chat(language: str) -> str:
    if language.startswith("hi"):
        return (
            "नमस्ते! बताइए क्या चाहिए — जैसे \"दो लीटर दूध और एक ब्रेड\"। "
            "मैं सामान ढूँढकर कार्ड दिखा दूँगा, फिर आप कार्ड नंबर बताकर बुक कर सकते हैं।"
        )
    return (
        "Hi! Tell me what you need - for example \"two litres of milk and a "
        "loaf of bread\". I will find the products, show you cards, and you "
        "pick one by its number."
    )


async def chat_reply(text: str, language: str = "en-IN") -> dict:
    """A plain answer for greetings and questions - no search, no order."""
    if settings.sarvam_enabled or settings.groq_enabled:
        payload = {
            "messages": [
                {"role": "system", "content": _CHAT_SYSTEM},
                {"role": "user", "content": f"Language: {language}\nShopper said: {text}"},
            ],
            "temperature": 0.4,
            "max_tokens": 120,
        }
        try:
            content, engine = await _chat(payload)
            if isinstance(content, str) and content.strip():
                return {"text": content.strip(), "engine": engine}
        except LLMError:
            pass
    return {"text": _fallback_chat(language), "engine": "template"}
