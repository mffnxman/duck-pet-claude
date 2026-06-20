"""
Duck proactive — the "Clippy but good" worker.

Watches the bus for activity patterns and proactively pings the duck
(via speak.txt → pet bubble) when something is worth saying WITHOUT
waiting for an urgent event or a user question.

Triggers (all configurable):
  - morning_briefing: first meaningful activity after a >4h gap → duck
    reads inbox + targets + agents and gives a 3-bullet orientation
  - idle_check: if window_change events have stopped for >25 min but
    the machine isn't locked, ask "stuck? want me on something?"
  - agent_summary: every 2 hours if any agent has new findings, roll
    them up into a single bubble instead of N separate triage hits
  - end_of_day: at configurable hour (default 21:00) if user is still
    active, wrap up what happened today

Proactive messages go through duck_session (so they inherit persona +
tools) and land in speak.txt + inbox.md like any reaction.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

import bus
import session_client

try:
    import mobile_push
except ImportError:
    mobile_push = None

SCRATCHPAD = os.path.expanduser("~/Downloads/duck_scratchpad")
SPEAK = os.path.join(SCRATCHPAD, "speak.txt")
INBOX = os.path.join(SCRATCHPAD, "inbox.md")
STATE_FILE = os.path.join(SCRATCHPAD, "proactive_state.json")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_MGR = os.path.join(_THIS_DIR, "agent_manager.py")

POLL_INTERVAL = 60.0
IDLE_THRESHOLD_SEC = 25 * 60
MORNING_GAP_SEC = 4 * 3600
AGENT_ROLLUP_SEC = 2 * 3600
END_OF_DAY_HOUR = 21  # 9pm local
CHROME_PROMPT_COOLDOWN_SEC = 3600  # only prompt about CDP once per hour

# Duck Radar — daily trending-AI briefing config (from trend_sources.json)
try:
    import trend_collector as _tc

    _trend_cfg = _tc.load_sources()
except Exception:
    _trend_cfg = {}
TREND_BRIEF_HOUR = _trend_cfg.get("briefing_hour", 9)
TREND_BRIEF_MIN = _trend_cfg.get("briefing_minute", 30)


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict):
    os.makedirs(SCRATCHPAD, exist_ok=True)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def _latest_activity_ts(c: sqlite3.Connection) -> float:
    row = c.execute(
        "SELECT MAX(ts) FROM events WHERE source='window' AND kind IN "
        "('window_change','app_change','title_change')"
    ).fetchone()
    return row[0] or 0


def _prev_activity_ts(c: sqlite3.Connection, before_ts: float) -> float:
    """Timestamp of the last activity BEFORE a given event, for gap detection."""
    row = c.execute(
        "SELECT ts FROM events WHERE source='window' AND ts < ? "
        "ORDER BY ts DESC LIMIT 1 OFFSET 1",
        (before_ts,),
    ).fetchone()
    return row[0] if row else 0


def _read_tail(path: str, n: int = 800) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    except OSError:
        return ""
    return data[-n:] if len(data) > n else data


def _chrome_foregrounded_without_cdp(c: sqlite3.Connection) -> bool:
    """True if Chrome was foregrounded in the last 3 min AND CDP isn't up."""
    cutoff = time.time() - 180
    row = c.execute(
        "SELECT COUNT(*) FROM events WHERE source='window' AND ts > ? "
        "AND (data LIKE '%chrome.exe%' OR data LIKE '%Google Chrome%')",
        (cutoff,),
    ).fetchone()
    if not row or not row[0]:
        return False
    # CDP check — quick timeout
    import urllib.error, urllib.request

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:9222/json/version",
            timeout=1,
        ) as r:
            return r.status != 200
    except Exception:
        return True  # CDP not reachable = Chrome w/o debug port


