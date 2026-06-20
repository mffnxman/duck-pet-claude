"""
Triage worker — runs Qwen locally to classify events.
Reads new events from bus, asks local LLM "boring/interesting/urgent",
writes back classification. Free — no Claude tokens spent.
"""
import os
import sys
import json
import time
import re

import bus

try:
    import ollama
except ImportError:
    print("[triage] missing 'ollama' package. pip install ollama")
    sys.exit(1)

# Prefer Qwen 2.5 7B; fall back to Gemma 2B if not pulled yet.
PREFERRED_MODELS = ["qwen2.5:7b", "gemma2:2b"]

SCRATCHPAD = os.path.expanduser("~/Downloads/duck_scratchpad")
INBOX = os.path.join(SCRATCHPAD, "inbox.md")
JOURNAL = os.path.join(SCRATCHPAD, "journal.md")
TARGETS = os.path.join(SCRATCHPAD, "targets.md")

SYSTEM = """You triage desktop events for an autonomous AI companion.
Classify each event into one of three buckets:

- urgent: something the user wants to be pulled out of flow for RIGHT NOW.
  A production-style error on a system they care about. A finding matching
  an EXPLICIT watch target in targets.md. A crash / alert / security signal
  while they're actively working on that thing.
- interesting: worth journaling but not interrupting for. File saves in
  projects they're working on, new info from tools they use, navigations
  to watch targets without errors.
- boring: routine activity. App switches, title changes inside the same
  app, idle noise, file saves to caches, debug console output, 3xx
  redirects, asset 404s (favicons, analytics pings), docs pages returning
  expected 401s for unauthenticated API explorers.

# HARD RULES

1. Bias HARD toward boring. Desktop activity is 90%+ noise.
2. DO NOT flag 'urgent' just because a domain NAME appears in targets.md
   — the event must be genuinely actionable (real error, real finding,
   real state change) AND match the user's current workflow context.
3. Public docs/reference pages throwing 401/403 on API explorers are
   EXPECTED behavior, not findings. Boring.
4. Watch-target matching: the event data must name the actual target,
   not just a substring of a generic domain. "example.com" doc page ≠
   a site you actually own.
5. If in doubt, boring.

Reply with strict JSON only:
{"priority": "urgent|interesting|boring", "reason": "one short sentence"}
"""


def _pick_model() -> str:
    client = ollama.Client()
    have = []
    try:
        listed = client.list()
        models = listed.models if hasattr(listed, "models") else []
        have = [m.model for m in models]
    except Exception as e:
        print(f"[triage] ollama not reachable: {e}")
        return PREFERRED_MODELS[-1]
    for m in PREFERRED_MODELS:
        if any(m in str(h) for h in have):
            return m
    return PREFERRED_MODELS[-1]


def _load_targets() -> str:
    try:
        with open(TARGETS, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _load_feedback(max_entries: int = 8) -> str:
    """Read recent user feedback (👍/👎 from the pet bubble) and format as
    few-shot calibration examples for the triage system prompt. Duck self-
    tunes over time — after a week of feedback, classifications shift to
    match what the user considers urgent vs noise."""
    path = os.path.expanduser("~/Downloads/duck_scratchpad/feedback.jsonl")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""
    entries = []
    for line in lines[-max_entries * 2:]:  # tail recent
        try:
            e = json.loads(line)
            if e.get("reaction") and e.get("verdict") in ("good", "noise"):
                entries.append(e)
        except Exception:
            continue
    if not entries:
        return ""
    out = ["\n# User calibration — recent 👍/👎 feedback on duck reactions:"]
    for e in entries[-max_entries:]:
        label = "USEFUL (urgent-ish worked)" if e["verdict"] == "good" else "NOISE (downgrade similar)"
        out.append(f"  - {label}: {e['reaction'][:160]}")
    out.append("Let these examples shift your 'urgent' vs 'boring' calls "
               "toward what the user actually values.")
    return "\n".join(out)


def classify(event: dict, model: str, targets: str) -> tuple[str, str]:
    """Ask local LLM to classify a single event."""
    client = ollama.Client()
    payload = {
        "source": event["source"],
        "kind": event["kind"],
        "data": json.loads(event["data"]) if isinstance(event["data"], str) else event["data"],
    }
    feedback = _load_feedback()
    user = (
        f"Watch targets / preferences:\n{targets}\n"
        f"{feedback}\n\n"
        f"Event:\n{json.dumps(payload, indent=2)}\n\n"
        f"Classify."
    )
    try:
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0.2, "num_predict": 120},
            format="json",
        )
        raw = resp["message"]["content"].strip()
        parsed = json.loads(raw)
        priority = parsed.get("priority", "boring").lower()
        if priority not in ("urgent", "interesting", "boring"):
            priority = "boring"
        reason = parsed.get("reason", "")[:300]
        return priority, reason
    except Exception as e:
        return "boring", f"triage_error: {e}"


