"""
Browser watcher — Chrome DevTools Protocol client.

Connects to Chrome's CDP on port 9222 and continuously observes:
  - active tab URL + title changes
  - runtime console errors/warnings
  - failed network requests (4xx/5xx)
  - page lifecycle (load/navigate)

Emits rich events into the bus. Chrome must be launched with
    --remote-debugging-port=9222
Use `chrome_debug.sh` to relaunch Chrome with this flag.

Runs entirely free (no tokens) — observation only.
"""
import json
import os
import sys
import threading
import time
import traceback
from collections import deque

import requests
import websocket  # pip install websocket-client

import bus

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
POLL_INTERVAL_TABS = 4.0          # rescan tabs list
WAIT_IF_NO_CHROME = 10.0          # backoff when Chrome isn't up
MAX_MESSAGE_RATE_PER_SEC = 10     # drop bursts, protect bus

# Only surface noisy signals; suppress deprecation spam
CONSOLE_INTERESTING_LEVELS = {"error", "warning"}


class TabSession:
    """One websocket per tab — subscribes to Runtime + Network events."""

    def __init__(self, tab_info: dict):
        self.id = tab_info["id"]
        self.url = tab_info.get("url", "")
        self.title = tab_info.get("title", "")
        self.ws_url = tab_info["webSocketDebuggerUrl"]
        self.ws: websocket.WebSocket | None = None
        self._req_seq = 0
        self._stop = threading.Event()
        self._recent = deque(maxlen=MAX_MESSAGE_RATE_PER_SEC * 2)
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"tab-{self.id[:6]}")

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def _send(self, method: str, params: dict | None = None):
        self._req_seq += 1
        msg = {"id": self._req_seq, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))

    def _rate_ok(self) -> bool:
        now = time.time()
        self._recent.append(now)
        # If full buffer spans < 1s, we're bursting — drop
        if len(self._recent) == self._recent.maxlen:
            if (self._recent[-1] - self._recent[0]) < 1.0:
                return False
        return True

    def _run(self):
        try:
            self.ws = websocket.create_connection(self.ws_url, timeout=5)
            self._send("Runtime.enable")
            self._send("Network.enable")
            self._send("Page.enable")
            print(f"[browser] attached: {self.title[:60]}")
            while not self._stop.is_set():
                try:
                    self.ws.settimeout(1.0)
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle(msg)
        except Exception as e:
            print(f"[browser] tab {self.id[:6]} error: {e}")

    def _handle(self, msg: dict):
        method = msg.get("method")
        params = msg.get("params", {}) or {}

        if method == "Runtime.consoleAPICalled":
            level = params.get("type", "")
            if level not in CONSOLE_INTERESTING_LEVELS:
                return
            args = params.get("args", [])
            text = " ".join(
                str(a.get("value", a.get("description", "")))[:200]
                for a in args[:4]
            )
            if not text.strip() or not self._rate_ok():
                return
            bus.push("browser", "console_message", {
                "level": level,
                "text": text[:500],
                "url": self.url,
                "title": self.title,
            })

        elif method == "Runtime.exceptionThrown":
            if not self._rate_ok():
                return
            ex = params.get("exceptionDetails", {})
            bus.push("browser", "exception", {
                "text": ex.get("text", "")[:300],
                "message": (ex.get("exception") or {}).get("description", "")[:500],
                "url": self.url,
                "title": self.title,
                "line": ex.get("lineNumber"),
                "column": ex.get("columnNumber"),
            })

        elif method == "Network.responseReceived":
            resp = params.get("response", {}) or {}
            status = resp.get("status", 0)
            if status < 400:
                return  # only care about 4xx/5xx
            if not self._rate_ok():
                return
            bus.push("browser", "network_error", {
                "status": status,
                "url": resp.get("url", "")[:500],
                "method": (params.get("type") or ""),
                "mime": resp.get("mimeType", ""),
                "page_url": self.url,
                "page_title": self.title,
            })

        elif method == "Page.frameNavigated":
            frame = params.get("frame", {})
            # Only main frame
            if frame.get("parentId"):
                return
            new_url = frame.get("url", "")
            if new_url and new_url != self.url:
                old_url = self.url
                self.url = new_url
                bus.push("browser", "navigate", {
                    "from": old_url[:300],
                    "to": new_url[:300],
                    "title": self.title,
                })


def list_tabs() -> list[dict]:
    try:
        r = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=2)
        r.raise_for_status()
        tabs = r.json()
    except Exception:
        return []
    # Filter out background pages / extension workers
    return [
        t for t in tabs
        if t.get("type") == "page"
        and t.get("webSocketDebuggerUrl")
        and not t.get("url", "").startswith("devtools://")
    ]


def chrome_alive() -> bool:
    try:
        r = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json/version", timeout=1)
        return r.ok
    except Exception:
        return False


def main():
    bus.init()
    print("[browser_watcher] starting")
    print(f"[browser_watcher] CDP target: http://{CDP_HOST}:{CDP_PORT}")

    sessions: dict[str, TabSession] = {}  # tab_id -> session

    while True:
        try:
            if not chrome_alive():
                # Close any stale sessions and wait for Chrome to come up
                for s in list(sessions.values()):
                    s.stop()
                sessions.clear()
                time.sleep(WAIT_IF_NO_CHROME)
                continue

            tabs = list_tabs()
            current_ids = {t["id"] for t in tabs}

            # Attach to new tabs
            for t in tabs:
                if t["id"] not in sessions:
                    sess = TabSession(t)
                    sessions[t["id"]] = sess
                    sess.start()

            # Detach from closed tabs
            for gone in list(sessions.keys()):
                if gone not in current_ids:
                    sessions[gone].stop()
                    del sessions[gone]

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[browser_watcher] loop error: {e}")
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_TABS)

    for s in sessions.values():
        s.stop()


if __name__ == "__main__":
    main()
