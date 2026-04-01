"""
Voice module for Desktop Pet Claude — v2.
- STT: RealtimeSTT (faster-whisper + VAD — auto detects speech)
- TTS: Edge TTS + pygame (no PowerShell, no terminal windows)
- Brain: Claude CLI
"""
import asyncio
import tempfile
import os
import subprocess
import time
import sys

import edge_tts
import pygame

# Suppress pygame welcome message
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1"

# ── Config ──────────────────────────────────────────
VOICE = "en-US-GuyNeural"
VOICE_RATE = "+5%"
VOICE_PITCH = "+0Hz"

TEMP_DIR = tempfile.mkdtemp(prefix="claude_pet_")
import shutil
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser(os.path.join("~", ".local", "bin", "claude.exe"))

from brain import search as brain_search
CREATE_NO_WINDOW = 0x08000000

# Initialize pygame mixer once
pygame.mixer.init()


def speak(text):
    """Generate and play speech — no windows, no subprocess."""
    print(f"[Voice] Speaking: {text[:80]}...")
    try:
        path = os.path.join(TEMP_DIR, f"s_{int(time.time()*1000)}.mp3")
        asyncio.run(edge_tts.Communicate(
            text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH
        ).save(path))

        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        pygame.mixer.music.unload()

        try:
            os.remove(path)
        except OSError:
            pass
    except Exception as e:
        print(f"[Voice] TTS error: {e}")


def ask_claude(text, history):
    """Send to Claude CLI, return response."""
    history.append({"role": "user", "content": text})
    recent = history[-20:]

    # Search Obsidian vault for relevant context
    vault_context = brain_search(text)

    system = (
        "You are Claude, a desktop pet companion speaking out loud. "
        "Keep responses SHORT (1-3 sentences). Be natural, casual, friendly. "
        "Be friendly and casual. "
        "No markdown, no lists, no formatting — just natural speech."
    )

    prompt = f"{system}\n\n"
    if vault_context:
        prompt += f"You have access to the user's notes. Use them to answer if relevant:\n{vault_context}\n\n"
    prompt += "Conversation:\n"
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Claude"
        prompt += f"{role}: {msg['content']}\n"
    prompt += "Claude:"

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--no-input"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW
        )
        response = result.stdout.strip()
        if not response:
            response = "Hey, say that again?"
        history.append({"role": "assistant", "content": response})
        return response
    except subprocess.TimeoutExpired:
        return "Took too long, try again."
    except Exception as e:
        print(f"[Voice] Claude error: {e}")
        return "Something glitched, try again."


def run_conversation():
    """Main voice conversation using RealtimeSTT."""
    print("[Voice] Initializing RealtimeSTT...")

    try:
        from RealtimeSTT import AudioToTextRecorder
    except ImportError:
        print("[Voice] RealtimeSTT not installed! pip install RealtimeSTT")
        return

    history = []
    last_text = None
    waiting_for_speech = True

    def on_text(text):
        nonlocal last_text
        if text and text.strip():
            last_text = text.strip()
            print(f"[Voice] Heard: {last_text}")

    # Greet
    speak("Hey, what's up?")

    print("[Voice] Starting recorder...")
    recorder = AudioToTextRecorder(
        model="tiny.en",
        language="en",
        spinner=False,
        silero_sensitivity=0.2,   # Very sensitive — catch speech easily
        webrtc_sensitivity=1,     # Light noise filter only
        post_speech_silence_duration=1.2,
        min_length_of_recording=0.3,
        min_gap_between_recordings=0.3,
        enable_realtime_transcription=False,
        # Uses system default microphone
    )

    print("[Voice] Listening... (say 'bye' or 'stop' to end)")

    try:
        while True:
            last_text = None

            # This blocks until speech is detected and transcribed
            recorder.text(on_text)

            if last_text is None:
                continue

            text = last_text

            # Exit check
            exit_words = ["goodbye", "bye", "stop", "quit", "cancel", "never mind"]
            if any(w in text.lower() for w in exit_words):
                speak("Catch you later!")
                break

            # Get Claude response
            print("[Voice] Thinking...")
            response = ask_claude(text, history)
            speak(response)

    except KeyboardInterrupt:
        print("[Voice] Interrupted")
    finally:
        recorder.shutdown()
        pygame.mixer.quit()
        print("[Voice] Done!")


if __name__ == "__main__":
    run_conversation()
