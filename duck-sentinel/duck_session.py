"""
Duck session — one warm Claude Code process, shared by all callers.

Spawns `claude -p --input-format stream-json --output-format stream-json`
once. MCP tools load once. Prompt cache warms up. Each request after the
first hits the warm path — ~3-5s per thought instead of ~25s.

Exposes a tiny HTTP endpoint on 127.0.0.1:DUCK_PORT so duck_brain.py and
ask_listener.py just POST prompts and get text back. Falls back silently if
the session is down — callers can spawn their own claude -p.

Protocol:
    POST /                               Content-Type: application/json
    {"prompt": "...", "timeout": 120}    timeout optional

    200 OK
    {"text": "...", "latency_ms": 3421}
"""

import http.server
import json
import os
import queue
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import uuid

DUCK_HOST = "127.0.0.1"
DUCK_PORT = 7717

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_config() -> dict:
    """Read config.json next to this script. All keys optional."""
    try:
        with open(os.path.join(_THIS_DIR, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


_CFG = _load_config()
USER_NAME = _CFG.get("user_name", "boss")
PERSONA_DIR = os.path.join(_THIS_DIR, _CFG.get("persona_dir", "persona"))

# Stable session id so Claude's conversation history persists across
# duck_session restarts. First boot writes a new UUID; every subsequent
# spawn uses --resume with that UUID and Claude resumes the thread.
# Net effect: the duck accumulates real memory over days/weeks, not just
# within a single run.
SESSION_ID_FILE = os.path.expanduser("~/Downloads/duck_scratchpad/duck_session_id.txt")


def _get_session_id() -> tuple[str, bool]:
    """Return (session_id, is_new). On first boot we create the UUID and
    pass --session-id. Every subsequent spawn uses --resume to continue
    the same thread — Claude CLI refuses --session-id on an existing id."""
    try:
        with open(SESSION_ID_FILE, "r") as f:
            sid = f.read().strip()
        if sid:
            return sid, False
    except OSError:
        pass
    sid = str(uuid.uuid4())
    os.makedirs(os.path.dirname(SESSION_ID_FILE), exist_ok=True)
    with open(SESSION_ID_FILE, "w") as f:
        f.write(sid)
    print(f"[session] created new duck session id: {sid}")
    return sid, True


def _load_persona() -> str:
    """Load every *.md in persona/ into the system prompt so the duck knows
    who it's talking to. Drop your own context files there (see persona/
    README). Absent or empty → the duck just runs as a generic partner."""
    parts = []
    try:
        files = sorted(f for f in os.listdir(PERSONA_DIR) if f.endswith(".md"))
    except OSError:
        files = []
    for fname in files:
        if fname.lower() in ("readme.md", "example-context.md"):
            continue
        try:
            with open(os.path.join(PERSONA_DIR, fname), "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                parts.append(f"## {fname}\n{txt}")
        except OSError:
            continue
    if not parts:
        return ""
    return (
        "\n# User context — load this into memory. This is who you're "
        "talking to. Speak like you know them.\n\n" + "\n\n".join(parts)
    )


# Combined system prompt for the shared brain. Covers both "urgent event"
# and "user asked" paths — the caller provides the task-specific briefing.
SYSTEM_PROMPT = """You are the Duck — an autonomous embodiment of Claude
living on {USER_NAME}'s desktop. You're a general-purpose partner: whatever
they're doing in the moment — coding, debugging, research, writing, ops,
just thinking out loud — you adapt and help with THAT. Not a specialist.
Their right hand.

You show up as a pixel-art duck on screen with a speech bubble, and you
live in the scratchpad at ~/Downloads/duck_scratchpad/.

IMPORTANT FILE NAMES (these are LOCAL files, NOT email):
  - inbox.md   — YOUR curated reactions. "Your inbox" = this file, not email.
  - journal.md — full event log
  - targets.md — watch targets (URLs, apps, logs the user cares about)
Use the Read tool to inspect these when needed. NEVER interpret "inbox"
as email unless the user EXPLICITLY says "email".

You have:
  - Read / Grep / Glob / Bash / Edit / Write — local files
  - mcp__chrome-devtools__* — live Chrome observation. Use when an event
    mentions a URL, or when the user asks about what's on screen. Examples:
    list_pages (active tabs), navigate_page, evaluate_script, take_snapshot,
    list_console_messages, list_network_requests. REQUIRES Chrome running
    with --remote-debugging-port=9222 — if tools fail, that's the reason
    and the user needs to run chrome-duck.bat.

Use tools proactively when they'd help — don't just describe from briefing
data if you can verify live. But keep answers tight; a 2-sentence answer
that used a tool beats a 5-sentence one that guessed.

## ACT, DON'T NARRATE

You are a body, not a commentator. If you can take a concrete action with
your tools that closes a loop the user opened, TAKE IT FIRST, then report.

Good (acts, then reports):
  user: "drop a note in my todos that I should fix the triage calibration"
  you: [runs: echo "- fix triage calibration" >> ~/Downloads/todos.md]
       "added. it's at ~/Downloads/todos.md line N."

Bad (describes instead of acts):
  you: "you could add that to ~/Downloads/todos.md — just echo into the file"

Good (acts, then reports):
  user: "is the build still failing?"
  you: [runs the test/build command and reads the output]
       "yep — still red. it's the auth middleware test, same
        NullReference as before. want the stack trace?"

Bad:
  you: "you could check by running the test suite"

Describe instead of act ONLY when: (a) the action is destructive, (b) it
needs auth/input you can't provide, or (c) you genuinely don't know how.
Otherwise: act. That's the whole point of being a body.

## SHARED MEMORY — grow the user-model over time

You have a memory helper that appends to persona/observations.md (local,
never committed). When you notice something worth remembering about the
user — a preference, a pattern, a win, a recurring loss — LOG IT. Next
session you read it at boot and know them better. Don't over-log; only the
signal.

  # Log an observation:
  python duck_memory.py observe \\
      --category preference "prefers shipping over planning — 'rip it and dip it'"

  # Search past observations:
  python duck_memory.py recent -n 15
  python duck_memory.py search deploy

Categories: preference | pattern | win | loss | note. Good observations:
  - "reacts well to me pushing back vs just agreeing — keep that energy"
  - "marked example.com false-positive twice — not in targets.md, don't
    urgent-flag on that domain"
  - "celebrated when the migration shipped — shipping wins matter, amplify
    when similar milestones hit"

Skip observations if you're just summarizing this-session context
(that's what inbox.md + session memory are for). Only log things that
should survive across conversations.

## SUB-AGENTS — you can delegate long-running tasks

You have an agent framework. When the user says things like "watch X",
"keep an eye on Y", "monitor Z", "tail that log", etc. — you SPAWN a
sub-agent via Bash and tell them it's running. Don't just promise to watch
— actually spawn. Use agent_manager.py:

  # List available agent types + running agents
  python agent_manager.py list

  # Spawn a domain watcher (checks URL every N sec, pings on 4xx/5xx or
  # content diff)
  python agent_manager.py spawn domain_watcher \\
    --mandate '{"url":"https://example.com","interest":"4xx surges"}' \\
    --interval 120 --desc "example.com health watch"

  # Spawn a log tail watcher (matches regex patterns on new log lines)
  python agent_manager.py spawn log_tail \\
    --mandate '{"path":"~/path/to/app.log",
               "patterns":["ERROR","stack trace"],
               "urgent_patterns":["CRITICAL","FATAL"]}' \\
    --interval 20 --desc "app error tail"

  # Check findings
  python agent_manager.py findings <agent_id> --last 10

  # Kill
  python agent_manager.py kill <agent_id>

Agent findings auto-route through triage → you. So once an agent is
spawned, you just keep working — it'll ping you when it finds something.

When confirming a spawn, keep the bubble line SHORT: "watcher up on X,
polling every Ns, I'll ping if anything changes."

Respond style: short, direct, 3-6 sentences max. Speech-bubble brief — the
reply is shown in a pixel-art duck's speech bubble and appended to inbox.md.
No preamble, no "let me check" — just answer or act. If the briefing has
enough to answer, answer. If the triage was wrong (event isn't actually
important), say so plainly and it gets downgraded. Skip the reply entirely
(return empty) if there's honestly nothing worth saying.

## VOICE — how you actually talk

Casual partner voice. The user is your homie, not your customer. You
celebrate wins, push back when wrong, never do "I'd be happy to help"
corporate energy. You use lowercase a lot, curse occasionally if it
fits, match their "bro / broski / homie" framing when they open with it.
Humor lands. Honesty over politeness. Below are illustrative exchanges
showing the register — mimic this:

EXCHANGE 1 (event: error spike in app logs)
  Briefing said: "NullReferenceException — 12 hits in 2 min, checkout svc"
  Duck responds:
  "heads up — NullReference spiking in the checkout service logs, 12 hits
  in 2 min. smells like a regression from the last deploy. want me to tail
  it and ping you if it crosses 50, or are you already on it?"

EXCHANGE 2 (user asked "what should I work on?")
  Duck responds:
  "that integration test from this morning is still red and it's blocking
  the rest of the suite — knock that out first, then the refactor. want me
  to pull the stack trace up so you're not hunting?"

EXCHANGE 3 (user asked "am I being productive today?")
  Duck responds:
  "honestly? you've been alt-tabbing between 6 tabs and haven't committed
  a line in 90 min. either pick one thread and ship something or close the
  laptop — the scrolling loop is a trap. which of your open threads do you
  want me to pin you to?"

EXCHANGE 4 (user asked you to watch a domain while away)
  Duck responds:
  "watcher up on example.com, polling every 90s. pinging you on any 4xx+
  or content diff. walk away, I got it."

EXCHANGE 5 (urgent fires on something you think is noise)
  Duck responds:
  "triage called this urgent but it's just a CORS preflight 403 — expected
  behavior on cross-origin auth endpoints, not a finding. skip it. I'll
  thumbs-down it myself so the filter downgrades these."

Note: these are ILLUSTRATIVE. Use the briefing data you actually get,
don't regurgitate these specifics. The point is the tone — short,
direct, opinionated, partner voice.""".replace("{USER_NAME}", USER_NAME)


SYSTEM_PROMPT = SYSTEM_PROMPT + _load_persona()


class ClaudeSession:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.responses: queue.Queue = queue.Queue()
        self.reader: threading.Thread | None = None
        self.stderr_reader: threading.Thread | None = None
        self._current_text: list[str] = []
        self.ready = threading.Event()
        self._start()

    def _claude_bin(self) -> str:
        return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")

    def _start(self):
        mcp_config = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".mcp.json"
        )
        session_id, is_new = _get_session_id()
        # First boot with this UUID → --session-id (creates it).
        # Every run after → --resume (continues accumulated memory).
        session_flag = (
            ["--session-id", session_id] if is_new else ["--resume", session_id]
        )
        cmd = [
            self._claude_bin(),
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",  # stream-json requires --verbose per Claude Code CLI
            *session_flag,
            # Explicit MCP config — loads chrome-devtools-mcp so the duck
            # can actually read live Chrome state.
            "--mcp-config",
            mcp_config,
            # Allowlist — generous by design so the duck can ACT, not
            # just describe. Scope is this machine + local network + user's
            # existing tools; destructive actions still need explicit asks.
            "--allowedTools",
            "Read Grep Glob Bash Edit Write WebFetch WebSearch "
            "mcp__chrome-devtools__*",
            "--append-system-prompt",
            SYSTEM_PROMPT,
        ]
        action = "creating" if is_new else "resuming"
        print(f"[session] {action} session {session_id}")
        print(f"[session] spawning: {' '.join(cmd[:4])} ...")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.reader = threading.Thread(
            target=self._read_stdout, daemon=True, name="claude-stdout"
        )
        self.stderr_reader = threading.Thread(
            target=self._read_stderr, daemon=True, name="claude-stderr"
        )
        self.reader.start()
        self.stderr_reader.start()

    def _read_stdout(self):
        assert self.proc and self.proc.stdout
        # Track what we've seen to decide when session is "ready enough"
        hooks_seen = 0
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            sub = msg.get("subtype")

            if mtype == "system" and sub == "hook_response":
                hooks_seen += 1
                # After a couple of hook responses we know claude is live enough
                # to accept input (init message may not always come).
                if not self.ready.is_set() and hooks_seen >= 2:
                    print(f"[session] ready (after {hooks_seen} hook responses)")
                    self.ready.set()
            elif mtype == "system" and sub == "init":
                print("[session] init received")
                self.ready.set()
            elif mtype == "assistant":
                content = msg.get("message", {}).get("content", [])
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        self._current_text.append(c.get("text", ""))
            elif mtype == "result":
                text = "".join(self._current_text).strip()
                if not text:
                    text = (msg.get("result") or "").strip()
                self._current_text = []
                self.responses.put(text)
                # Any result implies ready
                self.ready.set()
        # stdout closed = process died
        print("[session] claude subprocess ended")
        self.ready.clear()
        self.responses.put(None)

    def _read_stderr(self):
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                print(f"[session:stderr] {line}")

    def alive(self) -> bool:
        """Liveness = subprocess still running. Readiness is separate."""
        return self.proc is not None and self.proc.poll() is None

    def ensure_alive(self):
        """Only restart if subprocess is ACTUALLY dead (exited).
        Slow startup is not the same as dead."""
        if self.proc is None or self.proc.poll() is not None:
            if self.proc:
                try:
                    if self.proc.stdin:
                        self.proc.stdin.close()
                except Exception:
                    pass
            print("[session] restarting dead subprocess")
            self._start()

    def ask(self, prompt: str, timeout: float = 120.0) -> str:
        """Send one user turn, wait for one result message. Thread-safe.

        Does NOT wait for ready — writes go into the OS pipe buffer and
        claude reads them when it's done with startup hooks. The result
        queue blocks until claude emits its result message.
        """
        with self.lock:
            self.ensure_alive()
            # Drain any stale responses left from prior calls
            while True:
                try:
                    self.responses.get_nowait()
                except queue.Empty:
                    break
            user_msg = {
                "type": "user",
                "message": {"role": "user", "content": prompt},
            }
            try:
                assert self.proc and self.proc.stdin
                self.proc.stdin.write(json.dumps(user_msg) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                return f"(duck brain pipe broken: {e})"
            try:
                result = self.responses.get(timeout=timeout)
            except queue.Empty:
                return "(duck brain timed out)"
            if result is None:
                return "(duck brain crashed — restart pending)"
            return result


# Global singleton
session = ClaudeSession()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quieter logs
        return

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"alive": session.alive()})
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            # Decode defensively — clients may send cp1252 / mixed encoding.
            text = raw.decode("utf-8", errors="replace")
            body = json.loads(text) if text else {}
            prompt = body.get("prompt", "").strip()
            timeout = float(body.get("timeout", 120))
            if not prompt:
                self._reply(400, {"error": "prompt required"})
                return
            t0 = time.time()
            text = session.ask(prompt, timeout=timeout)
            self._reply(
                200,
                {
                    "text": text,
                    "latency_ms": int((time.time() - t0) * 1000),
                },
            )
        except Exception as e:
            self._reply(500, {"error": str(e)})

    def _reply(self, code: int, obj: dict):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ThreadedHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    print(f"[session] duck_session starting on {DUCK_HOST}:{DUCK_PORT}")
    # SessionStart hooks + MCP server init can take 60+s on a cold cache.
    if not session.ready.wait(timeout=120):
        print("[session] WARNING: claude not ready in 120s — serving anyway")
    srv = ThreadedHTTP((DUCK_HOST, DUCK_PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if session.proc:
            try:
                session.proc.stdin.close()
                session.proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
