"""Agent-loop tests: chunking, fallbacks, and the live Sarvam/Groq paths.

Runs with no API keys (everything falls back), plus fake-httpx tests for the
branches that only execute when the keys are present.
"""
import asyncio
import os

os.environ.pop("SPOONACULAR_API_KEY", None)
os.environ.pop("PAYPAL_CLIENT_ID", None)
os.environ.pop("PAYPAL_CLIENT_SECRET", None)
os.environ.pop("SARVAM_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import llm, sarvam  # noqa: E402

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Fake transport (same shape as tests/test_live_paths.py)
# --------------------------------------------------------------------------- #
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
    def __init__(self, routes):
        self.routes = routes

    def _match(self, url):
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

    async def post(self, url, json=None, data=None, headers=None, files=None, **kwargs):
        return self._match(url)


class _Shim:
    def __init__(self, fake):
        self.AsyncClient = lambda **kwargs: fake

    def __getattr__(self, item):
        return getattr(httpx, item)


def patch_httpx(monkeypatch, module, routes):
    monkeypatch.setattr(module, "httpx", _Shim(FakeClient(routes)))


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
def test_chunk_text_keeps_short_text_whole():
    assert sarvam.chunk_text("hello there", 800) == ["hello there"]


def test_chunk_text_splits_on_sentence_boundaries_within_limit():
    text = "One sentence here. Two sentence here. Three sentence here."
    chunks = sarvam.chunk_text(text, 40)
    assert len(chunks) > 1
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert " ".join(chunks).replace("  ", " ") == text


def test_chunk_text_hard_splits_one_long_sentence():
    text = "x" * 25
    chunks = sarvam.chunk_text(text, 10)
    assert [len(c) for c in chunks] == [10, 10, 5]


def test_chunk_text_handles_devanagari_danda():
    text = "पहला वाक्य। दूसरा वाक्य। तीसरा वाक्य।"
    chunks = sarvam.chunk_text(text, 30)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 30 for chunk in chunks)


def test_chunk_text_empty():
    assert sarvam.chunk_text("", 800) == []
    assert sarvam.chunk_text(None, 800) == []


# --------------------------------------------------------------------------- #
# Narration fallback
# --------------------------------------------------------------------------- #
CARDS = [
    {"card_no": 1, "title": "Milk 1 L", "price": 1.29, "currency": "USD", "availability": "in_stock"},
    {"card_no": 2, "title": "Bread 400 g", "price": 2.49, "currency": "USD", "availability": "in_stock"},
]


def test_fallback_narration_lists_every_card_and_asks_for_a_number():
    text = llm.fallback_narration(CARDS, "en-IN")
    assert "2 card" in text
    assert "Card 1" in text and "Card 2" in text
    assert "Milk 1 L" in text and "Bread 400 g" in text
    assert text.rstrip().endswith("Tell me the card number.")


def test_fallback_narration_hindi():
    text = llm.fallback_narration(CARDS, "hi-IN")
    assert "2 कार्ड" in text
    assert "कार्ड 1" in text and "कार्ड 2" in text
    assert "कार्ड नंबर बताइए" in text


def test_fallback_narration_without_cards():
    assert "could not find" in llm.fallback_narration([], "en-IN").lower()


# --------------------------------------------------------------------------- #
# Agent endpoints with no keys (fallback mode)
# --------------------------------------------------------------------------- #
def test_understand_without_keys_still_returns_cards_and_narration():
    response = client.post("/api/agent/understand", json={"text": "2 litre doodh aur ek bread", "language": "hi-IN"})
    assert response.status_code == 200
    body = response.json()

    assert body["llm_engine"] == "rules"
    assert body["narration_engine"] == "template"
    assert body["cards"], "expected product cards"

    numbers = [card["card_no"] for card in body["cards"]]
    assert numbers == list(range(1, len(numbers) + 1)), "cards must be numbered 1..N"

    assert "कार्ड" in body["narration"]
    assert body["audio"] and body["audio"][0]["text"]
    assert body["audio"][0]["audio"] is None, "no Sarvam key -> text only"


def test_understand_rejects_empty_text():
    assert client.post("/api/agent/understand", json={"text": "   "}).status_code == 422