def _agent_status_text() -> str:
    try:
        out = subprocess.run(
            [sys.executable, AGENT_MGR, "list"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        agents = json.loads(out.stdout) if out.stdout else []
    except Exception:
        return "(agents unavailable)"
    if not agents:
        return "no watchers running"
    running = [a for a in agents if a.get("running")]
    if not running:
        return "no active watchers"
    lines = []
    for a in running:
        lines.append(
            f"  - {a['id']}: {a['description']} " f"({a['findings']} findings)"
        )
    return "\n".join(lines)


def _speak(text: str):
    if not text:
        return
    os.makedirs(SCRATCHPAD, exist_ok=True)
    with open(SPEAK, "w", encoding="utf-8") as f:
        f.write(text.strip())


def _append_inbox(header: str, text: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = f"\n## {ts} — {header}\n\n{text.strip()}\n"
    try:
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError:
        pass


def _mobile(title: str, body: str, tier: str = "normal"):
    """Best-effort push to your phone via a synced folder.

    Tier guidance for proactive triggers:
      - morning_briefing → normal  (he's at desk; just an FYI on the phone)
      - idle_check       → low     (rare; mostly noise off-desk)
      - agent_rollup     → high    (skim-worthy; >=2 agent findings)
      - chrome_cdp_nudge → low     (machine-local, phone doesn't help)
      - critical agent finding (urgent=True elsewhere) → caller passes "critical"
    """
    if mobile_push is None:
        return
    try:
        mobile_push.push(title, body, tier=tier)
    except Exception as e:
        print(f"[proactive] mobile_push failed: {e}")


# ── Triggers ───────────────────────────────────────────


def maybe_morning_briefing(state: dict, c: sqlite3.Connection) -> bool:
    now = time.time()
    latest = _latest_activity_ts(c)
    if not latest:
        return False
    # Already briefed today?
    last_brief = state.get("last_morning_briefing", 0)
    if datetime.fromtimestamp(last_brief).date() == datetime.fromtimestamp(now).date():
        return False
    # Need a long gap before this activity to count as "morning"
    prev = _prev_activity_ts(c, latest)
    gap = latest - prev if prev else MORNING_GAP_SEC + 1
    if gap < MORNING_GAP_SEC:
        return False
    # And the activity must be recent (within last 5 min)
    if now - latest > 300:
        return False

    print("[proactive] triggering morning briefing")
    prompt = (
        "Morning briefing time. Read "
        "~/Downloads/duck_scratchpad/inbox.md (tail), targets.md, and "
        "give the user a 3-bullet orientation for today: "
        "(1) the hottest open thread from yesterday, "
        "(2) any agent finding he should glance at, "
        "(3) one concrete next action. "
        "Speech-bubble brief: ~60 words total. Casual partner voice — "
        "he just got to the desk, don't dump.\n\n"
        f"Running watchers:\n{_agent_status_text()}"
    )
    reply = session_client.ask(prompt, timeout=90)
    if reply and not reply.startswith("("):
        _speak(reply)
        _append_inbox("🦆 morning briefing", reply)
        _mobile("morning briefing", reply, tier="normal")
        state["last_morning_briefing"] = now
        _save_state(state)
        return True
    return False


def maybe_trend_briefing(state: dict, c: sqlite3.Connection) -> bool:
    """Duck Radar: once a day at/after the configured hour, deliver the
    blended trending-AI digest. Separate beat from the morning briefing.
    All output (bubble + inbox + phone push) is handled inside build_digest."""
    now = time.time()
    last = state.get("last_trend_briefing", 0)
    if (
        last
        and datetime.fromtimestamp(last).date() == datetime.fromtimestamp(now).date()
    ):
        return False
    nowdt = datetime.fromtimestamp(now)
    if (nowdt.hour, nowdt.minute) < (TREND_BRIEF_HOUR, TREND_BRIEF_MIN):
        return False
    # Only when the user is actually at the desk (recent window activity)
    latest = _latest_activity_ts(c)
    if not latest or now - latest > 600:
        return False

    print("[proactive] triggering Duck Radar trend briefing")
    res = None
    try:
        import trend_digest

        res = trend_digest.build_digest(
            fetch=True, speak=True, push=True, append_inbox=True
        )
    except Exception as e:
        print(f"[proactive] trend briefing failed: {e}")
    # Record the attempt regardless so a transient failure doesn't retry-spam.
    state["last_trend_briefing"] = now
    _save_state(state)
    return bool(res)


def maybe_idle_check(state: dict, c: sqlite3.Connection) -> bool:
    now = time.time()
    latest = _latest_activity_ts(c)
    if not latest:
        return False
    idle = now - latest
    if idle < IDLE_THRESHOLD_SEC:
        # Activity returned — reset cooldown
        state.pop("last_idle_check", None)
        return False
    # Don't re-ping within 1h of last idle check
    if now - state.get("last_idle_check", 0) < 3600:
        return False

    print(f"[proactive] idle {idle/60:.0f}m — triggering check-in")
    prompt = (
        f"the user has been idle for {idle/60:.0f} minutes (no window changes). "
        "He's probably still at the machine. Ask if he's stuck or wants "
        "you on something — ONE casual sentence. Don't list options. "
        "If he's been grinding on a specific target recently, reference "
        "it briefly. No preamble."
    )
    reply = session_client.ask(prompt, timeout=60)
    if reply and not reply.startswith("("):
        _speak(reply)
        _append_inbox("🦆 idle check-in", reply)
        _mobile("idle check-in", reply, tier="low")
        state["last_idle_check"] = now
        _save_state(state)
        return True
    return False


def maybe_agent_rollup(state: dict, c: sqlite3.Connection) -> bool:
    now = time.time()
    last = state.get("last_agent_rollup", 0)
    if now - last < AGENT_ROLLUP_SEC:
        return False
    # Count recent agent findings in bus since last rollup
    row = c.execute(
        "SELECT COUNT(*) FROM events WHERE source LIKE 'agent:%' AND ts > ?",
        (last if last else now - AGENT_ROLLUP_SEC,),
    ).fetchone()
    n = row[0] if row else 0
    if n < 2:
        # Not enough new agent activity to bother
        state["last_agent_rollup"] = now
        _save_state(state)
        return False

    print(f"[proactive] rolling up {n} agent findings")
    # Pull the raw findings
    rows = list(
        c.execute(
            "SELECT source, kind, data FROM events WHERE source LIKE 'agent:%' "
            "AND ts > ? ORDER BY ts DESC LIMIT 15",
            (last if last else now - AGENT_ROLLUP_SEC,),
        )
    )
    findings_blob = "\n".join(f"- {s} / {k}: {d[:200]}" for s, k, d in rows)
    prompt = (
        "Your sub-agents have reported new findings since the last rollup. "
        "Summarize in 2-3 sentences what the user should know — only surface "
        "the signal, skip routine status. If nothing's really worth his "
        "attention, say 'agents quiet, nothing new' and he'll move on.\n\n"
        f"Recent findings:\n{findings_blob}"
    )
    reply = session_client.ask(prompt, timeout=60)
    if reply and not reply.startswith("("):
        _speak(reply)
        _append_inbox("🦆 agent rollup", reply)
        # If any underlying finding was urgent, escalate to critical (phone push)
        urgent_hit = any("urgent" in (d or "").lower() for _, _, d in rows)
        tier = "critical" if urgent_hit else "high"
        _mobile(f"agent rollup ({n} findings)", reply, tier=tier)
    state["last_agent_rollup"] = now
    _save_state(state)
    return True


def maybe_chrome_cdp_nudge(state: dict, c: sqlite3.Connection) -> bool:
    """If Chrome is foregrounded but CDP isn't up, nudge once per hour."""
    now = time.time()
    last = state.get("last_chrome_nudge", 0)
    if now - last < CHROME_PROMPT_COOLDOWN_SEC:
        return False
    if not _chrome_foregrounded_without_cdp(c):
        return False

    print("[proactive] Chrome w/o CDP — nudging")
    msg = (
        "I can't see inside Chrome right now (CDP isn't up). "
        "Launch `~/tools/duck-sentinel/chrome-duck.bat` and I'll be "
        "able to read tabs, console, and network live. "
        "Or ignore this — I'll mention it again in an hour."
    )
    _speak(msg)
    _append_inbox("🦆 chrome CDP nudge", msg)
    state["last_chrome_nudge"] = now
    _save_state(state)
    return True


# ── Main loop ──────────────────────────────────────────


def main():
    bus.init()
    print(f"[proactive] starting at {datetime.now().isoformat()}")
    state = _load_state()
    while True:
        try:
            c = sqlite3.connect(bus.DB_PATH)
            # Fire at most one trigger per loop (avoid speech-bubble spam)
            if maybe_morning_briefing(state, c):
                pass
            elif maybe_trend_briefing(state, c):
                pass
            elif maybe_chrome_cdp_nudge(state, c):
                pass
            elif maybe_idle_check(state, c):
                pass
            elif maybe_agent_rollup(state, c):
                pass
            c.close()
        except Exception as e:
            print(f"[proactive] loop error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
