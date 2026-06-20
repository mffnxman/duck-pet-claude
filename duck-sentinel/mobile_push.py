"""
mobile_push — surface high-signal duck events to your phone. OPTIONAL.

Disabled by default. To turn it on, edit config.json:

  "mobile_push": {
    "enabled": true,
    "vault_dir": "~/Notes/inbox",     # a folder that syncs to your phone
    "env_file": "~/.duck/mail.env"    # optional, for critical-tier email push
  }

Two channels:
  1. Synced-folder inbox  (vault_dir/duck-mobile-inbox.md)
     Drop the file in any folder that syncs to your phone (Obsidian +
     cloud sync, Syncthing, a Drive/Dropbox folder, etc.). Always on once
     vault_dir is set; free and silent.
  2. Email push           (uses SMTP creds from env_file if present)
     Reserved for tier="critical". Phones treat inbound mail as a push.

Tier policy:
  low      → vault only
  normal   → vault only
  high     → vault + (deduped) vault summary
  critical → vault + email

Usage from duck modules:
    import mobile_push
    mobile_push.push("morning briefing", "today: 3 hot threads", tier="normal")

Standalone test:
    python mobile_push.py "test event" "this is a test body" --tier critical
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _config() -> dict:
    try:
        with open(os.path.join(_THIS_DIR, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("mobile_push", {})
    except (OSError, json.JSONDecodeError):
        return {}


_MP = _config()
ENABLED = bool(_MP.get("enabled", False))
VAULT = (
    Path(os.path.expanduser(_MP.get("vault_dir", ""))) if _MP.get("vault_dir") else None
)
INBOX_FILE = (VAULT / "duck-mobile-inbox.md") if VAULT else None
DEDUP_FILE = (VAULT / ".duck-mobile-dedup.json") if VAULT else None
ENV_FILE = Path(os.path.expanduser(_MP["env_file"])) if _MP.get("env_file") else None

TIER_ICON = {"low": "·", "normal": "•", "high": "⚠️", "critical": "🔥"}
TIER_RANK = {"low": 0, "normal": 1, "high": 2, "critical": 3}
DEDUP_TTL_SEC = 6 * 3600  # don't re-push the same event title within 6h


def _load_env() -> dict:
    cfg = {}
    if ENV_FILE and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("SMTP_USER", "SMTP_APP_PASSWORD", "ALERT_TO"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def _load_dedup() -> dict:
    try:
        return json.loads(DEDUP_FILE.read_text())
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _save_dedup(state: dict):
    try:
        VAULT.mkdir(parents=True, exist_ok=True)
        DEDUP_FILE.write_text(json.dumps(state, indent=2))
    except (OSError, AttributeError):
        pass


def _is_duplicate(title: str, body: str) -> bool:
    state = _load_dedup()
    key = title.strip().lower()[:120]
    last = state.get(key)
    now = datetime.now().timestamp()
    if last and now - last < DEDUP_TTL_SEC:
        return True
    state[key] = now
    # Prune stale keys to prevent unbounded growth
    cutoff = now - DEDUP_TTL_SEC * 4
    state = {k: v for k, v in state.items() if v > cutoff}
    _save_dedup(state)
    return False


def _append_vault(title: str, body: str, tier: str) -> bool:
    """Write to the synced mobile inbox. Newest entries at top of section."""
    if not VAULT:
        return False
    try:
        VAULT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[mobile_push] vault unreachable: {e}")
        return False
    icon = TIER_ICON.get(tier, "•")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"### {icon} {ts} — {title}\n\n{body.strip()}\n\n---\n\n"

    header = (
        "# Duck Mobile Inbox\n\n"
        "_Auto-pushed by duck-sentinel. Newest at top._\n"
        "_Tiers: 🔥 critical · ⚠️ high · • normal · · low_\n\n"
        "---\n\n"
    )
    existing = ""
    if INBOX_FILE.exists():
        try:
            existing = INBOX_FILE.read_text(encoding="utf-8")
            if existing.startswith("# Duck Mobile Inbox"):
                marker = "\n---\n\n"
                idx = existing.find(marker)
                if idx > 0:
                    existing = existing[idx + len(marker) :]
        except OSError:
            existing = ""

    try:
        INBOX_FILE.write_text(header + entry + existing, encoding="utf-8")
        return True
    except OSError as e:
        print(f"[mobile_push] vault write failed: {e}")
        return False


def _send_email(title: str, body: str) -> bool:
    cfg = _load_env()
    if not all(k in cfg for k in ("SMTP_USER", "SMTP_APP_PASSWORD", "ALERT_TO")):
        print("[mobile_push] no SMTP creds — vault-only push")
        return False
    host = cfg.get("SMTP_HOST", "smtp.gmail.com")
    port = int(cfg.get("SMTP_PORT", 465))
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[duck 🔥] {title}"
    msg["From"] = cfg["SMTP_USER"]
    msg["To"] = cfg["ALERT_TO"]
    plain = f"{title}\n\n{body}\n\n— duck-sentinel · {datetime.now().strftime('%b %d %I:%M %p')}"
    html = (
        "<html><body style='font-family:-apple-system,sans-serif;background:#0a0b14;color:#e4e6eb;padding:18px;'>"
        f"<h2 style='color:#ff3b30;margin:0 0 8px 0'>🔥 {title}</h2>"
        f"<p style='color:#94a3b8;margin:0 0 18px 0;font-size:12px'>"
        f"{datetime.now().strftime('%B %d, %Y · %I:%M %p')}</p>"
        f"<div style='white-space:pre-wrap;line-height:1.5'>{body}</div>"
        "<p style='color:#64748b;font-size:11px;margin-top:24px'>"
        "duck-sentinel · CRITICAL tier escalation</p></body></html>"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            s.login(cfg["SMTP_USER"], cfg["SMTP_APP_PASSWORD"])
            s.sendmail(cfg["SMTP_USER"], [cfg["ALERT_TO"]], msg.as_string())
        return True
    except Exception as e:
        print(f"[mobile_push] SMTP send failed: {e}")
        return False


def push(title: str, body: str, tier: str = "normal", *, force: bool = False) -> dict:
    """Send a mobile-bound push. No-op unless enabled in config.json.

    Returns {"vault": bool, "email": bool, "deduped": bool}
    """
    tier = tier.lower()
    if tier not in TIER_RANK:
        tier = "normal"

    result = {"vault": False, "email": False, "deduped": False}
    if not ENABLED or not VAULT:
        return result  # disabled — silent no-op

    if not force and _is_duplicate(title, body):
        result["deduped"] = True
        print(f"[mobile_push] deduped: {title}")
        return result

    result["vault"] = _append_vault(title, body, tier)
    if tier == "critical":
        result["email"] = _send_email(title, body)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title")
    ap.add_argument("body")
    ap.add_argument("--tier", default="normal", choices=list(TIER_RANK))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    res = push(args.title, args.body, tier=args.tier, force=args.force)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
