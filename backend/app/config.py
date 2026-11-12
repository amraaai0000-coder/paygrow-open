"""Settings, read from the environment.

Nothing is hardcoded here, so the same code runs on a laptop and on Render.
A .env file is picked up in development. On Render the values come from the
dashboard, which is also why every key has a safe default.
"""
import os
from pathlib import Path

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


class Settings:
    # --- Spoonacular -------------------------------------------------------
    SPOONACULAR_API_KEY = _env("SPOONACULAR_API_KEY")

    # Spoonacular's free plan allows 150 points/day and every product search
    # costs a point, so identical queries are cached for this long.
    SPOONACULAR_CACHE_TTL_SECONDS = int(_env("SPOONACULAR_CACHE_TTL_SECONDS", "3600") or 3600)

    # When no key is set the app serves a small built-in catalog so the UI can
    # be built offline. Set ALLOW_MOCK=false to force real API calls only, so
    # nothing fake can ever appear in front of the judges.
    ALLOW_MOCK = _env("ALLOW_MOCK", "true").lower() not in ("0", "false", "no", "off")

    # --- PayPal (sandbox by default; never point at live during a hackathon)
    PAYPAL_CLIENT_ID = _env("PAYPAL_CLIENT_ID")
    PAYPAL_CLIENT_SECRET = _env("PAYPAL_CLIENT_SECRET")
    PAYPAL_BASE = _env("PAYPAL_BASE", "https://api-m.sandbox.paypal.com")

    # --- Sarvam AI: speech in (Saaras) + speech out (Bulbul) ----------------
    SARVAM_API_KEY = _env("SARVAM_API_KEY")
    SARVAM_STT_MODEL = _env("SARVAM_STT_MODEL", "saaras:v4")
    # transcribe | translate | codemix | verbatim | translit
    SARVAM_STT_MODE = _env("SARVAM_STT_MODE", "codemix")
    SARVAM_STT_LANGUAGE = _env("SARVAM_STT_LANGUAGE", "unknown")
    SARVAM_TTS_MODEL = _env("SARVAM_TTS_MODEL", "bulbul:v3")
    SARVAM_TTS_SPEAKER = _env("SARVAM_TTS_SPEAKER", "shubh")
    # Only a last resort: the language normally comes from the audio (STT) or
    # from Sarvam /text-lid on the transcript. Send language="auto" to the API
    # to let it decide.
    SARVAM_TTS_LANGUAGE = _env("SARVAM_TTS_LANGUAGE", "hi-IN")
    SARVAM_TTS_PACE = float(_env("SARVAM_TTS_PACE", "1.0") or 1.0)

    # --- Sarvam LLM (Chat Completions, OpenAI-compatible) ------------------
    SARVAM_LLM_BASE = _env("SARVAM_LLM_BASE", "https://api.sarvam.ai")
    # sarvam-m was retired by Sarvam; sarvam-105b is the current flagship.
    # Both are tried, in this order, whichever answers first.
    SARVAM_LLM_MODEL = _env("SARVAM_LLM_MODEL", "sarvam-105b")
    # Which LLM to think with: sarvam | groq | auto (sarvam first, then groq)
    LLM_PROVIDER = _env("LLM_PROVIDER", "sarvam").lower()

    # --- Groq: Llama 3.1 8B instant ---------------------------------------
    GROQ_API_KEY = _env("GROQ_API_KEY")
    GROQ_BASE = _env("GROQ_BASE", "https://api.groq.com/openai/v1")
    GROQ_MODEL = _env("GROQ_MODEL", "llama-3.1-8b-instant")

    # --- Agent behaviour --------------------------------------------------
    # Spoken narration is split into chunks this long before each TTS call.
    TTS_CHUNK_CHARS = int(_env("TTS_CHUNK_CHARS", "800") or 800)
    # How many product cards to offer per requested item.
    AGENT_CARDS_PER_ITEM = int(_env("AGENT_CARDS_PER_ITEM", "2") or 2)

    # --- Money -------------------------------------------------------------
    CURRENCY = _env("CURRENCY", "USD").upper()

    # --- Redirect targets shown by PayPal after approval --------------------
    # Must be absolute http(s) URLs - PayPal rejects custom schemes such as
    # speakcart://. Point these at your deployed domain, or at a page your
    # Flutter WebView intercepts.
    RETURN_URL = _env("RETURN_URL", "https://example.com/paypal/return")
    CANCEL_URL = _env("CANCEL_URL", "https://example.com/paypal/cancel")
    BRAND_NAME = _env("BRAND_NAME", "SpeakCart")

    @property
    def spoonacular_enabled(self) -> bool:
        return bool(self.SPOONACULAR_API_KEY)

    @property
    def sarvam_enabled(self) -> bool:
        return bool(self.SARVAM_API_KEY)

    @property
    def groq_enabled(self) -> bool:
        return bool(self.GROQ_API_KEY)

    @property
    def voice_mode(self) -> str:
        return "live" if self.sarvam_enabled else "off"

    @property
    def llm_mode(self) -> str:
        return "live" if self.groq_enabled else "rules"

    @property
    def paypal_enabled(self) -> bool:
        return bool(self.PAYPAL_CLIENT_ID and self.PAYPAL_CLIENT_SECRET)

    @property
    def spoonacular_mode(self) -> str:
        if self.spoonacular_enabled:
            return "live"
        return "mock" if self.ALLOW_MOCK else "disabled"

    @property
    def paypal_mode(self) -> str:
        return "live" if self.paypal_enabled else "mock"


settings = Settings()