def _journal_append(event: dict, priority: str, reason: str):
    line = (
        f"- `{time.strftime('%H:%M:%S', time.localtime(event['ts']))}` "
        f"**{priority}** `{event['source']}/{event['kind']}` — {reason}\n"
    )
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _inbox_append(event: dict, reason: str):
    """Urgent only — surface to the human."""
    data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
    block = (
        f"\n## 🦆 {time.strftime('%Y-%m-%d %H:%M', time.localtime(event['ts']))}"
        f" — {event['source']}/{event['kind']}\n"
        f"**Why:** {reason}\n\n"
        f"```json\n{json.dumps(data, indent=2)}\n```\n\n---\n"
    )
    try:
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError:
        pass


def _signature(ev: dict) -> str:
    """Rough semantic fingerprint of an event for dedupe.
    Same source+kind+primary-identifier = same signal."""
    try:
        data = json.loads(ev["data"]) if isinstance(ev["data"], str) else ev["data"]
    except Exception:
        data = {}
    key = (
        data.get("url")
        or data.get("path")
        or data.get("to_title")
        or data.get("title")
        or data.get("error_text")
        or ""
    )
    # Strip volatile bits (query strings, tokens)
    import re
    key = re.sub(r"[?#].*$", "", str(key))
    key = re.sub(r"[a-f0-9]{16,}", "X", key)
    return f"{ev['source']}|{ev['kind']}|{key[:120]}"


def _recent_urgent_signatures(window_sec: int = 1800) -> set[str]:
    """Signatures of events already classified urgent in the last N seconds."""
    import sqlite3
    cutoff = time.time() - window_sec
    try:
        c = sqlite3.connect(bus.DB_PATH)
        rows = c.execute(
            "SELECT source, kind, data FROM events "
            "WHERE priority='urgent' AND triaged_at > ?",
            (cutoff,),
        ).fetchall()
        c.close()
    except Exception:
        return set()
    sigs = set()
    for source, kind, data in rows:
        fake = {"source": source, "kind": kind, "data": data}
        sigs.add(_signature(fake))
    return sigs


def run_once(model: str | None = None) -> int:
    """Process all currently-untriaged events. Returns count."""
    bus.init()
    model = model or _pick_model()
    targets = _load_targets()
    recent_urgent = _recent_urgent_signatures()
    for ev in bus.pop_untriaged(limit=50):
        # Dedupe: if we already went urgent on this exact signal in the
        # last 30 min, don't wake the user again.
        sig = _signature(ev)
        if sig in recent_urgent:
            priority = "boring"
            reason = "duplicate of a recent urgent — suppressed"
        else:
            priority, reason = classify(ev, model, targets)
            if priority == "urgent":
                recent_urgent.add(sig)  # in-batch dedupe too
        bus.mark_triaged(ev["id"], priority, reason)
        _journal_append(ev, priority, reason)
        if priority == "urgent":
            _inbox_append(ev, reason)
    return 0 if not recent_urgent else len(recent_urgent)


def loop(interval: float = 5.0):
    model = _pick_model()
    print(f"[triage] using model: {model}")
    print(f"[triage] polling bus every {interval}s")
    while True:
        try:
            n = run_once(model)
            if n:
                print(f"[triage] processed {n} events")
        except KeyboardInterrupt:
            print("\n[triage] stopped")
            return
        except Exception as e:
            print(f"[triage] error: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        n = run_once()
        print(f"processed {n} events")
    else:
        loop()
