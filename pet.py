"""
Desktop Pet Claude — pixel duck companion.
Click = wave. Right-click = menu with chat.
Walks around, reacts to Claude Code.
Chat bubble appears above head for text conversations.
"""
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageSequence
import os
import sys
import json
import random
import time
import subprocess
import threading
import psutil
import shutil
from duck_terminal import DuckTerminal
from brain import search as brain_search
from personality import Personality

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(BASE_DIR, ".pet.lock")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CREATE_NO_WINDOW = 0x08000000
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser(r"~\.local\bin\claude.exe")


def load_config():
    """Load config.json, return defaults if missing."""
    defaults = {
        "sprite_pack": "default",
        "pet_name": "Duck",
        "personality": {
            "enabled": True,
            "quip_interval_min": 45,
            "quip_interval_max": 120,
            "greeting_on_start": True,
        },
    }
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                user = json.load(f)
            # Merge personality sub-dict
            if "personality" in user:
                defaults["personality"].update(user["personality"])
                user["personality"] = defaults["personality"]
            defaults.update(user)
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def ensure_singleton():
    if os.path.exists(LOCK_FILE):
        try:
            old_pid = int(open(LOCK_FILE).read().strip())
            try:
                old = psutil.Process(old_pid)
                if 'pet.py' in ' '.join(old.cmdline()):
                    old.kill()
                    old.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (ValueError, OSError):
            pass
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))


ensure_singleton()

PET_SIZE = 256
WALK_SPEED = 5
CHECK_CLAUDE_INTERVAL = 5000


def resolve_sprite_dir(config):
    """Resolve sprite directory from config. Supports:
    - "default" -> ./sprites/
    - A name  -> ./sprite_packs/<name>/
    - An absolute path
    """
    pack = config.get("sprite_pack", "default")
    if pack == "default":
        return os.path.join(BASE_DIR, "sprites")
    # Absolute path
    if os.path.isabs(pack):
        return pack
    # Named pack under sprite_packs/
    return os.path.join(BASE_DIR, "sprite_packs", pack)


