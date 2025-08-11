import sys
import subprocess
import time
import os

if len(sys.argv) < 3:
    print("Usage: make_intro.py \"artist\" \"title\" [output_path]")
    sys.exit(1)

artist = sys.argv[1]
title = sys.argv[2]

# Optional output path from Liquidsoap
if len(sys.argv) >= 4:
    output_path = sys.argv[3]
else:
    ts = int(time.time())
    output_path = f"/opt/ai-radio/tts/intro_{ts}.mp3"

prompt = f"""
You are an energetic radio DJ. Introduce the song "{title}" by {artist}. 
Give a fun fact about the artist or song.
Keep it under 12 words. Make it unique and upbeat. Do not repeat the title twice.
"""

try:
    result = subprocess.run(
        ["ollama", "run", "llama3.1", prompt],
        capture_output=True, text=True, timeout=8
    )
    intro_text = result.stdout.strip() or f"Now playing {title} by {artist}."
except Exception:
    intro_text = f"Now playing {title} by {artist}."

print(f"AI DJ intro: {intro_text}")

model_path = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"

with subprocess.Popen(
    ["piper", "--model", model_path, "--output_file", output_path],
    stdin=subprocess.PIPE
) as proc:
    proc.stdin.write(intro_text.encode("utf-8"))
    proc.stdin.close()
    proc.wait()

# Print the output path so Liquidsoap knows where to find it
print(output_path)