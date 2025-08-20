#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   dj_enqueue_xtts.sh "Artist" "Title" [lang] [speaker_ref_wav]
ARTIST="${1:-}"
TITLE="${2:-}"
LANG="${3:-en}"
SPEAKER="${4:-/opt/ai-radio/voices/dj.wav}"

if [[ -z "$ARTIST" || -z "$TITLE" ]]; then
  echo "Usage: $0 \"Artist\" \"Title\" [lang] [speaker_wav]" >&2
  exit 2
fi

# --- paths / settings ---
VENV_PY="/opt/ai-radio/xtts-venv/bin/python"
GEN_PY="/opt/ai-radio/tts_xtts.py"
OUTDIR="/opt/ai-radio/tts"
mkdir -p "$OUTDIR"

# Build the DJ line text (simple; your AI script can pass richer lines later)
LINE="Up next: ${TITLE} by ${ARTIST}."

# Choose output path
TS="$(date +%s)"
OUT="$OUTDIR/intro_${TS}.mp3"

# Generate speech
if [[ -f "$SPEAKER" ]]; then
  "$VENV_PY" "$GEN_PY" --text "$LINE" --voice "$SPEAKER" --lang "$LANG" --out "$OUT"
else
  # no reference voice — fall back to generic
  "$VENV_PY" "$GEN_PY" --text "$LINE" --lang "$LANG" --out "$OUT"
fi

# Push into Liquidsoap queue via telnet
echo "request.push \"$OUT\"" | nc 127.0.0.1 1234 >/dev/null 2>&1 || true

# Optional: print JSON for app logs
printf '{ "ok": true, "audio": "%s", "text": "%s" }\n' "$OUT" "$LINE"
