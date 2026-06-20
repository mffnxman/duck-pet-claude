"""
Duck Status — a tiny always-on-top widget showing stack health.

One small window in the bottom-right corner with a colored dot per worker:
  green  = running
  red    = dead
  yellow = warming (session hook init)

Click any row to open its log in Notepad. Click the title to toggle collapse.

Uses only stdlib (tkinter, urllib, subprocess). No extra deps.
"""
import json
import os
import subprocess
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import font as tkfont

POLL_MS = 4000
HEALTH_URL = "http://127.0.0.1:7717/health"

WORKERS = [
    # (display name, script name, log path)
    ("sentinel",  "sentinel.py",        "/tmp/sentinel.log"),
    ("browser",   "browser_watcher.py", "/tmp/browser_watcher.log"),
    ("triage",    "triage.py",          "/tmp/triage.log"),
    ("session",   "duck_session.py",    "/tmp/duck_session.log"),
    ("ask",       "ask_listener.py",    "/tmp/ask_listener.log"),
    ("brain",     "duck_brain.py",      "/tmp/duck_brain.log"),
    ("proactive", "duck_proactive.py",  "/tmp/duck_proactive.log"),
    ("pet",       "pet.py",             "/tmp/pet.log"),
]

# Windows-friendly colors
BG   = "#0f0a1a"
FG   = "#e2e8f0"
ACC  = "#1e1b2e"
GREEN  = "#10b981"
RED    = "#ef4444"
YELLOW = "#fbbf24"
GREY   = "#475569"


def running_scripts() -> set[str]:
    """Return set of script names with a live python process."""
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    names = [w[1] for w in WORKERS]
    patterns = " -or ".join(
        f"$_.CommandLine -like '*{n}*'" for n in names
    )
    cmd = [
        ps, "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ ({patterns}) -and "
        "($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') }} | "
        "Select-Object -ExpandProperty CommandLine"
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout
    except Exception:
        return set()
    alive = set()
    for line in out.splitlines():
        for name in names:
            if name in line:
                alive.add(name)
    return alive


def session_healthy() -> bool | None:
    """True if /health says alive, False if endpoint down, None on error."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as r:
            return json.loads(r.read()).get("alive", False)
    except urllib.error.URLError:
        return False
    except Exception:
        return None


class StatusPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Duck")
        self.root.overrideredirect(True)  # borderless
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        # Drag-to-move
        self._drag = {"x": 0, "y": 0}
        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._do_drag)

        # Position bottom-right
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = 160, 24 + 22 * len(WORKERS)
        self.root.geometry(f"{w}x{h}+{sw - w - 20}+{sh - h - 80}")

        header_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        row_font = tkfont.Font(family="Segoe UI", size=9)

        header = tk.Frame(self.root, bg=ACC)
        header.pack(fill="x")
        tk.Label(header, text="  duck status", bg=ACC, fg=FG,
                 font=header_font, anchor="w").pack(side="left", fill="x",
                                                     expand=True)
        close = tk.Label(header, text=" × ", bg=ACC, fg=FG,
                         font=header_font, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.root.destroy())

        self.rows: dict[str, dict] = {}
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=6, pady=3)

        for display, script, log in WORKERS:
            row = tk.Frame(body, bg=BG, cursor="hand2")
            row.pack(fill="x", pady=1)
            dot = tk.Label(row, text="●", bg=BG, fg=GREY, font=row_font)
            dot.pack(side="left")
            name = tk.Label(row, text=f" {display}", bg=BG, fg=FG,
                            font=row_font, anchor="w")
            name.pack(side="left", fill="x", expand=True)
            for w in (row, dot, name):
                w.bind("<Button-1>", lambda e, p=log: self._open_log(p))
            self.rows[script] = {"dot": dot, "name": name}

        self.poll()

    def _start_drag(self, e):
        self._drag["x"] = e.x
        self._drag["y"] = e.y

    def _do_drag(self, e):
        x = self.root.winfo_x() + e.x - self._drag["x"]
        y = self.root.winfo_y() + e.y - self._drag["y"]
        self.root.geometry(f"+{x}+{y}")

    def _open_log(self, path: str):
        # Translate /tmp/... (msys) to a Windows path for Notepad
        win = path
        if path.startswith("/tmp/"):
            tmp = os.environ.get("TEMP") or os.path.expandvars(r"%TEMP%")
            # Git Bash /tmp usually maps to %USERPROFILE%\AppData\Local\Temp
            candidate = os.path.join(os.path.expandvars(r"%USERPROFILE%"),
                                     "AppData", "Local", "Temp",
                                     os.path.basename(path))
            if os.path.exists(candidate):
                win = candidate
            else:
                win = os.path.join(tmp, os.path.basename(path))
        try:
            subprocess.Popen(["notepad.exe", win])
        except Exception:
            pass

    def poll(self):
        alive = running_scripts()
        sess_ok = session_healthy() if "duck_session.py" in alive else False
        for display, script, _log in WORKERS:
            row = self.rows[script]
            if script not in alive:
                color = RED
            elif script == "duck_session.py":
                color = GREEN if sess_ok else YELLOW
            else:
                color = GREEN
            row["dot"].config(fg=color)
        self.root.after(POLL_MS, self.poll)

    def run(self):
        self.root.mainloop()


def main():
    StatusPanel().run()


if __name__ == "__main__":
    main()
