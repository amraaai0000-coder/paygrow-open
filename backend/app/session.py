"""The cards from the last turn, per session.

"Book card 4" means nothing on its own, so the server keeps the last few cards
it showed. Clients usually send them back, but a reloaded page does not, and
this fills that gap. A dict with an expiry, not a database.
"""
import time
from collections import OrderedDict
from typing import List, Optional

MAX_SESSIONS = 500
TTL_SECONDS = 60 * 60

_store: "OrderedDict[str, dict]" = OrderedDict()


def remember_cards(session_id: Optional[str], cards: List[dict]) -> None:
    if not session_id or not cards:
        return
    _store[session_id] = {"cards": cards, "at": time.time()}
    _store.move_to_end(session_id)
    while len(_store) > MAX_SESSIONS:
        _store.popitem(last=False)


def recall_cards(session_id: Optional[str]) -> Optional[List[dict]]:
    if not session_id:
        return None
    entry = _store.get(session_id)
    if not entry:
        return None
    if time.time() - entry["at"] > TTL_SECONDS:
        _store.pop(session_id, None)
        return None
    _store.move_to_end(session_id)
    return entry["cards"]


def forget(session_id: Optional[str]) -> None:
    if session_id:
        _store.pop(session_id, None)
