"""
Duck Terminal — mini terminal emulator using pyte + pywinpty.
Pyte handles all ANSI/VT100 escape codes so Claude's TUI renders properly.
"""
import tkinter as tk
import threading
import os
import time
import pyte
from winpty import PtyProcess

import shutil
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser(os.path.join("~", ".local", "bin", "claude.exe"))
CMD_EXE = r"C:\Windows\System32\cmd.exe"

COLS, ROWS = 107, 27
FONT_SIZE = 12
FONT = "Cascadia Code"


class DuckTerminal:
    def __init__(self, parent_pet):
        self.pet = parent_pet
        self.window = None
        self.pty = None
        self.reading = False
        # Virtual terminal screen
        self.screen = pyte.HistoryScreen(COLS, ROWS, history=500)
        self.screen.set_mode(pyte.modes.LNM)
        self.stream = pyte.Stream(self.screen)
        self.scroll_offset = 0

    def is_open(self):
        try:
            return self.window is not None and self.window.winfo_exists()
        except tk.TclError:
            return False

    def toggle(self):
        if self.is_open():
            self.close()
        else:
            self.open()

    def open(self):
        if self.is_open():
            self.window.lift()
            return

        self.window = tk.Toplevel(self.pet.root)
        self.window.title("Duck Chat")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.config(bg="#0f0a1a")

        # Calculate size based on font
        # Approximate: each char ~7px wide, ~16px tall at size 10
        W = COLS * 8 + 20
        H = ROWS * 17 + 50

        dx = int(self.pet.x + 128 - W // 2)
        dy = int(self.pet.y - H - 10)
        dx = max(10, min(dx, self.pet.screen_w - W - 10))
        dy = max(10, dy)
        self.window.geometry(f"{W}x{H}+{dx}+{dy}")

        # ── Title bar ──
        title_bar = tk.Frame(self.window, bg="#1e1433", height=26)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)

        tk.Label(title_bar, text="  Duck Chat", bg="#1e1433", fg="#a78bfa",
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        close_btn = tk.Label(title_bar, text=" X ", bg="#1e1433", fg="#64748b",
                             font=("Segoe UI", 9), cursor="hand2")
        close_btn.pack(side=tk.RIGHT, padx=4)
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ef4444"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#64748b"))

        title_bar.bind("<ButtonPress-1>", self._start_drag)
        title_bar.bind("<B1-Motion>", self._do_drag)

        # ── Terminal display (monospace canvas) ──
        self.canvas = tk.Canvas(
            self.window, bg="#0f0a1a", highlightthickness=0,
            width=W - 10, height=ROWS * 17
        )
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=(2, 0))

        # ── Keyboard input goes directly to PTY ──
        self.canvas.config(takefocus=True)
        self.canvas.focus_force()
        self.canvas.bind("<Return>", self._on_return)
        self.canvas.bind("<Key>", self._on_key)
        self.canvas.bind("<BackSpace>", lambda e: (self._send("\x08"), "break")[-1])
        self.canvas.bind("<Up>", lambda e: self._send_key("\x1b[A"))
        self.canvas.bind("<Down>", lambda e: self._send_key("\x1b[B"))
        self.canvas.bind("<Left>", lambda e: self._send_key("\x1b[D"))
        self.canvas.bind("<Right>", lambda e: self._send_key("\x1b[C"))
        self.canvas.bind("<Tab>", lambda e: self._send_key("\t"))
        self.canvas.bind("<Escape>", lambda e: self._send("\x1b"))
        self.canvas.bind("<Control-c>", lambda e: self._send("\x03"))
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Control-d>", lambda e: self._send("\x04"))
        self.canvas.bind("<Control-l>", lambda e: self._send("\x0c"))

        # Start PTY
        self._start_pty()
        # Auto-launch claude
        time.sleep(0.3)
        self._send(f'"{CLAUDE_BIN}"\r\n')

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event):
        x = self.window.winfo_x() + (event.x - self._drag_x)
        y = self.window.winfo_y() + (event.y - self._drag_y)
        self.window.geometry(f"+{x}+{y}")

    def _start_pty(self):
        try:
            self.screen.reset()
            self.pty = PtyProcess.spawn(CMD_EXE, dimensions=(ROWS, COLS))
            self.reading = True
            threading.Thread(target=self._read_loop, daemon=True).start()
            # Start render loop
            self._render_loop()
        except Exception as e:
            print(f"[DuckTerm] PTY error: {e}")

    def _read_loop(self):
        """Read from PTY, feed into pyte virtual terminal."""
        while self.reading and self.pty and self.pty.isalive():
            try:
                data = self.pty.read(4096)
                if data:
                    self.stream.feed(data)
            except EOFError:
                break
            except Exception:
                time.sleep(0.02)

    def _render_loop(self):
        """Periodically render the virtual screen to the canvas."""
        if not self.is_open():
            return
        self._render_screen()
        self.window.after(100, self._render_loop)

    def _render_screen(self):
        """Draw the pyte screen buffer onto the tkinter canvas."""
        if not self.is_open():
            return

        self.canvas.delete("all")

        # Color map for pyte colors
        color_map = {
            "default": "#d4d0dc",
            "black": "#1a1025",
            "red": "#f87171",
            "green": "#34d399",
            "yellow": "#fbbf24",
            "blue": "#60a5fa",
            "magenta": "#a78bfa",
            "cyan": "#22d3ee",
            "white": "#e2e8f0",
            "brightred": "#fca5a5",
            "brightgreen": "#6ee7b7",
            "brightyellow": "#fde68a",
            "brightblue": "#93c5fd",
            "brightmagenta": "#c4b5fd",
            "brightcyan": "#67e8f9",
            "brightwhite": "#ffffff",
        }

        char_w = 8
        char_h = 17
        y_offset = 2

        # Build display lines: history (if scrolled) + current screen
        history_lines = list(self.screen.history.top)
        if self.scroll_offset > 0:
            # Show history lines
            hist_start = max(0, len(history_lines) - self.scroll_offset)
            visible_hist = history_lines[hist_start:]
            # Fill remaining rows from current screen
            screen_rows = max(0, ROWS - len(visible_hist))
            display = []
            for h_line in visible_hist:
                display.append(h_line)
            for r in range(screen_rows):
                display.append(self.screen.buffer[r])
            display = display[:ROWS]
        else:
            display = [self.screen.buffer[r] for r in range(ROWS)]

        for row_idx in range(min(ROWS, len(display))):
            line = display[row_idx]
            x = 4
            for col_idx in range(COLS):
                char = line[col_idx]
                ch = char.data if char.data != " " else None
                if ch:
                    fg = char.fg if char.fg != "default" else "default"
                    color = color_map.get(fg, "#d4d0dc")
                    if char.bold and fg == "default":
                        color = "#ffffff"
                    self.canvas.create_text(
                        x, y_offset + row_idx * char_h,
                        text=ch, fill=color,
                        font=(FONT, FONT_SIZE, "bold" if char.bold else "normal"),
                        anchor=tk.NW
                    )
                x += char_w

        # Cursor
        cx = self.screen.cursor.x * char_w + 4
        cy = self.screen.cursor.y * char_h + y_offset
        self.canvas.create_rectangle(
            cx, cy + char_h - 2, cx + char_w, cy + char_h,
            fill="#a78bfa", outline=""
        )

    def _send(self, text):
        if self.pty and self.pty.isalive():
            self.pty.write(text)

    def _send_key(self, key):
        self._send(key)
        return "break"

    def _on_scroll(self, event):
        """Scroll through terminal history."""
        if event.delta > 0:
            # Scroll up
            self.scroll_offset = min(self.scroll_offset + 3, len(self.screen.history.top))
        else:
            # Scroll down
            self.scroll_offset = max(self.scroll_offset - 3, 0)
        self._render_screen()
        return "break"

    def _on_return(self, event):
        """Enter key — send CR to PTY."""
        self.scroll_offset = 0
        self._send("\r")
        return "break"

    def _on_key(self, event):
        """Send keystrokes directly to PTY."""
        # Skip if it's a special key handled elsewhere
        if event.keysym in ("Return", "BackSpace", "Up", "Down", "Left", "Right",
                            "Tab", "Escape", "Shift_L", "Shift_R", "Control_L",
                            "Control_R", "Alt_L", "Alt_R", "Caps_Lock"):
            return "break"
        char = event.char
        if char and len(char) == 1:
            self._send(char)
        return "break"

    def close(self):
        self.reading = False
        if self.pty:
            try:
                self.pty.terminate()
            except Exception:
                pass
            self.pty = None
        if self.window:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
        self.pet.set_state("idle")
