# Duck Pet Claude

A pixel art desktop duck that walks around your screen with a built-in Claude Code terminal.

Click the duck to wave. Right-click for the menu. Open **Chat** to get a full Claude Code terminal floating above the duck's head.

## Requirements

- **Windows 10/11**
- **Python 3.10+** — [python.org](https://python.org) (check "Add Python to PATH" during install)
- **Claude Code** — [claude.ai/download](https://claude.ai/download) (free CLI from Anthropic)

## Quick Start

### Option 1: Double-click
Run `start.bat` — the duck appears on your taskbar in a few seconds.

### Option 2: Terminal
```
python setup.py
python pet.py
```

## Setup

1. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

2. **Generate sprites** (only needed once):
   ```
   python sprite_gen.py source.gif
   ```
   Sprites are pre-generated in the `sprites/` folder, so you can skip this step unless you want to use your own GIF.

3. **Launch the duck:**
   ```
   python pet.py
   ```
   Or double-click `start.bat`.

## Controls

| Action | What happens |
|--------|-------------|
| **Click** | Duck waves |
| **Drag** | Move the duck |
| **Right-click** | Menu — Chat, Sit, Wave, Celebrate |
| **Chat** | Opens a Claude Code terminal above the duck |
| **Mouse wheel** (in terminal) | Scroll history |

## Voice Chat (Optional)

Talk to the duck with your microphone. Requires extra dependencies:

```
pip install -r voice_requirements.txt
python voice.py
```

Uses Edge TTS for speech and Whisper for listening. Say "bye" or "stop" to end.

## How It Works

- The duck watches for running Claude Code processes and reacts (gets excited when Claude is working, celebrates when it finishes)
- The terminal is a real PTY — everything you can do in Claude Code works here
- Sprites are generated from `source.gif` using `sprite_gen.py`

## Troubleshooting

- **"Python not found"** — Make sure Python is in your PATH. Reinstall with "Add to PATH" checked.
- **"Claude Code not found"** — Install from [claude.ai/download](https://claude.ai/download), then restart your terminal.
- **Duck won't start** — Delete `.pet.lock` if it exists, then try again.
- **Terminal blank** — Click inside the terminal window to focus it. Type something.

## License

Free for personal use. Do whatever you want with it.
