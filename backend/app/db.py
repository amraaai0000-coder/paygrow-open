"""Sessions, their messages and their orders.

Postgres when a connection string is in the environment, a dict in memory
otherwise. The memory store is what keeps the tests and the demo running
without a database. It forgets everything on restart, so do not treat it as
storage.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import settings

MAX_ORDERS = 6

_SCHEMA = """
create table if not exists sessions (
    id          text primary key,
    title       text not null default 'New order',
    owner       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

alter table sessions add column if not exists owner text;

create table if not exists messages (
    id          bigserial primary key,
    session_id  text not null references sessions(id) on delete cascade,
    role        text not null,
    body        text not null,
    cards       jsonb,
    meta        text,
    created_at  timestamptz not null default now()
);

create table if not exists orders (
    order_id    text primary key,
    session_id  text not null references sessions(id) on delete cascade,
    status      text not null,
    amount      numeric,
    currency    text,
    approve_url text,
    created_at  timestamptz not null default now()
);

create index if not exists messages_session_idx on messages(session_id);
create index if not exists orders_session_idx on orders(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Process-local fallback. Same surface as the Postgres store."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.messages: Dict[str, List[Dict[str, Any]]] = {}
        self.orders: Dict[str, List[Dict[str, Any]]] = {}

    def create_session(self, title: str = "New order", owner: str = "") -> Dict[str, Any]:
        session_id = f"pg-{uuid.uuid4().hex[:12]}"
        row = {"id": session_id, "title": title, "owner": owner, "created_at": _now()}
        self.sessions[session_id] = row
        self.messages[session_id] = []
        self.orders[session_id] = []
        return row

    def list_sessions(self, owner: str = "") -> List[Dict[str, Any]]:
        out = []
        for session_id, row in self.sessions.items():
            if owner and row.get("owner") != owner:
                continue
            out.append(
                {
                    **row,
                    "message_count": len(self.messages.get(session_id, [])),
                    "order_count": len(self.orders.get(session_id, [])),
                }
            )
        return sorted(out, key=lambda r: r["created_at"], reverse=True)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.sessions.get(session_id)
        if not row:
            return None
        return {
            **row,
            "messages": self.messages.get(session_id, []),
            "orders": self.orders.get(session_id, []),
        }

    def ensure_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            return
        self.sessions[session_id] = {
            "id": session_id,
            "title": "New order",
            "created_at": _now(),
        }
        self.messages[session_id] = []
        self.orders[session_id] = []

    def touch_title(self, session_id: str, title: str) -> None:
        row = self.sessions.get(session_id)
        if row and row.get("title") in ("New order", "", None):
            row["title"] = title[:60]

    def add_message(
        self,
        session_id: str,
        role: str,
        body: str,
        cards: Optional[List[dict]] = None,
        meta: Optional[str] = None,
    ) -> None:
        self.ensure_session(session_id)
        self.messages[session_id].append(
            {
                "role": role,
                "text": body,
                "cards": cards or [],
                "meta": meta,
                "at": _now(),
            }
        )

    def add_order(self, session_id: str, order: Dict[str, Any]) -> bool:
        bucket = self.orders.setdefault(session_id, [])
        if len(bucket) >= MAX_ORDERS:
            return False
        bucket.append({**order, "at": _now()})
        return True


class PostgresStore:
    """Thin wrapper over psycopg. One connection per call; the pool is psycopg's."""

    def __init__(self, url: str) -> None:
        import psycopg  # imported lazily so the memory path needs no driver

        self._psycopg = psycopg
        self.url = url
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()

    def _connect(self):
        return self._psycopg.connect(self.url, autocommit=False)

    def create_session(self, title: str = "New order", owner: str = "") -> Dict[str, Any]:
        session_id = f"pg-{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into sessions (id, title, owner) values (%s, %s, %s) "
                    "returning id, title, created_at",
                    (session_id, title, owner or None),
                )
                row = cur.fetchone()
            conn.commit()
        return {"id": row[0], "title": row[1], "created_at": row[2].isoformat()}

    def list_sessions(self, owner: str = "") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select s.id, s.title, s.created_at,
                           (select count(*) from messages m where m.session_id = s.id),
                           (select count(*) from orders o where o.session_id = s.id)
                    from sessions s
                    where (%s = '' or s.owner = %s)
                    order by s.created_at desc
                    limit 50
                    """,
                    (owner, owner),
                )
                rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "title": r[1],
                "created_at": r[2].isoformat(),
                "message_count": r[3],
                "order_count": r[4],
            }
            for r in rows
        ]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, title, created_at from sessions where id = %s",
                    (session_id,),
                )
                head = cur.fetchone()
                if not head:
                    return None
                cur.execute(
                    "select role, body, cards, meta, created_at from messages "
                    "where session_id = %s order by id",
                    (session_id,),
                )
                messages = [
                    {
                        "role": r[0],
                        "text": r[1],
                        "cards": r[2] or [],
                        "meta": r[3],
                        "at": r[4].isoformat(),
                    }
                    for r in cur.fetchall()
                ]
                cur.execute(
                    "select order_id, status, amount, currency, approve_url, created_at "
                    "from orders where session_id = %s order by created_at",
                    (session_id,),
                )
                orders = [
                    {
                        "order_id": r[0],
                        "status": r[1],
                        "amount": float(r[2]) if r[2] is not None else None,
                        "currency": r[3],
                        "approve_url": r[4],
                        "at": r[5].isoformat(),
                    }
                    for r in cur.fetchall()
                ]
        return {
            "id": head[0],
            "title": head[1],
            "created_at": head[2].isoformat(),
            "messages": messages,
            "orders": orders,
        }

    def ensure_session(self, session_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into sessions (id, title) values (%s, 'New order') "
                    "on conflict (id) do nothing",
                    (session_id,),
                )
            conn.commit()

    def touch_title(self, session_id: str, title: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update sessions set title = %s, updated_at = now() "
                    "where id = %s and (title = 'New order' or title = '')",
                    (title[:60], session_id),
                )
            conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        body: str,
        cards: Optional[List[dict]] = None,
        meta: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into messages (session_id, role, body, cards, meta) "
                    "values (%s, %s, %s, %s::jsonb, %s)",
                    (session_id, role, body, json.dumps(cards or []), meta),
                )
                cur.execute(
                    "update sessions set updated_at = now() where id = %s",
                    (session_id,),
                )
            conn.commit()

    def add_order(self, session_id: str, order: Dict[str, Any]) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select count(*) from orders where session_id = %s", (session_id,)
                )
                count = cur.fetchone()[0]
                if count >= MAX_ORDERS:
                    return False
                cur.execute(
                    "insert into orders (order_id, session_id, status, amount, currency, approve_url) "
                    "values (%s, %s, %s, %s, %s, %s) on conflict (order_id) do update "
                    "set status = excluded.status",
                    (
                        order.get("order_id"),
                        session_id,
                        order.get("status", "CREATED"),
                        order.get("amount"),
                        order.get("currency"),
                        order.get("approve_url"),
                    ),
                )
            conn.commit()
        return True


# The connection string can arrive under any of these names - Render keeps
# whatever you typed in the dashboard, so match generously instead of guessing.
_URL_NAMES = (
    "DATABASE_URL",
    "PAYGROW_DATABASE_URL",
    "PAYGROW_DATABASE",
    "PAYGROW_DB_URL",
    "Paygrow_database",
    "paygrow_database",
)


def _looks_like_url(value: str) -> bool:
    return "://" in value and len(value) > 12


def _database_url() -> tuple:
    """Return (url, source_name). The value must be a full connection string."""
    for name in _URL_NAMES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value, name

    # last resort: any variable whose name mentions both paygrow and database
    for key, value in os.environ.items():
        low = key.lower()
        if "paygrow" in low and ("database" in low or low.endswith("_db") or "_db_" in low):
            if value.strip():
                return value.strip(), key
    return "", ""


def _normalise(url: str) -> str:
    # psycopg wants postgresql://, some dashboards hand out postgres://
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _build_store():
    url, source = _database_url()
    if not url:
        print("[db] no database URL in the environment, using memory")
        return MemoryStore(), "memory", ""

    if not _looks_like_url(url):
        print(
            f"[db] {source} does not look like a connection string "
            f"(needs postgresql://user:pass@host/db). Using memory for now."
        )
        return MemoryStore(), "memory", source

    try:
        return PostgresStore(_normalise(url)), "postgres", source
    except Exception as exc:  # noqa: BLE001
        print(f"[db] could not open the database from {source}: {exc}")
        return MemoryStore(), "memory", source


store, backend, backend_source = _build_store()


def mode() -> str:
    return backend


def source() -> str:
    """Which environment variable the connection string came from."""
    return backend_source


def create_session(title: str = "New order", owner: str = "") -> Dict[str, Any]:
    return store.create_session(title, owner)


def list_sessions(owner: str = "") -> List[Dict[str, Any]]:
    return store.list_sessions(owner)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return store.get_session(session_id)


def record_turn(
    session_id: str,
    user_text: str,
    assistant_text: str,
    cards: Optional[List[dict]] = None,
    meta: Optional[str] = None,
    order: Optional[Dict[str, Any]] = None,
) -> bool:
    """Persist one turn. Returns False when the session already has MAX_ORDERS."""
    if not session_id:
        return True
    store.ensure_session(session_id)
    store.touch_title(session_id, user_text)
    store.add_message(session_id, "user", user_text)
    store.add_message(session_id, "assistant", assistant_text, cards, meta)
    if order:
        return store.add_order(session_id, order)
    return True
