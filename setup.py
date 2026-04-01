"""Setup script for Duck Pet Claude."""
import subprocess
import sys
import os

def main():
    print("=== Duck Pet Claude Setup ===\n")

    # Install dependencies
    print("[1/3] Installing dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r",
                          os.path.join(os.path.dirname(__file__), "requirements.txt")])

    # Generate sprites
    print("\n[2/3] Generating sprites...")
    sprite_gen = os.path.join(os.path.dirname(__file__), "sprite_gen.py")
    source_gif = os.path.join(os.path.dirname(__file__), "source.gif")
    if os.path.exists(source_gif):
        subprocess.check_call([sys.executable, sprite_gen, source_gif])
    else:
        print(f"  No source.gif found. Place your sprite GIF as 'source.gif' and re-run.")
        print(f"  The GIF should be an animated pixel art character on a solid background.")
        return

    # Check Claude Code
    print("\n[3/3] Checking Claude Code...")
    import shutil
    claude = shutil.which("claude")
    if claude:
        print(f"  Found Claude Code at: {claude}")
    else:
        print("  Claude Code not found in PATH.")
        print("  Install it from: https://claude.ai/download")
        print("  The duck terminal needs Claude Code to work.")

    print("\n=== Setup complete! ===")
    print("Run 'python pet.py' to launch your duck!")
    print("Or double-click 'start.bat' on Windows.")


if __name__ == "__main__":
    main()
