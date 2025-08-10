#!/usr/bin/env python3
import sys
import subprocess

if len(sys.argv) < 3:
    print("Usage: make_intro.py \"artist\" \"title\"")
    sys.exit(1)

artist = sys.argv[1]
title = sys.argv[2]

# Prompt for Ollama
prompt = f"""
You are an energetic radio DJ. Introduce the song "{title}" by {artist}.
Keep it under 12 words. Make it unique and upbeat. Do not repeat the title twice.
"""

try:
    # Run Ollama for text generation
    result = subprocess.run(
        ["ollama", "run", "llama3.1", prompt],
        capture_output=True, text=True, timeout=8
    )
    intro_text = result.stdout.strip()
    if not intro_text:
        intro_text = f"Now playing {title} by {artist}."
except Exception:
    intro_text = f"Now playing {title} by {artist}."

print(f"AI DJ intro: {intro_text}")

# Path to Norman model
model_path = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"

# Output file
output_path = "/opt/ai-radio/tts/next_intro.mp3"

# Generate intro speech with Piper
with subprocess.Popen(
    ["piper", "--model", model_path, "--output_file", output_path],
    stdin=subprocess.PIPE
) as proc:
    proc.stdin.write(intro_text.encode("utf-8"))
    proc.stdin.close()
    proc.wait()