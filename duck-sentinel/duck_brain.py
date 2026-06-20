"""
Duck brain — wakes on urgent events and calls headless Claude Code.

Polls bus for events with priority='urgent' and actioned_at IS NULL. For
each, builds a compact briefing (targets + recent context + event detail)
and invokes `claude -p --bare` to get a short reaction. Writes the reaction
into inbox.md and marks the event actioned.

Cheap: each wake ~300 tokens in, a few hundred out. Local triage does the
filtering so we only pay when something matters.
"""
import json
import os
import sqlite3
import time
from datetime import datetime

import bus
import session_client

SCRATCHPAD = os.path.expanduser("~/Downloads/duck_scratchpad")
INBOX = os.path.join(SCRATCHPAD, "inbox.md")
JOURNAL = os.path.join(SCRATCHPAD, "journal.md")
TARGETS = os.path.join(SCRATCHPAD, "targets.md")
# Signal file polled by pet.py — write a short message here and the on-screen
# duck pops a speech bubble. Never grows; pet read-and-deletes.
SPEAK_SIGNAL = os.path.join(SCRATCHPAD, "speak.txt")
SPEAK_MAX_CHARS = 220  # speech bubble stays readable

POLL_INTERVAL = 5.0
CLAUDE_TIMEOUT = 90  # seconds per wake
RECENT_CONTEXT_EVENTS = 5  # how many previous events to show as context

# Task-specific briefing prefix. The session holds the main Duck system prompt.
# This just frames the incoming event.
TASK_HEADER = """A local triage filter classified this event as URGENT and
woke you. Respond with one short actionable note (3-6 sentences) that will
be appended to the scratchpad inbox.md AND shown as a speech bubble.
Reason from the event data + context below. If triage was wrong (not
actually urgent), say so — helps calibration. Empty reply is fine if
there's nothing to add.
"""


def _read_tail(path: str, max_chars: int = 800) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    except OSError:
        return ""
    return data[-max_chars:] if len(data) > max_chars else data


def _recent_context(c: sqlite3.Connection, before_id: int) -> str:
    """Last N triaged events before this one — user's rough recent activity."""
    rows = list(c.execute(
        "SELECT kind, priority, data FROM events "
        "WHERE id < ? AND status='triaged' "
        "ORDER BY id DESC LIMIT ?",
        (before_id, RECENT_CONTEXT_EVENTS),
    ))
    lines = []
    for kind, priority, data in rows:
        try:
            d = json.loads(data)
        except Exception:
            d = {}
        summary = d.get("to_title") or d.get("title") or d.get("path") or kind
        lines.append(f"  - [{priority or '?'}] {kind}: {str(summary)[:80]}")
    return "\n".join(reversed(lines)) if lines else "  (no recent events)"


def build_briefing(event: dict, c: sqlite3.Connection) -> str:
    event_id = event["id"]
    try:
        data = json.loads(event["data"])
    except Exception:
        data = {"raw": event["data"]}

    targets = _read_tail(TARGETS, 600)
    inbox_tail = _read_tail(INBOX, 400)
    context = _recent_context(c, event_id)
    triage_note = event.get("triage_note") or "(no note)"

    return f"""{TASK_HEADER}

## Event
- source: {event['source']}
- kind: {event['kind']}
- triage said: {triage_note}
- detail: {json.dumps(data, ensure_ascii=False)[:500]}

## User's watch targets (from ~/Downloads/duck_scratchpad/targets.md)
{targets.strip()}

## Recent activity (last {RECENT_CONTEXT_EVENTS} events, oldest first)
{context}

## Tail of the scratchpad inbox.md (your past reactions — NOT Gmail)
{inbox_tail.strip() or '(empty)'}

---
Respond in 3-6 sentences. No preamble."""


def call_duck(briefing: str) -> str:
    """Route through the warm Duck session; falls back to cold claude -p."""
    return session_client.ask(briefing, timeout=CLAUDE_TIMEOUT)


