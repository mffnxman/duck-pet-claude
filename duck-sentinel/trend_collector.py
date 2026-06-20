"""
trend_collector.py — Duck Radar's cheap, token-free fetch layer.

Pulls top posts from the AI subreddits in trend_sources.json via Reddit's
public Atom/RSS feeds (no auth, no API key, zero Claude tokens). Reddit
403-blocks the old `.json` trick at the CDN now, but the RSS feeds still
serve — and they support combined multi-sub feeds (r/a+b+c/top/.rss), so
each lane is ONE request. RSS has no score number, but `top/.rss?t=day` is
already ranked, so feed order carries the signal.

TikTok / creator-side signal is NOT fetched here — it's gathered at curate
time by the warm Claude session (see trend_digest.py), because TikTok has
no easy public API and web search lives in the session's toolbelt.

Output: trend_raw.json in the scratchpad.

    python trend_collector.py            # fetch + print a summary
    python trend_collector.py --json     # fetch + dump raw json

Importable:  collect() -> dict
"""

import json
import os
import sys
import time
from datetime import datetime

import requests

# defusedxml hardens against XXE / billion-laughs. Fall back to stdlib so the
# live duck keeps working before the dep is installed (see requirements).
try:
    from defusedxml.ElementTree import fromstring as xml_fromstring
except ImportError:
    from xml.etree.ElementTree import fromstring as xml_fromstring

MAX_FEED_BYTES = 5_000_000  # guard against an oversized/hostile response body

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(_THIS_DIR, "trend_sources.json")
SCRATCHPAD = os.path.expanduser("~/Downloads/duck_scratchpad")
RAW_FILE = os.path.join(SCRATCHPAD, "trend_raw.json")

# Browser UA — Reddit blocks the default python-requests UA outright.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def load_sources() -> dict:
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _lane_url(subs: list[str], listing: str, t: str, limit: int) -> str:
    combined = "+".join(subs)
    if listing == "top":
        return f"https://www.reddit.com/r/{combined}/top/.rss" f"?t={t}&limit={limit}"
    if listing in ("hot", "new", "rising", "controversial"):
        return f"https://www.reddit.com/r/{combined}/{listing}/.rss?limit={limit}"
    return f"https://www.reddit.com/r/{combined}/top/.rss?t={t}&limit={limit}"


def _get_with_backoff(url: str, tries: int = 5):
    """GET that rides out Reddit's per-IP RSS rate limiting (429)."""
    for attempt in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            if r.status_code == 200:
                if len(r.content) > MAX_FEED_BYTES:
                    print(
                        f"[collector] oversized feed ({len(r.content)}B) — skipping",
                        file=sys.stderr,
                    )
                    return None
                return r
            if r.status_code == 429:
                time.sleep(3 + attempt * 3)
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            if attempt == tries - 1:
                print(f"[collector] fetch failed: {e}", file=sys.stderr)
                return None
            time.sleep(2 + attempt)
    return None


def _parse_feed(xml_text: str, lane: str, limit: int) -> list[dict]:
    try:
        root = xml_fromstring(xml_text)
    except Exception as e:  # ParseError, or defusedxml entity-forbidden errors
        print(f"[collector] parse error ({lane}): {e}", file=sys.stderr)
        return []
    items = []
    for rank, e in enumerate(root.findall("a:entry", ATOM), start=1):
        raw_id = e.findtext("a:id", "", ATOM)  # e.g. t3_1uagwhk
        post_id = raw_id.split("_", 1)[1] if "_" in raw_id else raw_id
        cat = e.find("a:category", ATOM)
        sub = cat.get("term") if cat is not None else ""
        link = e.find("a:link", ATOM)
        href = link.get("href") if link is not None else ""
        author = e.findtext("a:author/a:name", "", ATOM)
        items.append(
            {
                "source": "reddit",
                "lane": lane,
                "subreddit": sub,
                "id": post_id,
                "title": (e.findtext("a:title", "", ATOM) or "").strip(),
                "url": href,
                "permalink": href,
                "author": author,
                "rank": rank,
                "updated": e.findtext("a:updated", "", ATOM),
            }
        )
    return items[:limit]


def collect() -> dict:
    """Fetch all configured subreddits (one combined feed per lane).
    Returns the raw snapshot dict and writes it to trend_raw.json."""
    cfg = load_sources()
    listing = cfg.get("reddit_listing", "top")
    t = cfg.get("reddit_time", "day")
    per_sub = cfg.get("reddit_limit_per_sub", 10)
    reddit_cfg = cfg.get("reddit", {})

    items: list[dict] = []
    lanes = [ln for ln in ("builder", "creator") if reddit_cfg.get(ln)]
    for idx, lane in enumerate(lanes):
        subs = reddit_cfg[lane]
        feed_limit = min(50, max(10, per_sub * len(subs)))
        if idx > 0:
            time.sleep(2)  # be polite between lane requests
        r = _get_with_backoff(_lane_url(subs, listing, t, feed_limit))
        if r is not None:
            items += _parse_feed(r.text, lane, feed_limit)

    snapshot = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "listing": listing,
        "time_window": t,
        "reddit_items": items,
        "web_queries": cfg.get("web_queries", {}),
        "max_items_per_lane": cfg.get("max_items_per_lane", 12),
    }

    os.makedirs(SCRATCHPAD, exist_ok=True)
    try:
        with open(RAW_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
    except OSError as e:
        print(f"[collector] could not write {RAW_FILE}: {e}", file=sys.stderr)

    return snapshot


def load_raw() -> dict | None:
    """Load the last snapshot without re-fetching. None if missing."""
    try:
        with open(RAW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    snap = collect()
    if "--json" in sys.argv:
        print(json.dumps(snap, indent=2))
        return
    items = snap["reddit_items"]
    b = sum(1 for i in items if i["lane"] == "builder")
    c = sum(1 for i in items if i["lane"] == "creator")
    print(
        f"[collector] {len(items)} items "
        f"({b} builder / {c} creator) @ {snap['collected_at']}"
    )
    for i in items[:14]:
        tag = "🔧" if i["lane"] == "builder" else "🎬"
        print(f"  {tag} r/{i['subreddit']:<15} #{i['rank']:<2} {i['title'][:66]}")


if __name__ == "__main__":
    main()
