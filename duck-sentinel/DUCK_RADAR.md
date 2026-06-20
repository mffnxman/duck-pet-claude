# 🛰️ Duck Radar

A daily, blended **"what's trending in AI"** digest for the duck — one feed
that mixes what people are **shipping** (models, agents, local LLM) with what
people are **making** (AI video/image tools, viral formats). Delivered as a
morning speech-bubble briefing and on demand via `/duck trends`.

## Why

The duck is a general-purpose desk partner. "What's hot in AI right now" is
split across two very different worlds — Reddit (builder signal) and
TikTok (creator signal). Duck Radar fuses them into one skimmable board so
you stay current without doom-scrolling either.

## Architecture

```
trend_collector.py     →  trend_raw.json  →  trend_digest.py (warm session)  →  trends.md  ──┬─→ daily briefing (duck_proactive)
  Reddit RSS (free)         raw snapshot       curate: tag 🔧/🎬, dedup, +TikTok    shareable    └─→ /duck trends (on demand)
  combined feed/lane                           via web search, "what's NEW"          board
  $0, token-free                               ~1 Claude call per briefing
```

- **Collection is $0** — Reddit's public Atom/RSS feeds, one combined
  multi-sub request per lane, browser UA, 429 backoff. (The old `.json`
  trick is CDN-blocked now; RSS still serves.) XML parsed with `defusedxml`.
- **Curation touches Claude once per briefing / per pull** — same cost
  profile as the morning briefing. TikTok (no easy API) is covered by the
  warm session's web-search tool at curate time.
- **Degrades gracefully** — if the warm session is down, a basic Reddit-only
  board is emitted so the feature never hard-fails.

## Files

| File | Role |
|---|---|
| `trend_sources.json` | Editable config: builder/creator subreddits, TikTok web queries, briefing time. |
| `trend_collector.py` | Token-free Reddit RSS fetcher → `trend_raw.json`. |
| `trend_digest.py` | Curates raw → `trends.md` + spoken summary; dedupes via `trend_seen.json`. Used by both delivery paths. |
| `duck_proactive.py` | `maybe_trend_briefing()` — daily trigger at the configured hour. |
| `duck.sh` / `/duck` | `/duck trends [refresh\|builder\|creator]` command. |

State (scratchpad): `trend_raw.json`, `trends.md` (the artifact), `trend_seen.json` (dedupe memory).

## Delivery

- **Daily briefing** — once/day at `briefing_hour:briefing_minute` (default
  09:30) when you're active. Speaks the "what's new since yesterday" cut →
  bubble + inbox + phone push (`tier=normal`).
- **`/duck trends`** — on demand: regenerate, print the board, speak it.
  `refresh` forces a re-fetch; `builder`/`creator` filter the lane.

## Config (`trend_sources.json`)

```json
{
  "reddit": {
    "builder": ["LocalLLaMA", "singularity", "AI_Agents", "MachineLearning", "ClaudeAI"],
    "creator": ["StableDiffusion", "aivideo", "ChatGPT", "midjourney"]
  },
  "web_queries": {
    "creator": ["trending AI on TikTok this week", "viral AI video tool this week"],
    "builder": ["biggest open-source LLM or AI model release this week"]
  },
  "briefing_hour": 9,
  "briefing_minute": 30
}
```

## Dependencies

- `requests` (already used by the agent framework)
- `defusedxml` (hardened XML parsing; falls back to stdlib if absent)
