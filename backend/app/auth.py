"""Email login with a six digit code.

Ask for a code, get it by email, type it back, get a token. Codes last ten
minutes, work once, and only their hash is stored. Brevo sends them over HTTPS
because Render's free plan blocks outbound SMTP. If no provider is configured
the code comes back in the response instead, which is how the app gets tested
before the mail account exists.
"""
import hashlib
import os
import random
import secrets
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Dict, Optional, Tuple

import httpx

from . import db

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5

_SCHEMA = """
create table if not exists users (
    id          text primary key,
    email       text unique not null,
    created_at  timestamptz not null default now()
);

create table if not exists login_codes (
    email       text primary key,
    code_hash   text not null,
    expires_at  timestamptz not null,
    attempts    int not null default 0
);

create table if not exists auth_tokens (
    token       text primary key,
    user_id     text not null references users(id) on delete cascade,
    created_at  timestamptz not null default now()
);
"""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Storage: Postgres when it is there, memory otherwise
# --------------------------------------------------------------------------- #
class _MemoryAuth:
    def __init__(self) -> None:
        self.users: Dict[str, Dict] = {}          # email -> user
        self.codes: Dict[str, Dict] = {}          # email -> {hash, expires, attempts}
        self.tokens: Dict[str, str] = {}          # token -> user id

    def ensure_schema(self) -> None:
        return

    def put_code(self, email: str, code_hash: str, expires: datetime) -> None:
        self.codes[email] = {"hash": code_hash, "expires": expires, "attempts": 0}

    def get_code(self, email: str) -> Optional[Dict]:
        return self.codes.get(email)

    def bump_attempts(self, email: str) -> None:
        if email in self.codes:
            self.codes[email]["attempts"] += 1

    def drop_code(self, email: str) -> None:
        self.codes.pop(email, None)

    def upsert_user(self, email: str) -> Dict:
        user = self.users.get(email)
        if user is None:
            user = {"id": f"u-{secrets.token_hex(8)}", "email": email}
            self.users[email] = user
        return user

    def save_token(self, token: str, user_id: str) -> None:
        self.tokens[token] = user_id

    def user_for_token(self, token: str) -> Optional[Dict]:
        user_id = self.tokens.get(token)
        if not user_id:
            return None
        for user in self.users.values():
            if user["id"] == user_id:
                return user
        return None


