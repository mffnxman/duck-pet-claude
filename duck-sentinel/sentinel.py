"""
Sentinel daemon — local-only desktop awareness.
Watches active window + file saves + (later) browser tabs.
Emits events to bus.db for the triage worker to classify.

Costs zero tokens. Runs always.
"""
import os
import sys
import time
import hashlib
import threading
from datetime import datetime

import bus

# ── Helpers ────────────────────────────────────────────

def _normalize_title(t: str) -> str:
    """Strip spinner / progress noise so equivalent titles compare equal.

    Removes braille pattern chars (U+2800-U+28FF, used by CLI spinners),
    leading/trailing whitespace, and common progress indicators (percentages,
    arrow bars). The remaining text is what a human would consider the title.
    """
    if not t:
        return ""
    out = []
    for ch in t:
        cp = ord(ch)
        if 0x2800 <= cp <= 0x28FF:   # braille spinner chars
            continue
        if ch in "\u2800\u2801\u2802\u2804\u2808\u2810\u2820\u2840\u2880\u2733\u2737":
            continue
        out.append(ch)
    return "".join(out).strip()


# ── Active window watcher (Windows) ────────────────────

def _get_active_window():
    """Return (process_name, window_title) for foreground window."""
    try:
        import ctypes
        from ctypes import wintypes
        import psutil

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            proc = psutil.Process(pid.value)
            app = proc.name()
        except Exception:
            app = "unknown"
        return app, title
    except Exception:
        return "", ""


# ── Watch loops ────────────────────────────────────────

class WindowWatcher:
    """Emits a window_change event when the active window app changes."""

    def __init__(self, poll_interval: float = 1.5,
                 title_change_cooldown: float = 8.0):
        self.poll_interval = poll_interval
        self.title_change_cooldown = title_change_cooldown
        self.last_app = ""
        self.last_title = ""
        self.app_start_ts = time.time()
        self.last_title_event_ts = 0.0

    def tick(self):
        app, title = _get_active_window()
        if not app:
            return
        if app != self.last_app:
            duration = time.time() - self.app_start_ts
            bus.push("window", "app_change", {
                "from_app": self.last_app,
                "from_title": self.last_title,
                "to_app": app,
                "to_title": title,
                "previous_duration_sec": round(duration, 1),
            })
            self.last_app = app
            self.last_title = title
            self.app_start_ts = time.time()
        elif title != self.last_title:
            # Normalize out spinner/progress noise (braille U+2800-U+28FF,
            # leading whitespace, control/format chars). If the meaningful
            # title is unchanged, never emit.
            if _normalize_title(title) == _normalize_title(self.last_title):
                self.last_title = title
                return
            now = time.time()
            if (now - self.last_title_event_ts) < self.title_change_cooldown:
                self.last_title = title
                return
            bus.push("window", "title_change", {
                "app": app,
                "from_title": self.last_title,
                "to_title": title,
            })
            self.last_title = title
            self.last_title_event_ts = now

    def run(self):
        print(f"[sentinel.window] polling every {self.poll_interval}s")
        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"[sentinel.window] error: {e}")
            time.sleep(self.poll_interval)


class FileWatcher:
    """Emits file_save events for changes in watched dirs.
    Lightweight: just polls mtimes; no watchdog dependency."""

    def __init__(self, dirs: list[str], poll_interval: float = 4.0,
                 ignore_exts=(".tmp", ".swp", ".lock", ".log", ".pyc",
                              ".db", ".db-journal", ".db-wal", ".db-shm"),
                 ignore_basenames=("journal.md", "inbox.md",
                                   "speak.txt", "ask.txt")):
        self.dirs = [os.path.expanduser(d) for d in dirs]
        self.poll_interval = poll_interval
        self.ignore_exts = ignore_exts
        self.ignore_basenames = set(ignore_basenames)
        self.snapshot: dict[str, float] = {}
        self._seed()

    def _walk(self):
        for root in self.dirs:
            if not os.path.isdir(root):
                continue
            for dirpath, _, files in os.walk(root):
                # Skip hidden / cache
                if any(p.startswith(".") or p in ("__pycache__", "node_modules")
                       for p in dirpath.replace(root, "").split(os.sep)):
                    continue
                for f in files:
                    if f.endswith(self.ignore_exts):
                        continue
                    if f in self.ignore_basenames:
                        continue
                    yield os.path.join(dirpath, f)

    def _seed(self):
        for path in self._walk():
            try:
                self.snapshot[path] = os.path.getmtime(path)
            except OSError:
                pass

    def tick(self):
        seen = set()
        for path in self._walk():
            seen.add(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            prev = self.snapshot.get(path)
            if prev is None:
                self.snapshot[path] = mtime
                bus.push("file", "created", {"path": path, "mtime": mtime})
            elif mtime > prev + 0.5:
                self.snapshot[path] = mtime
                bus.push("file", "modified", {"path": path, "mtime": mtime})
        # Detect deletions
        gone = [p for p in self.snapshot if p not in seen]
        for path in gone:
            del self.snapshot[path]
            bus.push("file", "deleted", {"path": path})

    def run(self):
        print(f"[sentinel.file] watching {len(self.dirs)} dirs, every {self.poll_interval}s")
        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"[sentinel.file] error: {e}")
            time.sleep(self.poll_interval)


# ── Entry point ────────────────────────────────────────

def main():
    bus.init()
    print(f"[sentinel] starting at {datetime.now().isoformat()}")
    print(f"[sentinel] event bus: {bus.DB_PATH}")

    # NOTE: deliberately exclude ~/tools/duck-sentinel — it contains bus.db
    # and we'd create a self-reference feedback loop.
    # Folders to watch for file activity — configure via "watch_dirs" in
    # config.json (defaults to the scratchpad only).
    import json as _json
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config.json"), encoding="utf-8") as _f:
            watch_dirs = _json.load(_f).get(
                "watch_dirs", ["~/Downloads/duck_scratchpad"])
    except Exception:
        watch_dirs = ["~/Downloads/duck_scratchpad"]

    threads = []
    win = WindowWatcher()
    fwatch = FileWatcher(watch_dirs)
    threads.append(threading.Thread(target=win.run, daemon=True, name="window"))
    threads.append(threading.Thread(target=fwatch.run, daemon=True, name="file"))
    for t in threads:
        t.start()

    print("[sentinel] running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[sentinel] stopped")


if __name__ == "__main__":
    main()
