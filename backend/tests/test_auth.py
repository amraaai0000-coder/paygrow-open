"""Email-code login, and the way it scopes sessions."""
import os

os.environ.pop("DATABASE_URL", None)
os.environ.pop("Paygrow_database", None)
os.environ.pop("BREVO_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def _login(email="saurav@example.com"):
    sent = client.post("/api/auth/request-code", json={"email": email}).json()
    assert sent["sent"] is True
    code = sent["dev_code"]
    assert code and len(code) == 6
    verified = client.post("/api/auth/verify", json={"email": email, "code": code}).json()
    return verified


def test_a_code_is_emailed_and_verifies():
    user = _login()
    assert user["email"] == "saurav@example.com"
    assert user["token"]


def test_a_wrong_code_is_rejected():
    client.post("/api/auth/request-code", json={"email": "wrong@example.com"})
    response = client.post(
        "/api/auth/verify", json={"email": "wrong@example.com", "code": "000000"}
    )
    assert response.status_code == 401


def test_verifying_without_asking_is_rejected():
    response = client.post(
        "/api/auth/verify", json={"email": "never@example.com", "code": "123456"}
    )
    assert response.status_code == 401


def test_a_bad_email_is_refused():
    assert client.post("/api/auth/request-code", json={"email": "not-an-email"}).status_code == 422


def test_me_needs_a_token():
    assert client.get("/api/auth/me").status_code == 401
    user = _login("me@example.com")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {user['token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "me@example.com"


def test_sessions_are_scoped_to_the_signed_in_user():
    first = _login("one@example.com")
    second = _login("two@example.com")

    created = client.post(
        "/api/sessions", headers={"Authorization": f"Bearer {first['token']}"}
    ).json()

    mine = client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {first['token']}"}
    ).json()
    theirs = client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {second['token']}"}
    ).json()

    assert any(row["id"] == created["id"] for row in mine)
    assert all(row["id"] != created["id"] for row in theirs)


def test_a_guest_device_keeps_its_own_history():
    guest = client.post("/api/sessions", headers={"X-Device-Id": "phone-abc"}).json()
    listed = client.get("/api/sessions", headers={"X-Device-Id": "phone-abc"}).json()
    assert any(row["id"] == guest["id"] for row in listed)

    other = client.get("/api/sessions", headers={"X-Device-Id": "phone-xyz"}).json()
    assert all(row["id"] != guest["id"] for row in other)
