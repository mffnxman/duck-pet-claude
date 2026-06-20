"""
trend_digest.py — Duck Radar's curation + delivery layer.

Takes the token-free Reddit snapshot from trend_collector, hands it to the
warm Claude session (which adds TikTok/creator signal via web search), and
turns it into ONE blended, tagged, deduped digest:

  - trends.md   the full shareable board (🔧 Builder / 🎬 Creator, why-it-matters)
  - speak.txt   a ~60-word "what's new since yesterday" bubble
  - inbox.md    appended like any other duck reaction

Used by BOTH the daily proactive briefing and the `/duck trends` command.
Degrades gracefully: if the warm session is down, it still emits a basic
Reddit-only board so the feature never hard-fails.

    python trend_digest.py --cli                 # on-demand, print + speak
    python trend_digest.py --cli --lane builder  # one lane only
    python trend_digest.py --cli --refresh       # force a re-fetch first

Importable:  build_digest(...) -> dict | None
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

import trend_collector
import session_client

try:
    import mobile_push
except ImportError:
    mobile_push = None

SCRATCHPAD = os.path.expanduser("~/Downloads/duck_scratchpad")
TRENDS_MD = os.path.join(SCRATCHPAD, "trends.md")
SPEAK = os.path.join(SCRATCHPAD, "speak.txt")
INBOX = os.path.join(SCRATCHPAD, "inbox.md")
SEEN_FILE = os.path.join(SCRATCHPAD, "trend_seen.json")

SEEN_TTL_DAYS = 14  # forget an item's "seen" status after two weeks


# ── seen-memory (dedupe so the daily brief only highlights what's NEW) ──


def _load_seen() -> dict:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_seen(seen: dict):
    now = datetime.now().timestamp()
    cutoff = now - SEEN_TTL_DAYS * 86400
    seen = {k: v for k, v in seen.items() if v > cutoff}
    os.makedirs(SCRATCHPAD, exist_ok=True)
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, indent=2)
    except OSError:
        pass


def _key(item: dict) -> str:
    return (
        f"reddit:{item.get('id')}"
        if item.get("id")
        else f"reddit:{item.get('subreddit')}:{item.get('title', '')[:60]}"
    )


# ── prompt construction ────────────────────────────────


def _compose_prompt(snap: dict, new_keys: set, lane: str | None) -> str:
    items = snap.get("reddit_items", [])
    cap = snap.get("max_items_per_lane", 12)
    web_q = snap.get("web_queries", {})

    lanes = [lane] if lane else ["builder", "creator"]
    blocks = []
    for ln in lanes:
        picked = [i for i in items if i["lane"] == ln][:cap]
        lines = []
        for i in picked:
            new_flag = " [NEW]" if _key(i) in new_keys else ""
            lines.append(
                f"- (r/{i['subreddit']} #{i.get('rank', '?')})"
                f"{new_flag} {i['title']}"
            )
        blocks.append(f"### {ln.upper()} — Reddit\n" + ("\n".join(lines) or "(none)"))

    reddit_blob = "\n\n".join(blocks)

    want_creator = lane in (None, "creator")
    want_builder = lane in (None, "builder")
    search_lines = []
    if want_creator:
        for q in web_q.get("creator", []):
            search_lines.append(f'  - "{q}"  (TikTok / creator side)')
    if want_builder:
        for q in web_q.get("builder", []):
            search_lines.append(f'  - "{q}"  (builder side)')
    search_blob = "\n".join(search_lines) or "  (none configured)"

    today = datetime.now().strftime("%B %d, %Y")
    return f"""You are generating today's "Duck Radar" — a blended digest of what's \
trending in AI, for your human partner. Two lanes:
  🔧 Builder  = what people are SHIPPING (models, agents, local LLM, benchmarks, policy)
  🎬 Creator  = what people are MAKING (AI video/image tools, viral formats, content money plays)

Below are the top AI posts pulled from Reddit today (token-free). Items tagged \
[NEW] haven't appeared in a prior digest.

{reddit_blob}

To cover the TikTok / creator side (no easy API), use your web search tool on \
these queries and fold the best findings in:
{search_blob}
If you have no web search tool, infer the creator side from the \
StableDiffusion/aivideo/midjourney Reddit items above and say so briefly.

Produce the digest. Rules:
- Curate hard — pick the ~5-7 genuinely notable items PER LANE, not everything. \
Merge duplicates (the same model/tool showing up twice = one bullet).
- Each bullet: **bold the subject**, then ONE line on why it matters. Add a \
source hint (e.g. · r/LocalLLaMA or · TikTok) and 🆕 if it's new/this-week.
- Keep it skimmable and screenshot-worthy. No preamble, no fluff.

Return EXACTLY this structure and nothing else:

===TRENDS_MD===
# 🛰️ Duck Radar — AI · {today}

## 🔧 Builder
- ...

