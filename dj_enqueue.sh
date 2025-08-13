#!/usr/bin/env bash
set -euo pipefail

ARTIST="${1:-Unknown Artist}"
TITLE="${2:-Unknown Title}"
OUTFILE="${3:-/opt/ai-radio/tts/intro_$(date +%s).mp3}"

# 1) Generate DJ line text
LINE="$(/opt/ai-radio/gen_ai_dj_line.sh "$TITLE" "$ARTIST")"
echo "Generated DJ line: $LINE"

# 2) Convert to speech (replace with your preferred TTS command)
# Example using Piper (fast local TTS):
echo "$LINE" | piper --model en_US-amy-medium --output_file "$OUTFILE"

# 3) Push into Liquidsoap TTS queue
printf 'tts.push %s\nquit\n' "$OUTFILE" | nc 127.0.0.1 1234