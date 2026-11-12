"""Sessions, history and the chat (non-shopping) intent."""
import os

os.environ.pop("SPOONACULAR_API_KEY", None)
os.environ.pop("PAYPAL_CLIENT_ID", None)
os.environ.pop("PAYPAL_CLIENT_SECRET", None)
os.environ.pop("SARVAM_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_a_session_can_be_created_and_listed():
    created = client.post("/api/sessions").json()
    assert created["id"].startswith("pg-")

    listed = client.get("/api/sessions").json()
    assert any(row["id"] == created["id"] for row in listed)


def test_unknown_session_is_a_404():
    assert client.get("/api/sessions/does-not-exist").status_code == 404


def test_a_turn_is_stored_with_its_cards():
    session_id = client.post("/api/sessions").json()["id"]
    body = client.post(
        "/api/agent/understand",
        json={"text": "mujhe ek bread chahiye", "session_id": session_id},
    ).json()
    assert body["cards"]

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["messages"], "the turn should be persisted"
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[-1]["cards"]


def test_greeting_does_not_create_an_order():
    body = client.post(
        "/api/agent/understand", json={"text": "hi", "language": "en-IN"}
    ).json()
    assert body["intent"] == "chat"
    assert body["cards"] == []
    assert body["order"] is None
    assert body["narration"].strip()


def test_a_question_is_answered_not_shopped():
    body = client.post(
        "/api/agent/understand", json={"text": "what can you do?", "language": "en-IN"}
    ).json()
    assert body["intent"] == "chat"
    assert body["cards"] == []


def test_buying_intent_still_searches():
    body = client.post(
        "/api/agent/understand", json={"text": "mujhe do kilo doodh chahiye"}
    ).json()
    assert body["intent"] == "search"
    assert body["cards"]


def test_a_session_holds_at_most_six_orders():
    session_id = client.post("/api/sessions").json()["id"]
    for index in range(db.MAX_ORDERS):
        assert db.record_turn(
            session_id, f"item {index}", "ok", order={"order_id": f"o-{index}", "status": "CREATED"}
        )
    assert db.record_turn(
        session_id, "one more", "ok", order={"order_id": "o-extra", "status": "CREATED"}
    ) is False

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert len(detail["orders"]) == db.MAX_ORDERS


def test_sessions_do_not_share_history():
    first = client.post("/api/sessions").json()["id"]
    second = client.post("/api/sessions").json()["id"]

    client.post(
        "/api/agent/understand",
        json={"text": "mujhe ek bread chahiye", "session_id": first},
    )
    detail = client.get(f"/api/sessions/{second}").json()
    assert detail["messages"] == []


def test_dictation_endpoint_explains_a_missing_key():
    response = client.post(
        "/api/stt",
        files={"file": ("chunk.wav", b"fake-bytes", "audio/wav")},
        data={"language": "auto"},
    )
    assert response.status_code == 502
    assert "SARVAM_API_KEY" in response.json()["detail"]
