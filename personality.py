"""
Idle personality — gives the duck a voice when nobody's talking to it.
Time-aware greetings, random quips, reactions to Claude activity.
"""
import random
import time
from datetime import datetime


# ── Greetings by time of day ─────────────────────────

MORNING = [
    "Morning! Let's get it.",
    "Rise and grind.",
    "Coffee first, code second.",
    "New day, new bugs to squash.",
    "Good morning boss.",
]

AFTERNOON = [
    "Afternoon slump? Not on my watch.",
    "Halfway through the day, keep pushing.",
    "Lunch coma hitting yet?",
    "Still going strong.",
    "Don't forget to eat something.",
]

EVENING = [
    "Still at it? Respect.",
    "Evening vibes. Wrap it up soon?",
    "The code can wait til tomorrow... maybe.",
    "Night owl mode activated.",
    "Don't burn out, take a break.",
]

LATE_NIGHT = [
    "It's late. You good?",
    "Sleep is a feature, not a bug.",
    "3am code is tomorrow's refactor.",
    "The duck recommends sleep.",
    "Burning the midnight oil, huh.",
]


def get_greeting():
    """Return a time-appropriate greeting."""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return random.choice(MORNING)
    elif 12 <= hour < 17:
        return random.choice(AFTERNOON)
    elif 17 <= hour < 22:
        return random.choice(EVENING)
    else:
        return random.choice(LATE_NIGHT)


# ── Idle quips ────────────────────────────────────────

IDLE_QUIPS = [
    "...",
    "*stretches*",
    "*looks around*",
    "Quack.",
    "Quiet in here.",
    "I should learn Rust.",
    "Wonder what's on GitHub trending.",
    "Is it Friday yet?",
    "*yawns*",
    "You ever just... stare at the screen?",
    "I'm not sleeping. I'm optimizing.",
    "Tab count: too many.",
    "Refactor or rewrite? The eternal question.",
    "Merge conflicts build character.",
    "I wonder what the cloud is thinking.",
    "Production is fine. Probably.",
    "*taps foot*",
    "Ship it.",
    "LGTM.",
    "Did you push that commit?",
    "One more feature... then done. For real this time.",
    "Git blame never lies.",
    "The tests pass locally, that counts right?",
    "Semicolons are a lifestyle choice.",
    "Ctrl+S. Ctrl+S. Ctrl+S.",
    "You know what this needs? More abstractions. Kidding.",
]

WALKING_QUIPS = [
    "Just passing through.",
    "*waddles*",
    "On patrol.",
    "Exploring the desktop.",
    "Left side... right side... left side...",
]

SITTING_QUIPS = [
    "*settles in*",
    "Comfy.",
    "Zen mode.",
    "Observing.",
    "Taking five.",
]

# ── Claude activity reactions ─────────────────────────

CLAUDE_STARTED = [
    "Oh, we're cooking now.",
    "Claude's on it.",
    "Here we go.",
    "Let's see what happens.",
    "The machine awakens.",
]

CLAUDE_FINISHED = [
    "Done! Ship it?",
    "Claude cooked.",
    "That was fast.",
    "Another one down.",
    "Check the diff.",
]


# ── Personality engine ────────────────────────────────

class Personality:
    """Manages idle quips and timed personality events."""

    def __init__(self, config=None):
        cfg = config or {}
        self.enabled = cfg.get("enabled", True)
        self.quip_min = cfg.get("quip_interval_min", 45)
        self.quip_max = cfg.get("quip_interval_max", 120)
        self.greeting_on_start = cfg.get("greeting_on_start", True)
        self.last_quip_time = time.time()
        self.next_quip_at = self._pick_next_interval()
        self._greeted = False
        self._recent = []  # avoid repeats

    def _pick_next_interval(self):
        return random.uniform(self.quip_min, self.quip_max)

    def _pick_unique(self, pool):
        """Pick a quip not in the recent 5."""
        available = [q for q in pool if q not in self._recent]
        if not available:
            self._recent.clear()
            available = pool
        pick = random.choice(available)
        self._recent.append(pick)
        if len(self._recent) > 5:
            self._recent.pop(0)
        return pick

    def get_startup_greeting(self):
        """Called once on boot."""
        if not self.enabled or not self.greeting_on_start:
            return None
        if self._greeted:
            return None
        self._greeted = True
        return get_greeting()

    def check_idle_quip(self, state):
        """Called periodically. Returns a quip string or None."""
        if not self.enabled:
            return None

        elapsed = time.time() - self.last_quip_time
        if elapsed < self.next_quip_at:
            return None

        self.last_quip_time = time.time()
        self.next_quip_at = self._pick_next_interval()

        if state == "sit":
            return self._pick_unique(SITTING_QUIPS)
        elif state in ("walk_left", "walk_right"):
            return self._pick_unique(WALKING_QUIPS)
        elif state == "idle":
            return self._pick_unique(IDLE_QUIPS)

        return None

    def on_claude_started(self):
        if not self.enabled:
            return None
        return self._pick_unique(CLAUDE_STARTED)

    def on_claude_finished(self):
        if not self.enabled:
            return None
        return self._pick_unique(CLAUDE_FINISHED)