class _PostgresAuth:
    def __init__(self, url: str) -> None:
        import psycopg

        self._psycopg = psycopg
        self.url = url
        self.ensure_schema()

    def _connect(self):
        return self._psycopg.connect(self.url, autocommit=False)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()

    def put_code(self, email: str, code_hash: str, expires: datetime) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into login_codes (email, code_hash, expires_at, attempts) "
                    "values (%s, %s, %s, 0) on conflict (email) do update "
                    "set code_hash = excluded.code_hash, expires_at = excluded.expires_at, "
                    "attempts = 0",
                    (email, code_hash, expires),
                )
            conn.commit()

    def get_code(self, email: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select code_hash, expires_at, attempts from login_codes where email = %s",
                    (email,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {"hash": row[0], "expires": row[1], "attempts": row[2]}

    def bump_attempts(self, email: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update login_codes set attempts = attempts + 1 where email = %s",
                    (email,),
                )
            conn.commit()

    def drop_code(self, email: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from login_codes where email = %s", (email,))
            conn.commit()

    def upsert_user(self, email: str) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("select id, email from users where email = %s", (email,))
                row = cur.fetchone()
                if row:
                    return {"id": row[0], "email": row[1]}
                user_id = f"u-{secrets.token_hex(8)}"
                cur.execute(
                    "insert into users (id, email) values (%s, %s)", (user_id, email)
                )
            conn.commit()
        return {"id": user_id, "email": email}

    def save_token(self, token: str, user_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into auth_tokens (token, user_id) values (%s, %s)",
                    (token, user_id),
                )
            conn.commit()

    def user_for_token(self, token: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select u.id, u.email from auth_tokens t "
                    "join users u on u.id = t.user_id where t.token = %s",
                    (token,),
                )
                row = cur.fetchone()
        return {"id": row[0], "email": row[1]} if row else None


def _build_auth():
    if db.mode() == "postgres":
        url, _ = db._database_url()
        try:
            return _PostgresAuth(db._normalise(url))
        except Exception as exc:  # noqa: BLE001
            print(f"[auth] could not prepare the auth tables: {exc}")
    return _MemoryAuth()


_auth = None


def _store():
    global _auth
    if _auth is None:
        _auth = _build_auth()
    return _auth


# --------------------------------------------------------------------------- #
# Sending the code
# --------------------------------------------------------------------------- #
def _brevo_sender() -> str:
    """Brevo needs a verified sender. Accept the names Conquer-chat already uses."""
    for name in ("BREVO_SENDER_EMAIL", "BREVO_SENDER", "BREVO_FROM"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _send_with_brevo(to_email: str, code: str) -> bool:
    key = (os.getenv("BREVO_API_KEY") or "").strip()
    if not key:
        return False
    sender = _brevo_sender() or "no-reply@paygrow.app"
    sender_name = (os.getenv("BREVO_SENDER_NAME") or "PayGrow").strip()
    payload = {
        "sender": {"name": sender_name, "email": sender},
        "to": [{"email": to_email}],
        "subject": f"Your PayGrow code: {code}",
        "htmlContent": (
            f"<p>Your PayGrow login code is <b style='font-size:20px'>{code}</b>.</p>"
            "<p>It expires in 10 minutes. If you did not ask for it, ignore this email.</p>"
        ),
    }
    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=20.0,
        )
        return response.status_code < 400
    except httpx.HTTPError:
        return False


def _send_with_smtp(to_email: str, code: str) -> bool:
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        return False
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASS") or "").strip()
    port = int(os.getenv("SMTP_PORT") or "587")
    sender = (os.getenv("SMTP_FROM") or user or "no-reply@paygrow.app").strip()

    message = MIMEText(
        f"Your PayGrow login code is {code}. It expires in 10 minutes.", "plain"
    )
    message["Subject"] = f"Your PayGrow code: {code}"
    message["From"] = sender
    message["To"] = to_email
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            if user:
                server.login(user, password)
            server.sendmail(sender, [to_email], message.as_string())
        return True
    except Exception:  # noqa: BLE001
        return False


def send_code(email: str, code: str) -> str:
    """Returns the channel actually used: brevo | smtp | dev.

    Brevo's HTTPS API is tried first on purpose: Render's free plan blocks
    outbound SMTP ports, so an SMTP key silently fails there.
    """
    if _send_with_brevo(email, code):
        return "brevo"
    if _send_with_smtp(email, code):
        return "smtp"
    print(f"[auth] no mail provider configured - code for {email} is {code}")
    return "dev"


# --------------------------------------------------------------------------- #
# The operations the endpoints call
# --------------------------------------------------------------------------- #
def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def debug_otp() -> bool:
    """DEBUG_OTP=true makes the code visible in the logs and the response.

    Off by default. Turn it on only while wiring the mail provider up, never
    with real users.
    """
    return (os.getenv("DEBUG_OTP") or "").strip().lower() in ("1", "true", "yes", "on")


def request_code(email: str) -> Tuple[str, str]:
    """Create and send a code. Returns (channel, code)."""
    email = normalise_email(email)
    code = f"{random.randint(0, 999999):06d}"
    expires = datetime.now(timezone.utc) + CODE_TTL
    _store().put_code(email, _hash(code), expires)
    channel = send_code(email, code)
    if debug_otp():
        print(f"[auth] DEBUG_OTP is on - code for {email} is {code}")
    return channel, code


def verify_code(email: str, code: str) -> Tuple[bool, str, Optional[Dict]]:
    email = normalise_email(email)
    record = _store().get_code(email)
    if not record:
        return False, "Ask for a code first.", None

    expires = record["expires"]
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        _store().drop_code(email)
        return False, "That code has expired. Ask for a new one.", None

    if record["attempts"] >= MAX_ATTEMPTS:
        _store().drop_code(email)
        return False, "Too many attempts. Ask for a new code.", None

    if record["hash"] != _hash((code or "").strip()):
        _store().bump_attempts(email)
        return False, "That code is not right.", None

    _store().drop_code(email)
    user = _store().upsert_user(email)
    token = secrets.token_urlsafe(32)
    _store().save_token(token, user["id"])
    return True, "", {**user, "token": token}


def user_for_token(token: Optional[str]) -> Optional[Dict]:
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return _store().user_for_token(token)