class ChatBubble:
    """Floating chat bubble above the pet's head."""

    def __init__(self, parent):
        self.parent = parent
        self.window = None
        self.input_window = None
        self.history = []
        self._hide_timer = None

    def show_message(self, text, duration=4000):
        """Show a speech bubble with text above the pet."""
        self.hide()

        self.window = tk.Toplevel(self.parent.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.config(bg="#1e1b2e")

        # Wrap text
        if len(text) > 50:
            words = text.split()
            lines = []
            line = ""
            for w in words:
                if len(line + " " + w) > 40:
                    lines.append(line)
                    line = w
                else:
                    line = (line + " " + w).strip()
            if line:
                lines.append(line)
            text = "\n".join(lines)

        frame = tk.Frame(self.window, bg="#2d2640", padx=2, pady=2,
                         highlightbackground="#7c3aed", highlightthickness=1)
        frame.pack()

        label = tk.Label(
            frame, text=text, bg="#2d2640", fg="#e2e8f0",
            font=("Segoe UI", 10), padx=10, pady=6,
            justify=tk.LEFT, wraplength=300
        )
        label.pack()

        # Position above pet
        self.window.update_idletasks()
        bw = self.window.winfo_width()
        bh = self.window.winfo_height()
        px = int(self.parent.x + PET_SIZE // 2 - bw // 2)
        py = int(self.parent.y - bh - 10)
        # Keep on screen
        px = max(10, min(px, self.parent.screen_w - bw - 10))
        py = max(10, py)
        self.window.geometry(f"+{px}+{py}")

        # Auto-hide
        if self._hide_timer:
            self.parent.root.after_cancel(self._hide_timer)
        self._hide_timer = self.parent.root.after(duration, self.hide)

    def hide(self):
        if self.window:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None

    def show_input(self):
        """Show chat input box above the pet."""
        if self.input_window:
            try:
                self.input_window.destroy()
            except tk.TclError:
                pass

        self.input_window = tk.Toplevel(self.parent.root)
        self.input_window.overrideredirect(True)
        self.input_window.attributes("-topmost", True)
        self.input_window.config(bg="#1e1b2e")

        frame = tk.Frame(self.input_window, bg="#1e1b2e", padx=3, pady=3,
                         highlightbackground="#7c3aed", highlightthickness=2)
        frame.pack()

        label = tk.Label(frame, text="Chat with Claude:", bg="#1e1b2e",
                         fg="#7c3aed", font=("Segoe UI", 9, "bold"))
        label.pack(anchor="w", padx=4)

        entry = tk.Entry(frame, bg="#0f0a1a", fg="#e2e8f0",
                         insertbackground="#7c3aed",
                         font=("Segoe UI", 11), width=40,
                         relief=tk.FLAT, bd=4)
        entry.pack(padx=4, pady=(2, 4))
        entry.focus_force()

        def on_submit(event=None):
            text = entry.get().strip()
            if not text:
                return
            self.input_window.destroy()
            self.input_window = None
            self.parent.set_state("active")
            self.show_message("Thinking...", duration=60000)
            # Run Claude in background thread
            threading.Thread(target=self._ask_claude, args=(text,), daemon=True).start()

        def on_escape(event=None):
            self.input_window.destroy()
            self.input_window = None

        entry.bind("<Return>", on_submit)
        entry.bind("<Escape>", on_escape)

        # Position
        self.input_window.update_idletasks()
        bw = self.input_window.winfo_width()
        bh = self.input_window.winfo_height()
        px = int(self.parent.x + PET_SIZE // 2 - bw // 2)
        py = int(self.parent.y - bh - 15)
        px = max(10, min(px, self.parent.screen_w - bw - 10))
        py = max(10, py)
        self.input_window.geometry(f"+{px}+{py}")

    def _ask_claude(self, user_text):
        """Ask Claude with one-shot -p call. ~15s per response."""
        self.history.append({"role": "user", "content": user_text})

        # Search Obsidian vault for relevant context
        vault_context = brain_search(user_text)

        # Build conversation context
        recent = self.history[-10:]
        context = "You are a desktop pet duck. Keep responses SHORT (1-2 sentences). Be casual. No markdown, no emoji.\n"
        if vault_context:
            context += f"\nYou have access to the user's notes. Use them to answer if relevant:\n{vault_context}\n\n"
        else:
            context += "\n"
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Duck"
            context += f"{role}: {msg['content']}\n"
        context += "Duck:"

        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", context, "--model", "haiku"],
                capture_output=True, timeout=60,
                creationflags=CREATE_NO_WINDOW,
                cwd=os.path.expanduser("~"),
            )
            response = result.stdout.decode("utf-8", errors="replace").strip()
            response = response.encode('ascii', 'ignore').decode('ascii').strip()
            if not response:
                response = "Quack... try again?"
        except subprocess.TimeoutExpired:
            response = "Took too long, try again."
        except Exception as e:
            response = f"Error: {e}"

        self.history.append({"role": "assistant", "content": response})
        self.parent.root.after(0, lambda: self._show_response(response))

    def _show_response(self, text):
        self.parent.set_state("talk")
        self.show_message(text, duration=max(3000, len(text) * 60))
        # Go back to idle after bubble
        self.parent.root.after(max(3000, len(text) * 60), lambda: self.parent.set_state("idle"))


class DesktopPet:
    def __init__(self):
        self.config = load_config()
        self.sprite_dir = resolve_sprite_dir(self.config)
        self.pet_name = self.config.get("pet_name", "Duck")
        self.personality = Personality(self.config.get("personality", {}))

        self.root = tk.Tk()
        self.root.title("Claude Pet")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.attributes("-transparentcolor", "black")
        self.root.config(bg="black")

        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.x = self.screen_w // 2
        self.y = self.screen_h - PET_SIZE - 48

        self.state = "idle"
        self.state_timer = time.time()
        self.last_active = time.time()
        self.claude_running = False
        self.direction = 1
        self.frame_index = 0
        self.dragging = False
        self.drag_offset = (0, 0)
        self.drag_moved = False
        self.manual_sit = False

        self.canvas = tk.Canvas(
            self.root, width=PET_SIZE, height=PET_SIZE,
            bg="black", highlightthickness=0, bd=0
        )
        self.canvas.pack()

        self.sprites = {}
        self.load_sprites()
        self.sprite_id = self.canvas.create_image(
            PET_SIZE // 2, PET_SIZE // 2,
            image=self.sprites["idle"][0], anchor=tk.CENTER
        )

        self.tooltip = None
        self.chat = ChatBubble(self)
        self.terminal = DuckTerminal(self)

        # Events
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right_click)

        self.update_position()
        self.animate()
        self.behavior_loop()
        self.check_claude_running()
        self.personality_loop()

        # Startup greeting from personality
        greeting = self.personality.get_startup_greeting()
        if greeting:
            self.root.after(2000, lambda: self.chat.show_message(greeting, 5000))
        else:
            self.root.after(2000, lambda: self.chat.show_message("Hey! Right-click me to chat.", 5000))

        print(f"[Claude Pet] {self.pet_name} is alive! Right-click for menu.")

    # Terminal handles Claude communication via PTY

    def load_sprites(self):
        if not os.path.isdir(self.sprite_dir):
            print(f"[Claude Pet] Sprite directory not found: {self.sprite_dir}")
            print(f"[Claude Pet] Falling back to default sprites.")
            self.sprite_dir = os.path.join(BASE_DIR, "sprites")

        for name in ["idle", "walk_right", "walk_left", "sit",
                      "active", "wave", "talk", "celebrate", "listen"]:
            path = os.path.join(self.sprite_dir, f"{name}.gif")
            if os.path.exists(path):
                gif = Image.open(path)
                frames = []
                for frame in ImageSequence.Iterator(gif):
                    frame = frame.convert("RGBA")
                    frames.append(ImageTk.PhotoImage(frame))
                self.sprites[name] = frames
        if "idle" not in self.sprites:
            sys.exit(1)

    def update_position(self):
        self.root.geometry(f"{PET_SIZE}x{PET_SIZE}+{int(self.x)}+{int(self.y)}")
        # Move terminal with duck if it's open
        if self.terminal.is_open():
            tw = self.terminal.window.winfo_width()
            th = self.terminal.window.winfo_height()
            tx = int(self.x + PET_SIZE // 2 - tw // 2)
            ty = int(self.y - th - 10)
            tx = max(10, min(tx, self.screen_w - tw - 10))
            ty = max(10, ty)
            self.terminal.window.geometry(f"+{tx}+{ty}")

    def set_state(self, new_state):
        if new_state != self.state:
            self.state = new_state
            self.frame_index = 0
            self.state_timer = time.time()

    def animate(self):
        frames = self.sprites.get(self.state, self.sprites["idle"])
        self.frame_index = (self.frame_index + 1) % len(frames)
        self.canvas.itemconfig(self.sprite_id, image=frames[self.frame_index])
        durations = {
            "idle": 350, "walk_right": 85, "walk_left": 85,
            "sit": 700, "active": 200, "wave": 120,
            "talk": 180, "celebrate": 130, "listen": 250
        }
        self.root.after(durations.get(self.state, 200), self.animate)

    def behavior_loop(self):
        if self.dragging:
            self.root.after(100, self.behavior_loop)
            return

        # If manually sitting, stay put
        if self.manual_sit and self.state not in ("talk", "wave", "celebrate"):
            if self.state != "sit":
                self.set_state("sit")
            self.root.after(30, self.behavior_loop)
            return

        elapsed = time.time() - self.state_timer
        idle_time = time.time() - self.last_active

        if self.state in ("walk_left", "walk_right"):
            self.x += WALK_SPEED * self.direction
            if self.x <= 20:
                self.x = 20
                self.direction = 1
                self.set_state("walk_right")
            elif self.x >= self.screen_w - PET_SIZE - 20:
                self.x = self.screen_w - PET_SIZE - 20
                self.direction = -1
                self.set_state("walk_left")
            self.update_position()
            if elapsed > random.uniform(4, 10):
                self.set_state("idle")

        elif self.state == "idle":
            if elapsed > random.uniform(0.5, 2):
                if idle_time > 120 and not self.claude_running:
                    self.set_state("sit")
                else:
                    self.direction = random.choice([-1, 1])
                    self.set_state("walk_right" if self.direction > 0 else "walk_left")

        elif self.state == "sit":
            if self.claude_running:
                self.set_state("active")
            elif elapsed > random.uniform(10, 20):
                self.direction = random.choice([-1, 1])
                self.set_state("walk_right" if self.direction > 0 else "walk_left")

        elif self.state == "active":
            # Don't get stuck — go back to idle after a bit
            if elapsed > 2:
                self.set_state("idle")

        elif self.state in ("wave", "celebrate"):
            if elapsed > 2:
                self.set_state("idle")

        elif self.state == "talk":
            if elapsed > 8:
                self.set_state("idle")

        self.root.after(30, self.behavior_loop)

    def check_claude_running(self):
        def _check():
            try:
                for proc in psutil.process_iter(['name']):
                    if 'claude' in proc.info['name'].lower():
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            return False

        was_running = self.claude_running
        self.claude_running = _check()
        if self.claude_running and not was_running:
            self.last_active = time.time()
            if self.state in ("idle", "sit"):
                self.set_state("active")
            quip = self.personality.on_claude_started()
            if quip:
                self.chat.show_message(quip, 3000)
        elif not self.claude_running and was_running:
            self.set_state("celebrate")
            quip = self.personality.on_claude_finished()
            if quip:
                self.chat.show_message(quip, 3000)

        self.root.after(CHECK_CLAUDE_INTERVAL, self.check_claude_running)

    def personality_loop(self):
        """Check for idle quips periodically."""
        # Don't quip if chat bubble or terminal is active
        if not self.chat.window and not self.terminal.is_open():
            quip = self.personality.check_idle_quip(self.state)
            if quip:
                self.chat.show_message(quip, 3500)
        self.root.after(10000, self.personality_loop)

    # ── Mouse Events ──

    def on_press(self, event):
        self.drag_start = (event.x, event.y)
        self.drag_moved = False

    def on_drag(self, event):
        self.drag_moved = True
        if not self.dragging:
            self.dragging = True
            self.drag_offset = (event.x, event.y)
        self.x = self.root.winfo_pointerx() - self.drag_offset[0]
        self.y = self.root.winfo_pointery() - self.drag_offset[1]
        self.update_position()

    def on_release(self, event):
        if self.dragging:
            self.dragging = False
            self.last_active = time.time()
            if self.y > self.screen_h * 0.7:
                self.y = self.screen_h - PET_SIZE - 48
                self.update_position()
        elif not self.drag_moved:
            self.last_active = time.time()
            self.set_state("wave")

    def on_right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#1e1b2e", fg="#e2e8f0",
                       activebackground="#7c3aed", activeforeground="white",
                       font=("Segoe UI", 10))
        menu.add_command(label="Chat", command=self.terminal.toggle)
        menu.add_separator()
        menu.add_command(label="Sit", command=self.toggle_sit)
        menu.add_command(label="Wave!", command=lambda: self.set_state("wave"))
        menu.add_command(label="Celebrate!", command=lambda: self.set_state("celebrate"))
        menu.add_separator()

        # Sprite pack submenu
        packs = self._list_sprite_packs()
        if len(packs) > 1:
            pack_menu = tk.Menu(menu, tearoff=0, bg="#1e1b2e", fg="#e2e8f0",
                                activebackground="#7c3aed", activeforeground="white",
                                font=("Segoe UI", 10))
            current = self.config.get("sprite_pack", "default")
            for pack_name in packs:
                label = f"{'> ' if pack_name == current else '  '}{pack_name}"
                pack_menu.add_command(
                    label=label,
                    command=lambda p=pack_name: self.switch_sprite_pack(p)
                )
            menu.add_cascade(label="Sprites", menu=pack_menu)
            menu.add_separator()

        # Personality toggle
        p_label = "Personality: ON" if self.personality.enabled else "Personality: OFF"
        menu.add_command(label=p_label, command=self.toggle_personality)

        status = "Working" if self.claude_running else "Chillin'"
        menu.add_command(label=f"Status: {status}", state="disabled")
        menu.add_separator()
        menu.add_command(label="Quit", command=self.quit)
        menu.tk_popup(event.x_root, event.y_root)

    def _list_sprite_packs(self):
        """List available sprite packs."""
        packs = ["default"]
        packs_dir = os.path.join(BASE_DIR, "sprite_packs")
        if os.path.isdir(packs_dir):
            for name in sorted(os.listdir(packs_dir)):
                pack_path = os.path.join(packs_dir, name)
                if os.path.isdir(pack_path) and os.path.isfile(os.path.join(pack_path, "idle.gif")):
                    packs.append(name)
        return packs

    def switch_sprite_pack(self, pack_name):
        """Hot-swap sprites without restarting."""
        self.config["sprite_pack"] = pack_name
        self.sprite_dir = resolve_sprite_dir(self.config)
        # Save to config.json
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except OSError:
            pass
        # Reload sprites
        self.sprites.clear()
        self.load_sprites()
        self.frame_index = 0
        self.chat.show_message(f"Switched to {pack_name}!", 2500)

    def toggle_personality(self):
        """Toggle personality quips on/off."""
        self.personality.enabled = not self.personality.enabled
        self.config["personality"]["enabled"] = self.personality.enabled
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except OSError:
            pass
        state = "on" if self.personality.enabled else "off"
        self.chat.show_message(f"Personality {state}.", 2000)

    def toggle_sit(self):
        """Toggle sit mode — stays sitting until clicked again."""
        self.manual_sit = not self.manual_sit
        if self.manual_sit:
            self.set_state("sit")
        else:
            self.set_state("idle")

    def quit(self):
        self.chat.hide()
        self.terminal.close()
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    pet = DesktopPet()
    pet.run()
