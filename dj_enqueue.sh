#!/usr/bin/env bash
set -euo pipefail

# ---------- config ----------
BASE="${BASE:-http://127.0.0.1:5055}"           # Flask base (for /api/next)
TTSDIR="${TTSDIR:-/opt/ai-radio/tts}"           # where we put generated audio
QUEUE_HOST="${QUEUE_HOST:-127.0.0.1}"
QUEUE_PORT="${QUEUE_PORT:-1234}"                # Liquidsoap telnet
QUEUE_ID="${QUEUE_ID:-tts}"                     # FIXED: your queue name is 'tts' not 'djq'

mkdir -p "$TTSDIR"

echo "DJ_ENQUEUE: Starting at $(date)" >> /var/tmp/dj_enqueue.log

# ---------- 1) get upcoming track (robust to list/single/empty) ----------
json="$(curl -fsS "$BASE/api/next" 2>/dev/null || echo '[]')"
echo "DJ_ENQUEUE: Got JSON: $json" >> /var/tmp/dj_enqueue.log

TITLE="$(
python3 - <<'PY' "$json"
import json,sys
try:
  data=json.loads(sys.argv[1] or '[]')
except Exception:
  data=[]
if isinstance(data,list) and data:
  data=data[0]
elif not isinstance(data,dict):
  data={}
print(data.get("title","Unknown Title"))
PY
)"

ARTIST="$(
python3 - <<'PY' "$json"
import json,sys
try:
  data=json.loads(sys.argv[1] or '[]')
except Exception:
  data=[]
if isinstance(data,list) and data:
  data=data[0]
elif not isinstance(data,dict):
  data={}
print(data.get("artist","Unknown Artist"))
PY
)"

echo "DJ_ENQUEUE: Artist='$ARTIST', Title='$TITLE'" >> /var/tmp/dj_enqueue.log

# ---------- 2) build the line FIRST ----------
LINE="$(/opt/ai-radio/gen_ai_dj_line.sh "$TITLE" "$ARTIST" 2>/dev/null || true)"
if [[ -z "${LINE}" ]]; then
  LINE="Up next: $TITLE by $ARTIST."
fi

echo "DJ_ENQUEUE: DJ Line: $LINE" >> /var/tmp/dj_enqueue.log

# ---------- 3) make filenames ----------
ts=$(date +%s)
safe() { echo "$1" | tr -cs '[:alnum:]_-' '_' | sed 's/_\+/_/g'; }
BASE_NAME="intro_$(safe "$ARTIST")_$(safe "$TITLE")_${ts}"
WAV="$TTSDIR/${BASE_NAME}.wav"
OUT="$TTSDIR/${BASE_NAME}.mp3"

# ---------- 4) synthesize with espeak‑ng (simple, works today) ----------
# Check if tools exist
if ! command -v espeak-ng >/dev/null 2>&1; then
    echo "DJ_ENQUEUE: ERROR - espeak-ng not found!" >> /var/tmp/dj_enqueue.log
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "DJ_ENQUEUE: ERROR - ffmpeg not found!" >> /var/tmp/dj_enqueue.log
    exit 1
fi

echo "DJ_ENQUEUE: Generating TTS audio..." >> /var/tmp/dj_enqueue.log
espeak-ng -v en-us -s 175 -w "$WAV" "$LINE" 2>> /var/tmp/dj_enqueue.log
ffmpeg -nostdin -y -i "$WAV" -codec:a libmp3lame -q:a 3 "$OUT" >/dev/null 2>&1

echo "DJ_ENQUEUE: Generated audio file: $OUT" >> /var/tmp/dj_enqueue.log

# ---------- 5) enqueue into Liquidsoap queue ----------
URI="$(python3 - <<'PY' "$OUT"
import sys, urllib.parse
p=sys.argv[1]
print('file://' + urllib.parse.quote(p))
PY
)"

echo "DJ_ENQUEUE: Pushing to queue: $URI" >> /var/tmp/dj_enqueue.log

# Use telnet with printf instead of nc
# FIXED: Use correct queue name 'tts' instead of 'djq'
{
    printf '%s.push %s\n' "$QUEUE_ID" "$URI"
    printf 'quit\n'
    sleep 1
} | telnet "$QUEUE_HOST" "$QUEUE_PORT" >> /var/tmp/dj_enqueue.log 2>&1

echo "DJ_ENQUEUE: Queued DJ intro for: $TITLE — $ARTIST -> $OUT" >> /var/tmp/dj_enqueue.log
echo "Queued DJ intro for: $TITLE — $ARTIST -> $OUT"