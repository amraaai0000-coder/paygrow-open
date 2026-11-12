"""Make `import app...` work no matter how pytest is invoked."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


import pytest


@pytest.fixture(autouse=True)
def clear_spoonacular_cache():
    """Keep tests independent: the search cache is module-level state."""
    try:
        from app.services import spoonacular

        spoonacular._search_cache.clear()
    except Exception:  # pragma: no cover
        pass
    yield