def append_inbox(event: dict, reaction: str):
    if not reaction:
        return
    try:
        data = json.loads(event["data"])
    except Exception:
        data = {}
    ts = datetime.fromtimestamp(event["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    summary = data.get("to_title") or data.get("title") or data.get("path") or event["kind"]
    header = f"\n## {ts} — {event['kind']} · {str(summary)[:70]}"
    footer = f"\n_triage: {event.get('triage_note', '')}_\n"
    os.makedirs(SCRATCHPAD, exist_ok=True)
    with open(INBOX, "a", encoding="utf-8") as f:
        f.write(header + "\n\n" + reaction + footer)


def signal_pet(reaction: str):
    """Drop a short first-sentence version into speak.txt for the pet to show.

    Pet's speech bubble has limited room, so trim to the first sentence or
    SPEAK_MAX_CHARS, whichever comes first.
    """
    if not reaction or reaction.startswith("[duck] error"):
        return
    # First sentence heuristic — split on `. ` but preserve meaning
    first = reaction.split("\n", 1)[0]
    for sep in (". ", "? ", "! "):
        if sep in first:
            first = first.split(sep, 1)[0] + sep.strip()
            break
    if len(first) > SPEAK_MAX_CHARS:
        first = first[:SPEAK_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
    try:
        os.makedirs(SCRATCHPAD, exist_ok=True)
        with open(SPEAK_SIGNAL, "w", encoding="utf-8") as f:
            f.write(first)
    except OSError as e:
        print(f"[duck_brain] signal_pet failed: {e}")


def mark_actioned(c: sqlite3.Connection, event_id: int):
    c.execute(
        "UPDATE events SET actioned_at=? WHERE id=?",
        (time.time(), event_id),
    )
    c.commit()


def fetch_urgent(c: sqlite3.Connection) -> list[dict]:
    cols = [
        "id", "ts", "source", "kind", "data",
        "status", "priority", "triage_note", "triaged_at", "actioned_at",
    ]
    rows = c.execute(
        "SELECT " + ",".join(cols) +
        " FROM events WHERE priority='urgent' AND actioned_at IS NULL "
        "ORDER BY id ASC LIMIT 5"
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


AUTH_FAIL_PATTERNS = (
    "not logged in",
    "please run /login",
    "authentication_error",
    "invalid authentication credentials",
    "401",
)


def _auth_failed(reaction: str) -> bool:
    r = (reaction or "").lower()
    return any(p in r for p in AUTH_FAIL_PATTERNS)


def main():
    bus.init()
    print(f"[duck_brain] starting at {datetime.now().isoformat()}")
    print(f"[duck_brain] using duck_session at http://127.0.0.1:7717/ "
          f"(alive={session_client.session_alive()})")
    print(f"[duck_brain] polling bus every {POLL_INTERVAL}s for urgent events")

    auth_pause_until = 0.0  # epoch seconds — pause brain wakes if auth broken

    while True:
        try:
            now = time.time()
            if now < auth_pause_until:
                time.sleep(POLL_INTERVAL)
                continue

            c = sqlite3.connect(bus.DB_PATH)
            urgent = fetch_urgent(c)
            for ev in urgent:
                print(f"[duck_brain] waking on event {ev['id']} ({ev['kind']})")
                briefing = build_briefing(ev, c)
                reaction = call_duck(briefing)
                print(f"[duck_brain] reaction: {reaction[:100]}...")

                if _auth_failed(reaction):
                    # One speech bubble, then pause brain for 15 min so we
                    # don't spam. Does NOT mark event actioned — user can
                    # retry after /login.
                    print("[duck_brain] AUTH FAIL detected — pausing wakes")
                    signal_pet(
                        "⚠️ duck brain auth expired. run /login in main "
                        "Claude Code — I'll resume once you're back."
                    )
                    auth_pause_until = now + 900  # 15 min
                    break
                append_inbox(ev, reaction)
                signal_pet(reaction)
                mark_actioned(c, ev["id"])
            c.close()
        except Exception as e:
            print(f"[duck_brain] error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
