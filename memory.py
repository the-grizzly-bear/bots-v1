"""Lightweight shared memory: a rolling log of recently-covered topics so
personas don't react fresh to the same story twice. Not a knowledge base -
just enough recent context to avoid repetition."""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "memory.db"
RECENT_LIMIT = 15


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts INTEGER, channel TEXT, summary TEXT)"
    )
    conn.commit()
    return conn


_conn = init_db()


def remember(channel: str, summary: str):
    _conn.execute(
        "INSERT INTO events (ts, channel, summary) VALUES (?, ?, ?)",
        (int(time.time()), channel, summary[:300]),
    )
    _conn.commit()
    _conn.execute(
        "DELETE FROM events WHERE id NOT IN "
        "(SELECT id FROM events ORDER BY id DESC LIMIT 200)"
    )
    _conn.commit()


def recent_context(limit: int = RECENT_LIMIT) -> str:
    rows = _conn.execute(
        "SELECT channel, summary FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return ""
    lines = [f"#{channel}: {summary}" for channel, summary in reversed(rows)]
    return "--- recently covered, don't react fresh to these again ---\n" + "\n".join(lines)
