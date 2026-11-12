"""Turns a sentence into items.

Handles English, Hinglish and Devanagari, and needs no API call. That matters
more than it sounds: the tests run offline, and so does the demo when a key
runs out.
"""
import re
from typing import List, Optional

from .models import ParsedItem

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "half": 0.5,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "आधा": 0.5, "आधी": 0.5,
}

# Romanised Hindi numbers. These are only trusted at the start of a clause or
# right before a unit, because words like "do" are also ordinary verbs
# ("kar do") and would otherwise be read as the number two.
_HINGLISH_NUMBERS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "panch": 5,
    "paanch": 5, "chhah": 6, "chah": 6, "che": 6, "saat": 7, "aath": 8,
    "nau": 9, "das": 10, "aadha": 0.5, "adha": 0.5, "aadhi": 0.5,
}

_UNITS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg", "किलो": "kg", "किलोग्राम": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g", "ग्राम": "g",
    "l": "l", "lt": "l", "ltr": "l", "litre": "l", "litres": "l",
    "liter": "l", "liters": "l", "लीटर": "l",
    "ml": "ml", "मिली": "ml", "मिलीलीटर": "ml",
    "packet": "packet", "packets": "packet", "pack": "packet", "packs": "packet",
    "पैकेट": "packet",
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece",
    "nos": "piece", "पीस": "piece", "नग": "piece",
    "dozen": "dozen", "dozens": "dozen", "दर्जन": "dozen",
    "bottle": "bottle", "bottles": "bottle", "बोतल": "bottle",
    "box": "box", "boxes": "box", "डिब्बा": "box",
    "bunch": "bunch", "गड्डी": "bunch",
}

# Item words -> English search term. Spoonacular's catalog is English-first,
# so Hindi/Hinglish item names are translated before the search call.
_ITEM_WORDS = {
    "दूध": "milk", "ब्रेड": "bread", "अंडा": "eggs", "अंडे": "eggs",
    "चावल": "rice", "आटा": "flour", "चीनी": "sugar", "तेल": "oil",
    "चाय": "tea", "कॉफी": "coffee", "मक्खन": "butter", "पनीर": "paneer",
    "दाल": "lentils", "नमक": "salt", "आलू": "potato", "प्याज": "onion",
    "टमाटर": "tomato", "साबुन": "soap", "शैम्पू": "shampoo",
    "बिस्किट": "biscuits", "दही": "yogurt", "मैगी": "noodles",
    "पानी": "water", "चॉकलेट": "chocolate", "फल": "fruit",
    "सब्जी": "vegetables", "नमकीन": "snacks",
    "doodh": "milk", "dudh": "milk", "anda": "eggs", "ande": "eggs",
    "chawal": "rice", "atta": "flour", "cheeni": "sugar", "chini": "sugar",
    "tel": "oil", "chai": "tea", "makhan": "butter", "dahi": "yogurt",
    "pyaaz": "onion", "pyaz": "onion", "tamatar": "tomato", "aloo": "potato",
    "sabun": "soap", "dal": "lentils", "namak": "salt", "biscuit": "biscuits",
    "maggi": "noodles", "paani": "water", "ghee": "ghee",
}

_FILLER = {
    "mujhe", "muje", "mujhko", "chahiye", "chaiye", "chahie", "please", "pls",
    "i", "need", "want", "add", "get", "buy", "some", "of", "the", "karo",
    "kar", "do", "dena", "de", "dijiye", "lao", "laao", "list", "cart", "me",
    "my", "also", "and", "aur", "plus", "for", "to", "only", "just",
    "मुझे", "हमें", "चाहिए", "कृपया", "लाओ", "दीजिए", "और", "भी", "में",
    "का", "की", "के", "सिर्फ",
}

_SEPARATORS = re.compile(r"[,;\n]|\band\b|\baur\b|और|\bplus\b|\+")
_ATTACHED_QTY = re.compile(r"^(\d+(?:\.\d+)?)([^\d]+)$")
_PLAIN_NUM = re.compile(r"^\d+(?:\.\d+)?$")


def _strip(token: str) -> str:
    return token.strip(".,!?;:'\"()[]{}")


def _translate(name: str) -> str:
    return " ".join(_ITEM_WORDS.get(word, word) for word in name.split())


def parse_segment(segment: str) -> Optional[ParsedItem]:
    """Parse one clause such as '2 packet bread'."""
    tokens = [_strip(t) for t in segment.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None

    quantity: Optional[float] = None
    unit: Optional[str] = None
    name_tokens: List[str] = []

    for index, token in enumerate(tokens):
        low = token.lower()

        attached = _ATTACHED_QTY.match(low)
        if attached and attached.group(2) in _UNITS:
            if quantity is None:
                quantity = float(attached.group(1))
            if unit is None:
                unit = _UNITS[attached.group(2)]
            continue

        if _PLAIN_NUM.match(low):
            if quantity is None:
                quantity = float(low)
            continue

        if low in _NUMBER_WORDS:
            if quantity is None:
                quantity = float(_NUMBER_WORDS[low])
            continue

        if low in _HINGLISH_NUMBERS:
            next_is_unit = (
                index + 1 < len(tokens) and tokens[index + 1].lower() in _UNITS
            )
            if index == 0 or next_is_unit:
                if quantity is None:
                    quantity = float(_HINGLISH_NUMBERS[low])
                continue

        if low in _UNITS:
            if unit is None:
                unit = _UNITS[low]
            continue

        if low in _FILLER:
            continue

        name_tokens.append(low)

    if not name_tokens:
        return None

    name = " ".join(name_tokens)
    return ParsedItem(
        raw=segment.strip(),
        name=name,
        query=_translate(name),
        quantity=quantity if quantity is not None else 1.0,
        unit=unit,
    )


def parse_text(text: str) -> List[ParsedItem]:
    """Split a sentence into items and parse each one."""
    if not text or not text.strip():
        return []

    normalized = text.translate(_DIGITS).lower()
    segments = [s for s in _SEPARATORS.split(normalized) if s and s.strip()]

    items: List[ParsedItem] = []
    for segment in segments:
        parsed = parse_segment(segment)
        if parsed:
            items.append(parsed)
    return items