## 🎬 Creator
- ...
===SPOKEN===
<a ~55-word spoken bubble summarizing what's genuinely NEW since yesterday, \
casual partner voice, lead with the single hottest thing>
"""


def _parse_reply(reply: str) -> tuple[str, str]:
    """Split the model reply into (markdown_body, spoken). Tolerant of drift."""
    md, spoken = "", ""
    if "===TRENDS_MD===" in reply:
        after = reply.split("===TRENDS_MD===", 1)[1]
        if "===SPOKEN===" in after:
            md, spoken = after.split("===SPOKEN===", 1)
        else:
            md = after
    else:
        # Model ignored the markers — best effort: whole thing is the board,
        # first paragraph becomes the bubble.
        md = reply
        spoken = reply.strip().split("\n\n", 1)[0]
    return md.strip(), spoken.strip()


def _fallback_md(snap: dict, lane: str | None, new_keys: set) -> tuple[str, str]:
    """No warm session → emit a basic Reddit-only board so we never hard-fail."""
    items = snap.get("reddit_items", [])
    cap = snap.get("max_items_per_lane", 12)
    today = datetime.now().strftime("%B %d, %Y")
    out = [
        f"# 🛰️ Duck Radar — AI · {today}",
        "",
        "_(offline mode: warm session down — Reddit-only, uncurated)_",
    ]
    lane_meta = [("builder", "🔧 Builder"), ("creator", "🎬 Creator")]
    new_count = 0
    for ln, head in lane_meta:
        if lane and ln != lane:
            continue
        out += ["", f"## {head}"]
        picked = [i for i in items if i["lane"] == ln][:cap]
        if not picked:
            out.append("- (nothing fetched)")
        for i in picked:
            flag = " 🆕" if _key(i) in new_keys else ""
            if _key(i) in new_keys:
                new_count += 1
            out.append(f"- **{i['title']}**{flag} · r/{i['subreddit']}")
    md = "\n".join(out)
    spoken = (
        f"AI radar (offline): {new_count} new items across the feeds. "
        "Warm session was down so it's the raw Reddit cut — "
        "run `/duck trends refresh` once I'm back up for the full board."
    )
    return md, spoken


# ── output side effects ────────────────────────────────


def _write(path: str, text: str):
    os.makedirs(SCRATCHPAD, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")


def _append_inbox(text: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts} — 🛰️ AI radar\n\n{text.strip()}\n")
    except OSError:
        pass


# ── main entry ─────────────────────────────────────────


def build_digest(
    fetch="auto",
    lane: str | None = None,
    *,
    speak: bool = True,
    push: bool = False,
    append_inbox: bool = True,
    print_md: bool = False,
) -> dict | None:
    """Generate the Duck Radar digest and deliver it.

    fetch: True (always), False (use cached), or "auto" (refetch if stale).
    Returns {"spoken", "md", "new_count"} or None on total failure.
    """
    snap = trend_collector.load_raw()
    need = (fetch is True) or (fetch == "auto" and _is_stale(snap)) or snap is None
    if need:
        snap = trend_collector.collect()
    if not snap:
        return None

    seen = _load_seen()
    cur_keys = {_key(i) for i in snap.get("reddit_items", [])}
    new_keys = {k for k in cur_keys if k not in seen}

    reply = session_client.ask(_compose_prompt(snap, new_keys, lane), timeout=200)
    if reply and not reply.startswith("(") and "===" in reply:
        md, spoken = _parse_reply(reply)
        if not md:
            md, spoken = _fallback_md(snap, lane, new_keys)
    else:
        md, spoken = _fallback_md(snap, lane, new_keys)

    new_count = len(new_keys)
    _write(TRENDS_MD, md)
    if speak and spoken:
        _write(SPEAK, spoken)
    if append_inbox and spoken:
        _append_inbox(spoken)
    if push and mobile_push is not None and spoken:
        try:
            mobile_push.push(f"AI radar ({new_count} new)", spoken, tier="normal")
        except Exception as e:
            print(f"[radar] mobile_push failed: {e}", file=sys.stderr)

    # Only mark items seen on a full (both-lane) run, so a lane-filtered
    # pull doesn't silently "consume" the other lane's NEW flags.
    if lane is None:
        now = datetime.now().timestamp()
        for k in cur_keys:
            seen[k] = now
        _save_seen(seen)

    if print_md:
        print(md)
    return {"spoken": spoken, "md": md, "new_count": new_count}


def _is_stale(snap: dict | None) -> bool:
    if not snap:
        return True
    try:
        cfg = trend_collector.load_sources()
        ttl_min = cfg.get("refetch_after_minutes", 180)
        collected = datetime.fromisoformat(snap["collected_at"])
        return (datetime.now() - collected).total_seconds() > ttl_min * 60
    except Exception:
        return True


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Duck Radar digest")
    ap.add_argument(
        "--cli", action="store_true", help="on-demand mode: print the board to stdout"
    )
    ap.add_argument("--lane", choices=["builder", "creator"], default=None)
    ap.add_argument("--refresh", action="store_true", help="force a re-fetch")
    ap.add_argument("--no-speak", action="store_true")
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    res = build_digest(
        fetch=True if args.refresh else "auto",
        lane=args.lane,
        speak=not args.no_speak,
        push=args.push,
        append_inbox=True,
        print_md=args.cli,
    )
    if res is None:
        print("[radar] could not build digest (no sources reachable)", file=sys.stderr)
        sys.exit(1)
    if not args.cli:
        print(f"[radar] {res['new_count']} new · wrote {TRENDS_MD}")


if __name__ == "__main__":
    main()