def test_understand_rejects_sentence_without_products():
    assert client.post("/api/agent/understand", json={"text": "please"}).status_code == 422


def test_tts_without_key_returns_text_chunks():
    body = client.post("/api/agent/tts", json={"text": "नमस्ते। आप कैसे हैं?", "language": "hi-IN"}).json()
    assert body["chunks"]
    assert body["chunks"][0]["audio"] is None
    assert body["language"] == "hi-IN"


def test_voice_without_key_explains_the_missing_key():
    files = {"file": ("voice.webm", b"\x1aE\xdf\xa3fake-webm-bytes", "audio/webm")}
    response = client.post("/api/agent/voice", files=files, data={"language": "hi-IN"})
    assert response.status_code == 502
    assert "SARVAM_API_KEY" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Live Sarvam paths (fake transport)
# --------------------------------------------------------------------------- #
def test_live_tts_returns_base64_audio(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    patch_httpx(monkeypatch, sarvam, [("/text-to-speech", 200, {"audios": ["UklGRiQAAABXQVZF"]})])

    chunks = asyncio.run(sarvam.text_to_speech_chunks("नमस्ते, यह एक टेस्ट है।", "hi-IN"))
    assert len(chunks) == 1
    assert chunks[0]["audio"] == "UklGRiQAAABXQVZF"
    assert chunks[0]["error"] is None


def test_live_tts_failure_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    patch_httpx(monkeypatch, sarvam, [("/text-to-speech", 422, {"message": "invalid speaker"})])

    with pytest.raises(sarvam.SarvamError) as excinfo:
        asyncio.run(sarvam.text_to_speech_chunks("test", "hi-IN"))
    assert "422" in str(excinfo.value)


def test_live_stt_returns_transcript(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    patch_httpx(
        monkeypatch,
        sarvam,
        [("/speech-to-text", 200, {"transcript": "मुझे दो किलो आलू चाहिए", "language_code": "hi-IN"})],
    )

    result = asyncio.run(sarvam.speech_to_text(b"fake-bytes", "voice.webm"))
    assert result["transcript"] == "मुझे दो किलो आलू चाहिए"
    assert result["language_code"] == "hi-IN"


# --------------------------------------------------------------------------- #
# Live Groq paths (fake transport)
# --------------------------------------------------------------------------- #
def _groq_reply(content):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}}
        ]
    }


