"""Sarvam for speech in and speech out.

Saaras transcribes, Bulbul speaks, and a third endpoint names the language of a
piece of text. Their REST limits are the reason for the chunking here: 30
seconds of audio per request, 2500 characters of text per request.
"""
import re
from typing import List, Optional

import httpx

from ..config import settings

STT_URL = "https://api.sarvam.ai/speech-to-text"
TTS_URL = "https://api.sarvam.ai/text-to-speech"
TIMEOUT = 60.0

# Sentence ends for both Devanagari (danda) and Latin scripts.
_SENTENCE_END = re.compile(r"(?<=[।.!?\n])\s+")


class SarvamError(Exception):
    """Raised when Sarvam fails; the API layer explains it to the client."""


def _headers() -> dict:
    return {"api-subscription-key": settings.SARVAM_API_KEY}


def chunk_text(text: str, size: int) -> List[str]:
    """Split narration on sentence boundaries, never exceeding `size` chars."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: List[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        piece = sentence.strip()
        if not piece:
            continue
        if len(current) + len(piece) + 1 <= size:
            current = f"{current} {piece}".strip()
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(piece) > size:  # one very long sentence
            chunks.append(piece[:size])
            piece = piece[size:]
        current = piece
    if current:
        chunks.append(current)
    return chunks


async def speech_to_text(
    audio: bytes,
    filename: str = "voice.webm",
    language_code: Optional[str] = None,
    mode: Optional[str] = None,
) -> dict:
    """Transcribe a short voice clip. Returns {transcript, language_code}."""
    if not settings.sarvam_enabled:
        raise SarvamError(
            "SARVAM_API_KEY is not set, so voice input is unavailable. Type the request instead."
        )
    if not audio:
        raise SarvamError("No audio was uploaded.")

    data = {
        "model": settings.SARVAM_STT_MODEL,
        "language_code": language_code or settings.SARVAM_STT_LANGUAGE,
        "mode": mode or settings.SARVAM_STT_MODE,
    }
    files = {"file": (filename, audio, "audio/webm")}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(STT_URL, headers=_headers(), data=data, files=files)
    except httpx.HTTPError as exc:
        raise SarvamError(
            f"Could not reach Sarvam speech-to-text: {exc.__class__.__name__}."
        ) from exc

    if response.status_code >= 400:
        raise SarvamError(
            f"Sarvam speech-to-text failed (HTTP {response.status_code}): {response.text[:200]}"
        )

    body = response.json()
    return {
        "transcript": (body.get("transcript") or "").strip(),
        "language_code": body.get("language_code"),
    }


async def text_to_speech_chunks(text: str, language_code: Optional[str] = None) -> List[dict]:
    """Split text into chunks and synthesise each one.

    Returns [{"index", "text", "audio"(base64|None), "error"}]. With no key the
    text still comes back, just without audio, so the UI keeps working.
    """
    language = language_code or settings.SARVAM_TTS_LANGUAGE
    chunks = chunk_text(text, settings.TTS_CHUNK_CHARS)

    if not settings.sarvam_enabled:
        return [
            {
                "index": index,
                "text": chunk,
                "audio": None,
                "error": "SARVAM_API_KEY is not set - showing text only.",
            }
            for index, chunk in enumerate(chunks)
        ]

    results: List[dict] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for index, chunk in enumerate(chunks):
            payload = {
                "text": chunk,
                "language_code": language,
                "speaker": settings.SARVAM_TTS_SPEAKER,
                "model": settings.SARVAM_TTS_MODEL,
                "pace": settings.SARVAM_TTS_PACE,
                "speech_sample_rate": 22050,
                "output_audio_codec": "wav",
            }
            try:
                response = await client.post(TTS_URL, headers=_headers(), json=payload)
            except httpx.HTTPError as exc:
                raise SarvamError(
                    f"Could not reach Sarvam text-to-speech: {exc.__class__.__name__}."
                ) from exc

            if response.status_code >= 400:
                raise SarvamError(
                    f"Sarvam text-to-speech failed (HTTP {response.status_code}): {response.text[:200]}"
                )

            audios = response.json().get("audios") or []
            results.append(
                {
                    "index": index,
                    "text": chunk,
                    "audio": audios[0] if audios else None,
                    "error": None if audios else "Sarvam returned no audio for this chunk.",
                }
            )
    return results

# --------------------------------------------------------------------------- #
# Language detection - nothing is hardcoded, the language comes from the audio
# (Sarvam STT reports it) or from the text (Sarvam /text-lid, then a local
# script check so detection still works with no key).
# --------------------------------------------------------------------------- #
LID_URL = "https://api.sarvam.ai/text-lid"

# Languages Bulbul can actually speak; anything else falls back.
TTS_LANGUAGES = {
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN",
    "od-IN", "pa-IN", "ta-IN", "te-IN",
}

_SCRIPT_RANGES = [
    (0x0900, 0x097F, "hi-IN"),   # Devanagari (Hindi, Marathi)
    (0x0980, 0x09FF, "bn-IN"),   # Bengali
    (0x0A00, 0x0A7F, "pa-IN"),   # Gurmukhi
    (0x0A80, 0x0AFF, "gu-IN"),   # Gujarati
    (0x0B00, 0x0B7F, "od-IN"),   # Odia
    (0x0B80, 0x0BFF, "ta-IN"),   # Tamil
    (0x0C00, 0x0C7F, "te-IN"),   # Telugu
    (0x0C80, 0x0CFF, "kn-IN"),   # Kannada
    (0x0D00, 0x0D7F, "ml-IN"),   # Malayalam
]

# Romanised Hindi words that give the game away when the script is Latin.
_HINGLISH_HINTS = {
    "mujhe", "muje", "mujhko", "chahiye", "chaiye", "chahie", "karo", "karna",
    "hai", "nahi", "nhi", "aur", "kitna", "kitne", "doodh", "dudh", "atta",
    "sabun", "paisa", "dena", "dijiye", "bhai", "wala", "kilo", "packet",
}


def detect_language_local(text: str) -> str:
    """Script + Hinglish heuristic, used when Sarvam cannot be asked."""
    text = text or ""
    counts = {}
    for char in text:
        code = ord(char)
        for low, high, language in _SCRIPT_RANGES:
            if low <= code <= high:
                counts[language] = counts.get(language, 0) + 1
                break
    if counts:
        return max(counts, key=counts.get)

    words = set(re.findall(r"[a-z]+", text.lower()))
    if words & _HINGLISH_HINTS:
        return "hi-IN"
    return "en-IN"


def _normalise(code: str | None, fallback: str) -> str:
    if code in TTS_LANGUAGES:
        return code
    return fallback if fallback in TTS_LANGUAGES else "hi-IN"


async def identify_language(text: str, fallback: str | None = None) -> dict:
    """Detect the language of a piece of text.

    Returns {"language_code", "source"} where source is 'sarvam', 'local' or
    'fallback' so the caller can tell how it was decided.
    """
    default = fallback or settings.SARVAM_TTS_LANGUAGE
    text = (text or "").strip()
    if not text:
        return {"language_code": _normalise(None, default), "source": "fallback"}

    if settings.sarvam_enabled and len(text) <= 1000:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(LID_URL, headers=_headers(), json={"input": text})
            if response.status_code < 400:
                code = (response.json() or {}).get("language_code")
                if code:
                    return {"language_code": _normalise(code, default), "source": "sarvam"}
        except httpx.HTTPError:
            pass

    return {"language_code": detect_language_local(text), "source": "local"}


async def synthesize(text: str, language_code: Optional[str] = None) -> Optional[str]:
    """Synthesise ONE chunk and return its base64 WAV (None when TTS is off).

    The streaming endpoint calls this per chunk, so playback can start on the
    first sentence while the rest is still being generated.
    """
    text = (text or "").strip()
    if not text or not settings.sarvam_enabled:
        return None

    payload = {
        "text": text,
        "language_code": language_code or settings.SARVAM_TTS_LANGUAGE,
        "speaker": settings.SARVAM_TTS_SPEAKER,
        "model": settings.SARVAM_TTS_MODEL,
        "pace": settings.SARVAM_TTS_PACE,
        "speech_sample_rate": 22050,
        "output_audio_codec": "wav",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(TTS_URL, headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise SarvamError(f"Could not reach Sarvam text-to-speech: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        raise SarvamError(
            f"Sarvam text-to-speech failed (HTTP {response.status_code}): {response.text[:200]}"
        )
    audios = response.json().get("audios") or []
    return audios[0] if audios else None
