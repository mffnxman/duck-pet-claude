"""
Client for duck_session.py — HTTP POST to the warm Claude session.

Callers use `ask(prompt, timeout)` and get back text. Falls back to a
fresh `claude -p` subprocess if the session is down, so the pipeline
never hard-breaks.
"""
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

SESSION_URL = "http://127.0.0.1:7717/"
HEALTH_URL = "http://127.0.0.1:7717/health"


def _claude_bin() -> str:
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def session_alive() -> bool:
    """True if the HTTP endpoint responds. 'Alive' from the session means the
    subprocess is running (may still be doing hook init on first call)."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
            return r.status == 200 and json.loads(r.read()).get("alive", False)
    except Exception:
        return False


def ask_session(prompt: str, timeout: float = 120.0) -> str:
    body = json.dumps({"prompt": prompt, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(
        SESSION_URL, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # HTTP read timeout — give a small buffer beyond the duck session timeout
    with urllib.request.urlopen(req, timeout=timeout + 10) as r:
        return json.loads(r.read()).get("text", "")


def ask_subprocess(prompt: str, system: str | None = None,
                   timeout: float = 120.0) -> str:
    """Fallback path — cold-spawn claude per call."""
    cmd = [_claude_bin(), "-p"]
    if system:
        cmd += ["--append-system-prompt", system]
    cmd.append(prompt)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        return "(duck brain timed out)"
    if r.returncode != 0:
        return f"(duck error: {(r.stderr or r.stdout)[:200]})"
    return (r.stdout or "").strip()


def ask(prompt: str, fallback_system: str | None = None,
        timeout: float = 120.0) -> str:
    """Prefer warm session; fall back to cold subprocess. Both return text."""
    if session_alive():
        try:
            return ask_session(prompt, timeout=timeout)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[session_client] session call failed: {e} — falling back")
    return ask_subprocess(prompt, system=fallback_system, timeout=timeout)