def test_live_plan_parses_groq_json(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    content = (
        '{"items": [{"name": "doodh", "query": "milk", "quantity": 2, "unit": "l"}],'
        ' "reply": "Adding milk."}'
    )
    patch_httpx(monkeypatch, llm, [("/chat/completions", 200, _groq_reply(content))])

    plan = asyncio.run(llm.plan("mujhe 2 litre doodh chahiye"))
    assert plan["engine"] == "groq"
    assert plan["items"][0].query == "milk"
    assert plan["items"][0].quantity == 2
    assert plan["items"][0].unit == "l"
    assert plan["reply"] == "Adding milk."


def test_live_plan_falls_back_on_broken_json(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    patch_httpx(monkeypatch, llm, [("/chat/completions", 200, _groq_reply("not json at all"))])

    plan = asyncio.run(llm.plan("2 litre milk"))
    assert plan["engine"] == "rules"
    assert plan["items"][0].query == "milk"


def test_live_narration_uses_groq_text(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    patch_httpx(
        monkeypatch,
        llm,
        [("/chat/completions", 200, _groq_reply("मैंने दो कार्ड ढूँढे हैं। कार्ड नंबर बताइए।"))],
    )

    result = asyncio.run(llm.narrate(CARDS, "hi-IN"))
    assert result["engine"] == "groq"
    assert "कार्ड नंबर" in result["text"]


def test_live_narration_falls_back_on_groq_error(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    patch_httpx(monkeypatch, llm, [("/chat/completions", 500, {"error": "boom"})])

    result = asyncio.run(llm.narrate(CARDS, "en-IN"))
    assert result["engine"] == "template"
    assert "Card 1" in result["text"]


# --------------------------------------------------------------------------- #
# Language detection - the language is detected, never hardcoded
# --------------------------------------------------------------------------- #
def test_detect_language_from_devanagari():
    assert sarvam.detect_language_local("मुझे दो किलो आलू चाहिए") == "hi-IN"


def test_detect_language_from_tamil_script():
    assert sarvam.detect_language_local("எனக்கு இரண்டு லிட்டர் பால் வேண்டும்") == "ta-IN"


def test_detect_language_from_telugu_script():
    assert sarvam.detect_language_local("నాకు రెండు లీటర్ల పాలు కావాలి") == "te-IN"


def test_detect_language_from_hinglish():
    assert sarvam.detect_language_local("mujhe do kilo doodh chahiye") == "hi-IN"


def test_detect_language_plain_english():
    assert sarvam.detect_language_local("two litres of milk please") == "en-IN"


def test_identify_language_uses_sarvam_when_available(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    patch_httpx(monkeypatch, sarvam, [("/text-lid", 200, {"language_code": "ta-IN", "script_code": "Taml"})])

    found = asyncio.run(sarvam.identify_language("some text"))
    assert found == {"language_code": "ta-IN", "source": "sarvam"}


def test_identify_language_falls_back_to_local_script_check(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "")
    found = asyncio.run(sarvam.identify_language("मुझे दूध चाहिए"))
    assert found["language_code"] == "hi-IN"
    assert found["source"] == "local"


def test_identify_language_ignores_unsupported_code(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    patch_httpx(monkeypatch, sarvam, [("/text-lid", 200, {"language_code": "ur-IN"})])

    found = asyncio.run(sarvam.identify_language("میرا نام سورو ہے"))
    # ur-IN is not a Bulbul voice, so it must not be passed straight through
    assert found["language_code"] in sarvam.TTS_LANGUAGES


def test_understand_auto_detects_hindi(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "")
    body = client.post(
        "/api/agent/understand",
        json={"text": "मुझे दो किलो आलू और एक ब्रेड चाहिए", "language": "auto"},
    ).json()
    assert body["language"] == "hi-IN"
    assert "कार्ड" in body["narration"]


def test_understand_auto_detects_english(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "")
    body = client.post(
        "/api/agent/understand",
        json={"text": "I need two litres of milk", "language": "auto"},
    ).json()
    assert body["language"] == "en-IN"
    assert "Card 1" in body["narration"]


def test_voice_uses_the_language_sarvam_heard(monkeypatch):
    """The audio says Tamil; nothing in the request mentions Tamil."""
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    patch_httpx(
        monkeypatch,
        sarvam,
        [
            ("/speech-to-text", 200, {"transcript": "எனக்கு பால் வேண்டும்", "language_code": "ta-IN"}),
            ("/text-to-speech", 200, {"audios": ["UklGRiQAAABXQVZF"]}),
        ],
    )

    files = {"file": ("voice.webm", b"fake-webm-bytes", "audio/webm")}
    response = client.post("/api/agent/voice", files=files, data={"language": "auto"})
    assert response.status_code == 200

    body = response.json()
    assert body["language"] == "ta-IN"
    assert body["transcript"] == "எனக்கு பால் வேண்டும்"
    assert body["audio"][0]["audio"] == "UklGRiQAAABXQVZF"


# --------------------------------------------------------------------------- #
# Intent routing - "book card 1" must book, not search again
# --------------------------------------------------------------------------- #
def test_detect_intent_card_number_english():
    assert llm.detect_intent("card number 1 book karo") == {"intent": "select", "card_no": 1}


def test_detect_intent_book_short():
    assert llm.detect_intent("book 2") == {"intent": "select", "card_no": 2}


def test_detect_intent_devanagari_card():
    assert llm.detect_intent("कार्ड नंबर 3 बुक करो") == {"intent": "select", "card_no": 3}


def test_detect_intent_ordinal():
    assert llm.detect_intent("pehla wala book karo") == {"intent": "select", "card_no": 1}


def test_detect_intent_repeat():
    assert llm.detect_intent("phir se bolo")["intent"] == "repeat"
    assert llm.detect_intent("फिर से सुनाओ")["intent"] == "repeat"


def test_detect_intent_cancel():
    assert llm.detect_intent("cancel karo")["intent"] == "cancel"


def test_detect_intent_search_stays_search():
    assert llm.detect_intent("mujhe do kilo doodh chahiye")["intent"] == "search"


CARDS_PAYLOAD = [
    {
        "card_no": 1,
        "product_id": "mock-milk",
        "title": "Amul Taaza Toned Milk 1 L",
        "price": 1.29,
        "currency": "USD",
        "quantity": 2,
        "availability": "in_stock",
        "price_source": "simulated",
    },
    {
        "card_no": 2,
        "product_id": "mock-bread",
        "title": "Whole Wheat Sandwich Bread 400 g",
        "price": 2.49,
        "currency": "USD",
        "quantity": 1,
        "availability": "in_stock",
        "price_source": "simulated",
    },
]


def test_select_card_creates_an_order_instead_of_searching():
    body = client.post(
        "/api/agent/understand",
        json={"text": "card number 1 book karo", "language": "hi-IN", "cards": CARDS_PAYLOAD},
    ).json()

    assert body["intent"] == "select"
    assert body["order"] is not None
    assert body["order"]["order_id"].startswith("MOCK-")
    assert body["order"]["amount"] == 2.58  # 1.29 x 2
    assert "कार्ड 1" in body["narration"]
    # the cards are echoed back, not replaced by a fresh search
    assert [c["card_no"] for c in body["cards"]] == [1, 2]


def test_select_unknown_card_number_asks_again():
    body = client.post(
        "/api/agent/understand",
        json={"text": "book 9", "language": "hi-IN", "cards": CARDS_PAYLOAD},
    ).json()

    assert body["intent"] == "select"
    assert body["order"] is None
    assert "9" in body["narration"]


def test_select_without_cards_asks_which_card():
    body = client.post(
        "/api/agent/understand",
        json={"text": "card number 1", "language": "en-IN"},
    ).json()

    assert body["intent"] == "select"
    assert body["order"] is None
    assert "do not have any cards" in body["narration"].lower()


def test_repeat_reads_the_same_cards_again():
    body = client.post(
        "/api/agent/understand",
        json={"text": "phir se bolo", "language": "hi-IN", "cards": CARDS_PAYLOAD},
    ).json()

    assert body["intent"] == "repeat"
    assert body["order"] is None
    assert "कार्ड 1" in body["narration"] and "कार्ड 2" in body["narration"]


def test_cancel_intent():
    body = client.post(
        "/api/agent/understand",
        json={"text": "cancel karo", "language": "hi-IN", "cards": CARDS_PAYLOAD},
    ).json()

    assert body["intent"] == "cancel"
    assert body["order"] is None
    assert body["narration"]


def test_plain_search_still_searches():
    body = client.post(
        "/api/agent/understand",
        json={"text": "mujhe ek bread chahiye", "language": "hi-IN"},
    ).json()

    assert body["intent"] == "search"
    assert body["order"] is None
    assert body["cards"]


# --------------------------------------------------------------------------- #
# Speech-to-text writes numbers as words - handle that too
# --------------------------------------------------------------------------- #
def test_detect_intent_hindi_word_number():
    assert llm.detect_intent("कार्ड नंबर चार बुक करो") == {"intent": "select", "card_no": 4}


def test_detect_intent_english_word_number():
    assert llm.detect_intent("card number four book karo") == {"intent": "select", "card_no": 4}


def test_detect_intent_hinglish_word_number():
    assert llm.detect_intent("card number do book karo") == {"intent": "select", "card_no": 2}


# --------------------------------------------------------------------------- #
# Session memory: "book 4" works even when the client sends no cards
# --------------------------------------------------------------------------- #
def test_selection_uses_remembered_cards_when_client_sends_none():
    search = client.post(
        "/api/agent/understand",
        json={"text": "2 litre doodh aur ek bread", "language": "hi-IN", "session_id": "session-a"},
    ).json()
    assert search["intent"] == "search"
    assert len(search["cards"]) >= 2

    picked = client.post(
        "/api/agent/understand",
        json={"text": "card number 2 book karo", "language": "hi-IN", "session_id": "session-a"},
    ).json()

    assert picked["intent"] == "select"
    assert picked["order"] is not None
    assert picked["order"]["order_id"].startswith("MOCK-")
    expected = search["cards"][1]["price"] * search["cards"][1]["quantity"]
    assert abs(picked["order"]["amount"] - round(expected, 2)) < 0.01


def test_selection_without_cards_or_session_asks_for_a_list():
    body = client.post(
        "/api/agent/understand",
        json={"text": "book 3", "language": "hi-IN", "session_id": "never-seen"},
    ).json()
    assert body["intent"] == "select"
    assert body["order"] is None
    assert body["narration"]


def test_llm_note_explains_the_fallback():
    body = client.post(
        "/api/agent/understand",
        json={"text": "ek bread chahiye", "language": "hi-IN"},
    ).json()
    assert body["llm_engine"] == "rules"
    assert body["llm_note"] and "No LLM key" in body["llm_note"]


# --------------------------------------------------------------------------- #
# Groq model fallback - a retired model must not break the agent
# --------------------------------------------------------------------------- #
class SequencedClient:
    """Returns the queued responses in order, whatever the URL."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, data=None, headers=None, files=None, **kwargs):
        self.calls.append((json or {}).get("model"))
        status, payload = self.sequence.pop(0) if self.sequence else (500, {"error": "exhausted"})
        return FakeResponse(url, status, payload)

    async def get(self, url, params=None, **kwargs):
        return FakeResponse(url, 200, {})


def test_groq_falls_back_to_another_model_when_one_is_missing(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_working_model", None, raising=False)

    missing = (404, {"error": {"message": "The model does not exist", "code": "model_not_found"}})
    ok = (200, _groq_reply('{"intent": "search", "items": [{"name": "ketchup", "query": "ketchup", '
                           '"quantity": 1, "unit": null}], "reply": "ok"}'))
    fake = SequencedClient([missing, ok])
    monkeypatch.setattr(llm, "httpx", _Shim(fake))

    plan = asyncio.run(llm.plan("I want ketchup"))
    assert plan["engine"] == "groq"
    assert plan["items"][0].query == "ketchup"
    assert len(fake.calls) == 2, "the first model should have been skipped"
    assert fake.calls[0] != fake.calls[1]


def test_groq_reports_when_no_model_is_available(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_working_model", None, raising=False)

    missing = (404, {"error": {"code": "model_not_found", "message": "nope"}})
    fake = SequencedClient([missing] * 10)
    monkeypatch.setattr(llm, "httpx", _Shim(fake))

    plan = asyncio.run(llm.plan("I want ketchup and milk"))
    assert plan["engine"] == "rules"
    assert "Groq" in (plan.get("note") or "") or "model" in (plan.get("note") or "").lower()


# --------------------------------------------------------------------------- #
# Sarvam chat models as the primary LLM
# --------------------------------------------------------------------------- #
def test_sarvam_chat_is_used_when_a_sarvam_key_exists(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "sarvam")
    monkeypatch.setattr(llm, "_working_sarvam_model", None, raising=False)

    content = (
        '{"intent": "search", "items": [{"name": "ketchup", "query": "ketchup", '
        '"quantity": 1, "unit": null}], "reply": "ok"}'
    )
    patch_httpx(monkeypatch, llm, [("/v1/chat/completions", 200, _groq_reply(content))])

    plan = asyncio.run(llm.plan("I want ketchup"))
    assert plan["engine"] == "sarvam"
    assert plan["items"][0].query == "ketchup"


def test_sarvam_missing_model_falls_through_to_the_next_one(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "sarvam")
    monkeypatch.setattr(llm, "_working_sarvam_model", None, raising=False)

    retired = (404, {"error": {"code": "model_not_found", "message": "sarvam-m is retired"}})
    ok = (
        200,
        _groq_reply(
            '{"intent": "search", "items": [{"name": "milk", "query": "milk", '
            '"quantity": 1, "unit": "l"}], "reply": "hi"}'
        ),
    )
    fake = SequencedClient([retired, ok])
    monkeypatch.setattr(llm, "httpx", _Shim(fake))

    plan = asyncio.run(llm.plan("I want milk"))
    assert plan["engine"] == "sarvam"
    assert len(fake.calls) == 2, "the retired model should be skipped"
    assert fake.calls[0] != fake.calls[1]


def test_sarvam_failure_falls_back_to_groq(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "sarvam")
    monkeypatch.setattr(llm, "_working_sarvam_model", None, raising=False)
    monkeypatch.setattr(llm, "_working_model", None, raising=False)

    class TwoHosts:
        """Sarvam 500s, Groq answers."""

        def __init__(self):
            self.hosts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None, data=None, headers=None, files=None, **kwargs):
            self.hosts.append(url)
            if "sarvam.ai" in url:
                return FakeResponse(url, 500, {"error": {"message": "boom"}})
            return FakeResponse(
                url,
                200,
                _groq_reply(
                    '{"intent": "search", "items": [{"name": "milk", "query": "milk", '
                    '"quantity": 1, "unit": "l"}], "reply": "hi"}'
                ),
            )

    fake = TwoHosts()
    monkeypatch.setattr(llm, "httpx", _Shim(fake))

    plan = asyncio.run(llm.plan("I want milk"))
    assert plan["engine"] == "groq"
    assert any("sarvam.ai" in host for host in fake.hosts)
    assert any("groq.com" in host for host in fake.hosts)


def test_narration_reports_which_engine_spoke(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "sarvam")
    patch_httpx(
        monkeypatch,
        llm,
        [("/v1/chat/completions", 200, _groq_reply("मैंने दो कार्ड ढूँढे हैं। कार्ड नंबर बताइए।"))],
    )

    result = asyncio.run(llm.narrate(CARDS, "hi-IN"))
    assert result["engine"] == "sarvam"
    assert "कार्ड नंबर" in result["text"]


# --------------------------------------------------------------------------- #
# Search cache: repeated demo sentences must not burn Spoonacular quota
# --------------------------------------------------------------------------- #
def test_repeated_search_hits_spoonacular_only_once(monkeypatch):
    from app.services import spoonacular

    monkeypatch.setattr(settings, "SPOONACULAR_API_KEY", "test-key")
    spoonacular._search_cache.clear()

    class Counting:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, params=None, **kwargs):
            self.calls += 1
            return FakeResponse(
                url, 200, {"products": [{"id": 7, "title": "Milk 1 L", "imageType": "jpg"}]}
            )

        async def post(self, url, json=None, data=None, headers=None, files=None, **kwargs):
            raise AssertionError("unexpected POST")

    fake = Counting()
    monkeypatch.setattr(spoonacular, "httpx", _Shim(fake))

    first, mode_one = asyncio.run(spoonacular.search_products("milk", 2))
    second, mode_two = asyncio.run(spoonacular.search_products("MILK ", 2))

    assert fake.calls == 1, "the second search should have been served from cache"
    assert mode_one == "live"
    assert mode_two == "live-cached"
    assert first[0].id == second[0].id


# --------------------------------------------------------------------------- #
# A reasoning model can answer with content: null - that must never 500
# --------------------------------------------------------------------------- #
def _null_content_reply():
    return {"choices": [{"message": {"role": "assistant", "content": None}}]}


def test_null_llm_content_does_not_crash_the_turn(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "sarvam")
    monkeypatch.setattr(llm, "_working_sarvam_model", None, raising=False)
    patch_httpx(monkeypatch, llm, [("/v1/chat/completions", 200, _null_content_reply())])

    plan = asyncio.run(llm.plan("i want coffe powder and tea"))
    assert plan["engine"] == "rules"
    assert plan["note"]


def test_null_llm_content_still_answers_the_endpoint():
    """The exact request that returned HTTP 500 in production."""
    response = client.post(
        "/api/agent/understand",
        json={"text": "i want coffe powder and tea", "language": "auto"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cards"]
    assert body["narration"]


def test_null_narration_content_falls_back_to_the_template(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "sarvam")
    monkeypatch.setattr(llm, "_working_sarvam_model", None, raising=False)
    patch_httpx(monkeypatch, llm, [("/v1/chat/completions", 200, _null_content_reply())])

    result = asyncio.run(llm.narrate(CARDS, "en-IN"))
    assert result["engine"] == "template"
    assert "Card 1" in result["text"]
