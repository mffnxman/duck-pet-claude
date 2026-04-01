"""Extract frames from the original duck GIF and create sprite sheets.
Uses the ACTUAL pixel data — no approximation.
"""
from PIL import Image, ImageDraw
import os, sys

SPRITE_DIR = os.path.join(os.path.dirname(__file__), "sprites")
os.makedirs(SPRITE_DIR, exist_ok=True)

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "source.gif")
BG_COLOR = (50, 60, 57)  # Dark background in original

# Load source GIF
gif = Image.open(SRC)
print(f"Source: {gif.size}, {gif.n_frames} frames")

# Extract all frames, replace background with transparency
frames = []
for i in range(gif.n_frames):
    gif.seek(i)
    f = gif.convert("RGBA")
    pixels = f.load()
    w, h = f.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if (r, g, b) == BG_COLOR:
                pixels[x, y] = (0, 0, 0, 0)
    frames.append(f)

print(f"Extracted {len(frames)} transparent frames")

# The animation is a walk cycle (8 frames)
# Use the raw frames directly — they're already perfect

# === WALK LEFT (full 8-frame cycle — duck faces left in original) ===
walk_l = frames.copy()
walk_l[0].save(os.path.join(SPRITE_DIR, "walk_left.gif"),
    save_all=True, append_images=walk_l[1:],
    duration=120, loop=0, disposal=2, transparency=0)

# === WALK RIGHT (mirror) ===
walk_r = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in frames]
walk_r[0].save(os.path.join(SPRITE_DIR, "walk_right.gif"),
    save_all=True, append_images=walk_r[1:],
    duration=120, loop=0, disposal=2, transparency=0)

# === IDLE (facing RIGHT) ===
idle = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in [frames[3], frames[3], frames[4], frames[4], frames[3], frames[0]]]
idle[0].save(os.path.join(SPRITE_DIR, "idle.gif"),
    save_all=True, append_images=idle[1:],
    duration=400, loop=0, disposal=2, transparency=0)

# === SIT (body bobs, no feet — floating in place) ===
sit = []
for i, fi in enumerate([3, 4, 5, 4, 3, 4]):
    f = frames[fi].copy()
    pixels = f.load()
    w, h = f.size
    OR_COLOR = (223, 113, 38)
    # Remove orange feet pixels (bottom portion)
    for y in range(int(h * 0.75), h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if (r, g, b) == OR_COLOR:
                pixels[x, y] = (0, 0, 0, 0)
    # Slight bob on alternate frames
    if i in (1, 2, 3):
        shifted = Image.new("RGBA", f.size, (0, 0, 0, 0))
        shifted.paste(f, (0, -2))
        sit.append(shifted)
    else:
        sit.append(f)
# Flip to face right
sit = [s.transpose(Image.FLIP_LEFT_RIGHT) for s in sit]
sit[0].save(os.path.join(SPRITE_DIR, "sit.gif"),
    save_all=True, append_images=sit[1:],
    duration=350, loop=0, disposal=2, transparency=0)

# === ACTIVE (faster walk cycle — duck is busy) ===
active = frames.copy()
active[0].save(os.path.join(SPRITE_DIR, "active.gif"),
    save_all=True, append_images=active[1:],
    duration=80, loop=0, disposal=2, transparency=0)

# === WAVE (use upright frames with a wing overlay) ===
wave = [frames[3], frames[4], frames[5], frames[4], frames[3], frames[4]]
wave[0].save(os.path.join(SPRITE_DIR, "wave.gif"),
    save_all=True, append_images=wave[1:],
    duration=150, loop=0, disposal=2, transparency=0)

# === TALK (alternate frames for beak-like movement) ===
talk = [frames[3], frames[0], frames[3], frames[0], frames[3], frames[0]]
talk[0].save(os.path.join(SPRITE_DIR, "talk.gif"),
    save_all=True, append_images=talk[1:],
    duration=200, loop=0, disposal=2, transparency=0)

# === CELEBRATE (use the most dynamic frames, sped up) ===
cele = [frames[0], frames[1], frames[2], frames[3], frames[2], frames[1]]
cele[0].save(os.path.join(SPRITE_DIR, "celebrate.gif"),
    save_all=True, append_images=cele[1:],
    duration=100, loop=0, disposal=2, transparency=0)

# === LISTEN (still pose) ===
listen = [frames[3], frames[4], frames[3], frames[4]]
listen[0].save(os.path.join(SPRITE_DIR, "listen.gif"),
    save_all=True, append_images=listen[1:],
    duration=300, loop=0, disposal=2, transparency=0)

print("\nSprites generated from original frames:")
for fn in sorted(os.listdir(SPRITE_DIR)):
    if fn.endswith('.gif'):
        img = Image.open(os.path.join(SPRITE_DIR, fn))
        print(f"  {fn}: {img.size[0]}x{img.size[1]}, {img.n_frames} frames")
print("\nDone — sprites generated!")
