#!/bin/bash
set -euo pipefail

LOG="/var/log/ai-dj-gen.log"
OUT_DIR="/opt/ai-radio/tts_queue"
PIPER="/root/.local/bin/piper"
VOICE_ONNX="/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"
VOICE_CFG="${VOICE_ONNX}.json"
MODEL="llama3.2:1b"      # or: phi3:mini
STATION="AI Plex DJ"

mkdir -p "$OUT_DIR" "$(dirname "$LOG")"

# -------- Helpers --------
tod() {
  h=$(date +%H)
  if   [ "$h" -lt 12 ]; then echo morning
  elif [ "$h" -lt 18 ]; then echo afternoon
  elif [ "$h" -lt 22 ]; then echo evening
  else echo "late night"; fi
}

ensure_ollama() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet ollama || systemctl start ollama || true
  fi
  for i in $(seq 1 10); do
    if curl -sSf --max-time 1 http://127.0.0.1:11434/api/version >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# -------- Filenames --------
STAMP="$(date +%s)"
BASE="dj_${STAMP}"
OUT_WAV="${OUT_DIR}/${BASE}.wav"
OUT_TXT="${OUT_DIR}/${BASE}.txt"
VARIATION="$(date +%s%N)"

# -------- Build prompt --------
PROMPT=$(
  cat <<EOF
You are "Norman", an energetic but natural radio DJ for a personal music stream.

Goal:
- Say ONE short upbeat line to introduce the next set of songs.
- Be friendly & confident; 8–20 words; plain text only; no emojis/hashtags.

Context:
- Station: ${STATION}
- Time of day: $(tod)

Constraints:
- Vary your wording every time. Variation token: ${VARIATION}
EOF
)

echo "[$(date)] START base=$BASE" >> "$LOG"

# -------- Generate text via Ollama --------
if ensure_ollama; then
  DJ_TEXT="$(printf '%s' "$PROMPT" | ollama run "$MODEL" | tr -d '\r' | sed 's/^"//;s/"$//')"
else
  echo "[$(date)] Ollama not reachable — using fallback" >> "$LOG"
  DJ_TEXT="Stay tuned, more great tracks coming your way!"
fi

if [ -z "${DJ_TEXT// }" ]; then
  echo "[$(date)] ERROR: Empty DJ text" >> "$LOG"
  exit 1
fi

printf '%s\n' "$DJ_TEXT" > "$OUT_TXT"
echo "[$(date)] TEXT: $DJ_TEXT" >> "$LOG"

# -------- TTS with Piper --------
if ! command -v "$PIPER" >/dev/null 2>&1; then
  echo "[$(date)] Piper missing at $PIPER" >> "$LOG"
  exit 1
fi

if ! printf '%s' "$DJ_TEXT" | "$PIPER" \
    -m "$VOICE_ONNX" -c "$VOICE_CFG" \
    --length-scale 0.92 --volume 1.15 \
    -f "$OUT_WAV"; then
  echo "[$(date)] ERROR: Piper synthesis failed" >> "$LOG"
  exit 1
fi

echo "[$(date)] WAV: $OUT_WAV ($(du -h "$OUT_WAV" | cut -f1))" >> "$LOG"

# -------- Push once into Liquidsoap queue --------
echo "tts.push ${OUT_WAV}" | nc -w 1 127.0.0.1 1234 >/dev/null 2>&1 || \
  echo "[$(date)] WARN: Could not push to Liquidsoap" >> "$LOG"

# -------- Cleanup (>12h) --------
find "$OUT_DIR" -type f -mmin +720 -delete 2>/dev/null || true