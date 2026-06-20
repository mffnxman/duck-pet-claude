"""
duck_memory — a tiny CLI the duck calls to log observations about the user.

The duck has MCP tools for reading local files, but `observe` is the
primitive that lets it CONTRIBUTE to an evolving user-model. Over time this
becomes a running log of what the duck has learned:

  - preferences ("prefers X over Y")
  - patterns ("checks the CI dashboard first thing every morning")
  - wins / losses ("shipped the migration on 2026-01-12")
  - inside references ("'rip it and dip it' means ship now")

Writes to persona/observations.md (local, gitignored). Your interactive
Claude can read the same file, so the duck and Claude build a shared
understanding of you across sessions.

Usage (called by the duck via Bash):

    python duck_memory.py observe "shipped the migration today"

    python duck_memory.py observe --category preference \\
        "prefers action over clarification, ship-first mentality"

    python duck_memory.py recent           # print last 10 observations
    python duck_memory.py search deploy    # grep observations
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Force UTF-8 stdout so we don't crash on em-dashes / emoji on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _persona_dir() -> str:
    try:
        with open(os.path.join(_THIS_DIR, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        cfg = {}
    return os.path.join(_THIS_DIR, cfg.get("persona_dir", "persona"))


MEMORY_FILE = os.path.join(_persona_dir(), "observations.md")

HEADER = """# Duck observations

Running log of what the duck (and your interactive Claude) have learned
about you. Entries are appended with date + category — do NOT rewrite,
only add. Read at boot so the duck knows you across sessions, not just
within a single conversation. Local-only (gitignored).

"""


def _ensure_file():
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    if not os.path.isfile(MEMORY_FILE):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(HEADER)


def cmd_observe(args):
    _ensure_file()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    cat = args.category or "note"
    entry = f"- **{ts}** [{cat}] — {args.text.strip()}\n"
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"logged [{cat}]: {args.text[:80]}")


def cmd_recent(args):
    _ensure_file()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = [l for l in lines if l.strip().startswith("- **")]
    for l in entries[-args.n :]:
        print(l.rstrip())


def cmd_search(args):
    _ensure_file()
    needle = args.term.lower()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if needle in line.lower() and line.strip().startswith("- **"):
                print(line.rstrip())


def main():
    p = argparse.ArgumentParser(description="duck shared-memory helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("observe", help="append an observation")
    o.add_argument("text", help="what you observed")
    o.add_argument(
        "--category",
        default="note",
        help="preference | pattern | win | loss | note (default note)",
    )
    o.set_defaults(func=cmd_observe)

    r = sub.add_parser("recent", help="print last N observations")
    r.add_argument("-n", type=int, default=10)
    r.set_defaults(func=cmd_recent)

    s = sub.add_parser("search", help="grep observations")
    s.add_argument("term")
    s.set_defaults(func=cmd_search)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
