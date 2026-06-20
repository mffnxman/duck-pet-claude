"""
Ask listener — the duck becomes a two-way partner.

Watches ~/Downloads/duck_scratchpad/ask.txt. When the user (via pet's
right-click menu) drops a question there, this worker:
  1. Reads question + current context (inbox tail, recent events, targets)
  2. Wakes Claude Code headlessly with full MCP access (so it can read the
     active Chrome tab, console, files — whatever it needs)
  3. Writes the answer to speak.txt (pet picks it up → speech bubble)
  4. Also appends to inbox.md for later reading

This is what turns the duck from a passive alerter into an interactive
co-pilot. Cost: one claude -p call per ask. Cheap because briefings are
small and Claude Code's MCP tools do heavy lifting locally.
"""
import os
import sqlite3
import time
from datetime import datetime

import bus
import session_client

SCRATCHPAD = os.path.expanduser("~/Downloads/duck_scratchpad")
ASK = os.path.join(SCRATCHPAD, "ask.txt")
SPEAK = os.path.join(SCRATCHPAD, "speak.txt")
INBOX = os.path.join(SCRATCHPAD, "inbox.md")
TARGETS = os.path.join(SCRATCHPAD, "targets.md")

POLL_INTERVAL = 2.0
CLAUDE_TIMEOUT = 120

# Duck session already has the main persona. This prefix frames that the
# incoming text is a user question asked via right-click → pet popup.
TASK_HEADER = """The user just right-clicked you on screen and asked a
direct question. Answer in 3-6 sentences — the reply appears as a speech
bubble. Use Read/Grep/Glob on local files when helpful. For questions
about "inbox", "leads", "what am I working on", read
~/Downloads/duck_scratchpad/inbox.md and journal.md directly. NEVER
interpret "inbox" as Gmail unless the user explicitly says "email" or
"gmail"."""


def _read_tail(path: str, n: int = 600) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    except OSError:
        return ""
    return data[-n:] if len(data) > n else data


def _recent_events(n: int = 4) -> str:
    try:
        c = sqlite3.connect(bus.DB_PATH)
        rows = list(c.execute(
            "SELECT kind, priority, substr(data,1,120) FROM events "
            "WHERE status='triaged' ORDER BY id DESC LIMIT ?",
            (n,),
        ))
        c.close()
    except Exception:
        return "  (unavailable)"
    if not rows:
        return "  (none yet)"
    return "\n".join(f"  - [{p or '?'}] {k}: {d}" for k, p, d in reversed(rows))


def build_prompt(question: str) -> str:
    return f"""{TASK_HEADER}

# User asked:
{question.strip()}

# Context — user's current state

## Recent activity (last 4 events from bus, oldest first)
{_recent_events()}

## Watch targets (from ~/Downloads/duck_scratchpad/targets.md)
{_read_tail(TARGETS, 500).strip() or '(not set)'}

## Tail of the scratchpad inbox.md (your past reactions — this is NOT Gmail)
{_read_tail(INBOX, 500).strip() or '(empty)'}

---
Answer the question directly in 3-6 sentences."""


def call_claude(prompt: str) -> str:
    return session_client.ask(prompt, timeout=CLAUDE_TIMEOUT)


def write_speak(text: str, max_chars: int = 400):
    """Pet pops speech bubble. Keep readable."""
    if not text:
        return
    out = text if len(text) <= max_chars else text[:max_chars - 1].rsplit(" ", 1)[0] + "…"
    with open(SPEAK, "w", encoding="utf-8") as f:
        f.write(out)


def append_inbox(question: str, answer: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = (
        f"\n## {ts} — you asked the duck\n\n"
        f"**Q:** {question.strip()}\n\n"
        f"**A:** {answer.strip()}\n"
    )
    with open(INBOX, "a", encoding="utf-8") as f:
        f.write(block)


def main():
    os.makedirs(SCRATCHPAD, exist_ok=True)
    print(f"[ask_listener] starting at {datetime.now().isoformat()}")
    print(f"[ask_listener] watching {ASK} every {POLL_INTERVAL}s")
    while True:
        try:
            if os.path.exists(ASK):
                with open(ASK, "r", encoding="utf-8") as f:
                    question = f.read().strip()
                try:
                    os.remove(ASK)
                except OSError:
                    pass
                if question:
                    print(f"[ask_listener] Q: {question[:80]}")
                    # Tell pet we're thinking (quick placeholder bubble)
                    write_speak("thinking…")
                    answer = call_claude(build_prompt(question))
                    print(f"[ask_listener] A: {answer[:100]}")
                    write_speak(answer)
                    append_inbox(question, answer)
        except Exception as e:
            print(f"[ask_listener] error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
