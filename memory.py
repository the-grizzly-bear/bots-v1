"""Lightweight shared memory: a rolling log of recently-covered topics so
personas don't react fresh to the same story twice. Not a knowledge base -
just enough recent context to avoid repetition."""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "memory.db"
RECENT_LIMIT = 10
RECAP_CHARS = 90


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
    lines = [f"#{channel}: {summary[:RECAP_CHARS]}" for channel, summary in reversed(rows)]
    return "--- recently covered, don't react fresh to these again ---\n" + "\n".join(lines)


def init_meta_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn


_meta_conn = init_meta_db()


def seconds_since_last_ping(tier: str = "default") -> float:
    row = _meta_conn.execute("SELECT value FROM meta WHERE key = ?", (f"last_ping_ts:{tier}",)).fetchone()
    if not row:
        return float("inf")
    return time.time() - float(row[0])


def record_ping(tier: str = "default"):
    _meta_conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"last_ping_ts:{tier}", str(time.time())),
    )
    _meta_conn.commit()
