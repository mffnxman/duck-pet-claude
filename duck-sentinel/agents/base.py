"""
Agent base — shared lifecycle for duck-sentinel sub-agents.

Each agent is a long-running Python subprocess with a filesystem-based
state directory:

  ~/tools/duck-sentinel/agents/<agent_id>/
    meta.json        — type, mandate, spawn time, user-facing description
    pid.txt          — PID while running; deleted on exit
    log.txt          — agent's stdout/stderr
    findings.jsonl   — each "interesting" observation, one JSON per line
    kill.flag        — touch this to request graceful shutdown

Findings are ALSO pushed into the sentinel bus so the normal triage +
duck_brain pipeline picks them up (they become speech bubbles).

Agents are the "run a background task for me" primitive — like Devin's
watchers or Claude's Agent SDK but lightweight and local.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

# Locate the parent duck-sentinel dir so agents can import `bus` etc.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_THIS_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import bus  # noqa: E402

AGENTS_ROOT = os.path.join(_PARENT, "agents")


class Agent:
    """One sub-agent. Subclass or just call its helpers from a script.

    Typical usage inside an agent entry-point:

        agent = Agent.from_argv()
        while not agent.should_stop():
            try:
                agent.tick()
            except Exception as e:
                agent.log(f"error: {e}")
            time.sleep(agent.interval_sec)
        agent.finalize()
    """

    def __init__(self, agent_id: str, meta: dict):
        self.id = agent_id
        self.meta = meta
        self.type = meta.get("type", "unknown")
        self.mandate = meta.get("mandate", {})
        self.interval_sec = int(meta.get("interval_sec", 60))
        self.dir = os.path.join(AGENTS_ROOT, agent_id)
        os.makedirs(self.dir, exist_ok=True)
        self.log_path = os.path.join(self.dir, "log.txt")
        self.findings_path = os.path.join(self.dir, "findings.jsonl")
        self.kill_path = os.path.join(self.dir, "kill.flag")
        self.pid_path = os.path.join(self.dir, "pid.txt")
        self._write_pid()

    # ── filesystem helpers ──────────────────────

    def _write_pid(self):
        try:
            with open(self.pid_path, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass

    def should_stop(self) -> bool:
        return os.path.exists(self.kill_path)

    def log(self, msg: str):
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
        # Also to stdout so the parent log captures it
        print(line.rstrip())

    def finding(self, data: dict, urgent: bool = False):
        """Record an observation. Writes locally AND pushes to the sentinel
        bus so triage + duck_brain react normally (speech bubble on urgent).
        """
        record = {
            "ts": time.time(),
            "agent_id": self.id,
            "type": self.type,
            "mandate": self.mandate,
            "data": data,
            "urgent": urgent,
        }
        try:
            with open(self.findings_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            self.log(f"finding write failed: {e}")
        # Push to bus — triage will classify, brain may wake
        try:
            bus.push(f"agent:{self.type}", "finding", {
                "agent_id": self.id,
                "mandate": self.mandate,
                "data": data,
                "hint": "urgent" if urgent else "normal",
            })
        except Exception as e:
            self.log(f"bus push failed: {e}")

    def finalize(self):
        """Cleanup on exit."""
        try:
            os.remove(self.pid_path)
        except OSError:
            pass
        self.log("agent exiting")

    # ── construction ────────────────────────────

    @classmethod
    def from_argv(cls) -> "Agent":
        """Entry-point helper. Agent scripts are invoked as:
            python agents/foo.py <agent_id>
        and meta.json lives in the agent's dir.
        """
        if len(sys.argv) < 2:
            print("usage: <agent_script> <agent_id>")
            sys.exit(2)
        agent_id = sys.argv[1]
        meta_path = os.path.join(AGENTS_ROOT, agent_id, "meta.json")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return cls(agent_id, meta)
