#!/usr/bin/env bash
set -euo pipefail

# ---------- config ----------
BASE="${BASE:-http://127.0.0.1:5055}"           # Flask base (for /api/next)
TTSDIR="${TTSDIR:-/opt/ai-radio/tts}"           # where we put generated audio
QUEUE_HOST="${QUEUE_HOST:-127.0.0.1}"
QUEUE_PORT="${QUEUE_PORT:-1234}"                # Liquidsoap telnet
QUEUE_ID="${QUEUE_ID:-djq}"                     # your queue name is 'djq'

mkdir -p "$TTSDIR"

# ---------- 1) get upcoming track (robust to list/single/empty) ----------
json="$(curl -fsS "$BASE/api/next" 2>/dev/null || echo '[]')"

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

# ---------- 2) build the line FIRST ----------
LINE="$(/opt/ai-radio/gen_ai_dj_line.sh "$TITLE" "$ARTIST" 2>/dev/null || true)"
if [[ -z "${LINE}" ]]; then
  LINE="Up next: $TITLE by $ARTIST."
fi

# ---------- 3) make filenames ----------
ts=$(date +%s)
safe() { echo "$1" | tr -cs '[:alnum:]_-' '_' | sed 's/_\+/_/g'; }
BASE_NAME="intro_$(safe "$ARTIST")_$(safe "$TITLE")_${ts}"
WAV="$TTSDIR/${BASE_NAME}.wav"
OUT="$TTSDIR/${BASE_NAME}.mp3"

# ---------- 4) synthesize with espeak‑ng (simple, works today) ----------
espeak-ng -v en-us -s 175 -w "$WAV" "$LINE"
ffmpeg -nostdin -y -i "$WAV" -codec:a libmp3lame -q:a 3 "$OUT" >/dev/null 2>&1

# ---------- 5) enqueue into Liquidsoap queue ----------
URI="$(python3 - <<'PY' "$OUT"
import sys, urllib.parse
p=sys.argv[1]
print('file://' + urllib.parse.quote(p))
PY
)"

# Use djq.push and close telnet with quit so nc doesn't hang
printf '%s.push %s\nquit\n' "$QUEUE_ID" "$URI" | nc "$QUEUE_HOST" "$QUEUE_PORT"

echo "Queued DJ intro for: $TITLE — $ARTIST -> $OUT"