# 🦆 Duck Sentinel

The [Duck Pet](../README.md) grew a brain. Duck Sentinel turns the desktop
pet into a **JARVIS-style co-pilot**: a cheap, always-on observation layer
runs 24/7 on your machine, a local LLM triages everything for free, and
Claude Code only wakes up when something actually matters — then talks to
you through the duck's speech bubble.

> Local-first. The watching + triage cost **$0** (no cloud tokens). Claude
> is only spent on the handful of things worth your attention.

```
sentinel.py    →  bus.db  →  triage.py (local LLM, free)  →  duck_brain.py (Claude, only on urgent)
 windows +        sqlite     classifies every event as       short actionable reaction
 files +          event      boring | interesting | urgent   → speech bubble + inbox.md
 browser          queue
```

## ✨ Highlights

- **Wakes only when it matters** — a local model (Qwen/Ollama) classifies every
  window/file/browser event; Claude is invoked only on `urgent`.
- **Acts, doesn't narrate** — a warm Claude Code session with Read/Bash/Edit/Write
  + live Chrome (DevTools Protocol). It *does* the thing, then reports.
- **🛰️ Duck Radar** — a daily, blended *"what's trending in AI"* digest:
  what people are **shipping** (models, agents, local LLM) + what people are
  **making** (AI video/content, viral tools). See [DUCK_RADAR.md](DUCK_RADAR.md).
- **Proactive, not annoying** — morning briefing, idle check-ins, agent
  roll-ups, and the Radar — one bubble at a time, never spam.
- **Sub-agents** — tell it "watch this URL / tail that log" and it spawns a
  background watcher that pings you on changes.
- **Knows you** — drop context files in `persona/` and it talks to you like a
  partner, not a cold assistant. It even appends its own learnings over time.
- **Phone push** (optional) — high-signal events to a synced folder / email.

## Requirements

- **Windows 10/11**, **Python 3.10+**
- **Claude Code** — [claude.ai/download](https://claude.ai/download)
- A **local LLM** for triage (e.g. [Ollama](https://ollama.com) running a small
  model). Triage degrades gracefully if it's not up.
- `pip install -r requirements.txt`

## Quick start

```bash
pip install -r requirements.txt
cp persona/example-context.md persona/me.md   # then edit it (optional but nice)
./duck.sh start                                # launches the full stack + pet + status panel
```

Stop with `./duck.sh stop`. Your curated feed lands in
`~/Downloads/duck_scratchpad/inbox.md`.

### Commands

```
./duck.sh                 # status
./duck.sh start | stop | restart
./duck.sh ask "..."       # ask the duck; it answers in the bubble
./duck.sh trends          # 🛰️ Duck Radar — trending-AI digest
./duck.sh watchers        # list running sub-agents
./duck.sh logs            # tail all worker logs
```

### Browser awareness (optional)

Run `chrome-duck.bat` to launch Chrome with the DevTools Protocol enabled
(separate profile — your normal Chrome is untouched). Then the duck can read
your live tabs, console, and network.

## Configuration

Everything personal lives in `config.json` and `persona/` — both kept out of
git by default:

```json
{
  "user_name": "boss",
  "persona_dir": "persona",
  "watch_dirs": ["~/Downloads/duck_scratchpad"],
  "mobile_push": { "enabled": false, "vault_dir": "", "env_file": "" }
}
```

- **`user_name`** — what the duck calls you.
- **`watch_dirs`** — folders to watch for file activity (add your project dirs).
- **`persona/`** — drop `*.md` files describing you; they load into the duck's
  system prompt. See [persona/README.md](persona/README.md).
- **`mobile_push`** — set `enabled` + a `vault_dir` that syncs to your phone.

## How it stays cheap

| Layer | Cost |
|---|---|
| `sentinel` + `triage` | **$0** — all local |
| `duck_brain` | only fires on `urgent` — a few hundred tokens per wake |
| `duck radar` | one Claude call per briefing / on-demand pull |

## Layout

| File | Role |
|---|---|
| `sentinel.py` | Window + file watcher → bus. Zero tokens. |
| `browser_watcher.py` | Chrome DevTools Protocol events. |
| `triage.py` | Local LLM classifier. Zero cloud tokens. |
| `duck_session.py` | Warm Claude Code process, HTTP on :7717. |
| `duck_brain.py` | Reacts to urgent events. |
| `duck_proactive.py` | Briefings, idle checks, roll-ups, Duck Radar. |
| `agent_manager.py` + `agents/` | Sub-agent framework. |
| `trend_*.py` | Duck Radar (see DUCK_RADAR.md). |
| `mobile_push.py` | Optional phone push. |

## License

Free for personal use. Do whatever you want with it.
