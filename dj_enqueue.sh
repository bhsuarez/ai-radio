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

# ---------- NEW: Check for recent DJ activity ----------
echo "DJ_ENQUEUE: Checking for recent DJ activity..." >> /var/tmp/dj_enqueue.log

# Get recent history to check for DJ lines
history_json="$(curl -fsS "$BASE/api/history" 2>/dev/null || echo '[]')"

# Check if there's a recent DJ line (within last 45 seconds)
has_recent_dj="$(python3 - <<'PY' "$history_json"
import json, sys, time
try:
    history = json.loads(sys.argv[1] or '[]')
except:
    history = []

current_time = int(time.time() * 1000)
recent_threshold = 45000  # 45 seconds

for event in history[:5]:  # Check last 5 events
    if event.get("type") == "dj":
        event_time = event.get("time", 0)
        if (current_time - event_time) < recent_threshold:
            print("yes")
            sys.exit(0)
print("no")
PY
)"

if [[ "$has_recent_dj" == "yes" ]]; then
    echo "DJ_ENQUEUE: Recent DJ line found, skipping generation to avoid duplicates" >> /var/tmp/dj_enqueue.log
    echo "DJ_ENQUEUE: Skipping - recent DJ activity detected"
    exit 0
fi

echo "DJ_ENQUEUE: No recent DJ activity, proceeding with generation" >> /var/tmp/dj_enqueue.log

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

# Skip if we don't have meaningful track info
if [[ "$TITLE" == "Unknown Title" && "$ARTIST" == "Unknown Artist" ]]; then
    echo "DJ_ENQUEUE: No meaningful track info, skipping" >> /var/tmp/dj_enqueue.log
    exit 0
fi

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

# ---------- 4) synthesize with Piper CLI (fallback to espeak‑ng) ----------
# You can use either a model NAME (downloaded via `python3 -m piper.download_voices`)
# or an ONNX file path you already have.
VOICE_NAME="${VOICE_NAME:-en_US-norman-medium}"
VOICE_PATH="${VOICE_PATH:-/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx}"

make_wav_with_piper() {
  # Try model name first (fast if voices were installed via piper downloader)
  if python3 -m piper -m "$VOICE_NAME" -f "$WAV" -- "$LINE"; then
    return 0
  fi
  # Fallback to explicit ONNX path
  if [ -f "$VOICE_PATH" ] && python3 -m piper -m "$VOICE_PATH" -f "$WAV" -- "$LINE"; then
    return 0
  fi
  return 1
}

if make_wav_with_piper; then
  :
else
  echo "DJ_ENQUEUE: WARN - Piper failed; using espeak-ng fallback." >> /var/tmp/dj_enqueue.log
  espeak-ng -v en-us -s 175 -w "$WAV" "$LINE" 2>> /var/tmp/dj_enqueue.log
fi

# Convert to a "radio-safe" MP3: 44.1kHz, stereo, a bit louder, tiny tail pad
ffmpeg -nostdin -y -i "$WAV" \
  -ar 44100 -ac 2 -af "volume=9dB,apad=pad_dur=0.5" \
  -codec:a libmp3lame -q:a 3 "$OUT" >/dev/null 2>&1

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

# ---------- 6) NEW: Notify Flask about the DJ line for timeline ----------
echo "DJ_ENQUEUE: Notifying Flask about DJ line..." >> /var/tmp/dj_enqueue.log

# Create the audio URL for the web interface
AUDIO_URL="/api/tts-file/$(basename "$OUT")"

# Send the DJ event to Flask
curl -fsS -X POST "$BASE/api/tts_queue" \
  -H "Content-Type: application/json" \
  -d @- <<EOF >> /var/tmp/dj_enqueue.log 2>&1
{
  "text": "$LINE",
  "audio_url": "$AUDIO_URL",
  "external_generated": true
}
EOF

echo "DJ_ENQUEUE: Queued DJ intro for: $TITLE — $ARTIST -> $OUT" >> /var/tmp/dj_enqueue.log
echo "Queued DJ intro for: $TITLE — $ARTIST -> $OUT"