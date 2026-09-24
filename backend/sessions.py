"""Server-side chat session persistence (SQLite)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from config import SESSIONS_DB

_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    new = not SESSIONS_DB.exists()
    conn = sqlite3.connect(SESSIONS_DB)
    if new:
        os.chmod(SESSIONS_DB, 0o600)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
               id TEXT PRIMARY KEY,
               owner TEXT NOT NULL,
               title TEXT DEFAULT '',
               created TEXT NOT NULL,
               updated TEXT NOT NULL,
               messages TEXT NOT NULL DEFAULT '[]'
           )"""
    )
    return conn


def create(owner: str, title: str = "") -> str:
    sid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO sessions (id, owner, title, created, updated) VALUES (?,?,?,?,?)",
            (sid, owner, title, now, now),
        )
    return sid


def append(sid: str, role: str, content: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _db() as conn:
        row = conn.execute("SELECT messages FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError(sid)
        msgs = json.loads(row[0])
        msgs.append({"role": role, "content": content})
        conn.execute("UPDATE sessions SET messages=?, updated=? WHERE id=?",
                     (json.dumps(msgs), now, sid))


def get(sid: str, owner: str, max_age_minutes: int | None = None) -> dict | None:
    with _lock, _db() as conn:
        row = conn.execute(
            "SELECT id, owner, title, created, updated, messages FROM sessions WHERE id=?",
            (sid,),
        ).fetchone()
    if not row or row[1] != owner:
        return None
    if max_age_minutes is not None:
        try:
            updated = datetime.fromisoformat(row[4])
            age = (datetime.now(timezone.utc) - updated).total_seconds() / 60
            if age > max_age_minutes:
                return None  # expired
        except ValueError:
            pass
    return {"id": row[0], "owner": row[1], "title": row[2],
            "created": row[3], "updated": row[4], "messages": json.loads(row[5])}


def list_for(owner: str) -> list[dict]:
    with _lock, _db() as conn:
        rows = conn.execute(
            "SELECT id, title, created, updated FROM sessions WHERE owner=? ORDER BY updated DESC",
            (owner,),
        ).fetchall()
    return [{"id": r[0], "title": r[1], "created": r[2], "updated": r[3]} for r in rows]


def delete(sid: str, owner: str) -> bool:
    with _lock, _db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id=? AND owner=?", (sid, owner))
        return cur.rowcount > 0
