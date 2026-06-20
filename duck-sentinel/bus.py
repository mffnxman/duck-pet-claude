"""
Event bus for the Duck Sentinel.
Sqlite-backed queue. Sentinel pushes, triage pops, duck brain consumes urgent.
"""
import os
import json
import sqlite3
import time
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bus.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    source      TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    data        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'new',
    priority    TEXT,
    triage_note TEXT,
    triaged_at  REAL,
    actioned_at REAL
);
CREATE INDEX IF NOT EXISTS idx_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_priority ON events(priority);
CREATE INDEX IF NOT EXISTS idx_ts ON events(ts);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with _conn() as c:
        c.executescript(SCHEMA)


def push(source: str, kind: str, data: dict) -> int:
    """Emit an event. Returns event id."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO events (ts, source, kind, data) VALUES (?, ?, ?, ?)",
            (time.time(), source, kind, json.dumps(data, default=str)),
        )
        return cur.lastrowid


def pop_untriaged(limit: int = 10):
    """Get events that haven't been classified yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE status='new' ORDER BY ts ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_triaged(event_id: int, priority: str, note: str = ""):
    """Mark event as classified. Priority: boring|interesting|urgent."""
    with _conn() as c:
        c.execute(
            """UPDATE events
               SET status='triaged', priority=?, triage_note=?, triaged_at=?
               WHERE id=?""",
            (priority, note, time.time(), event_id),
        )


def pop_urgent(limit: int = 5):
    """Get urgent events the duck brain should react to."""
    with _conn() as c:
        rows = c.execute(
            """SELECT * FROM events
               WHERE status='triaged' AND priority='urgent'
               ORDER BY ts ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_actioned(event_id: int):
    with _conn() as c:
        c.execute(
            "UPDATE events SET status='actioned', actioned_at=? WHERE id=?",
            (time.time(), event_id),
        )


def stats():
    """Quick counts for the dashboard / debugging."""
    with _conn() as c:
        rows = c.execute(
            """SELECT status, priority, COUNT(*) as n
               FROM events GROUP BY status, priority"""
        ).fetchall()
        return [dict(r) for r in rows]


def recent(limit: int = 20):
    """Recent events for debugging."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    import sys
    init()
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        for row in stats():
            print(row)
    elif len(sys.argv) > 1 and sys.argv[1] == "recent":
        for row in recent():
            print(f"[{row['id']}] {row['source']}/{row['kind']} "
                  f"pri={row['priority']} status={row['status']}")
            print(f"    data={row['data'][:120]}")
    else:
        print(f"Bus initialized at {DB_PATH}")
