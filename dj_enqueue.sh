#!/usr/bin/env bash
set -euo pipefail

BASE="http://127.0.0.1:5055"   # change if your Flask binds elsewhere
TTSDIR="/opt/ai-radio/tts"
mkdir -p "$TTSDIR"

# 1) Get the upcoming track (UI already uses /api/next) 
#    It may return a list or single object; handle both.
# --- safe next-track parsing (no eval, handles spaces) ---
BASE="${BASE:-http://127.0.0.1:5055}"
json="$(curl -fsS "$BASE/api/next" 2>/dev/null || echo '[]')"

TITLE="$(
python3 - <<'PY' "$json"
import json,sys
data=json.loads(sys.argv[1] or '[]')
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
data=json.loads(sys.argv[1] or '[]')
if isinstance(data,list) and data:
    data=data[0]
elif not isinstance(data,dict):
    data={}
print(data.get("artist","Unknown Artist"))
PY
)"
# --- end safe parser ---

# 2) Safe filename + paths
ts=$(date +%s)
safe() { echo "$1" | tr -cs '[:alnum:]_-' '_' | sed 's/_\+/_/g'; }
OUT="$TTSDIR/intro_$(safe "$ARTIST")_$(safe "$TITLE")_${ts}.mp3"

# 3) Generate the spoken line (reuse your generator)
LINE="$(/opt/ai-radio/gen_ai_dj_line.sh "$TITLE" "$ARTIST" 2>/dev/null || echo "Up next: $TITLE by $ARTIST.")"

# 4) Synthesize with Piper (or your existing TTS tool)
WAV="$TTSDIR/intro_${ts}.wav"
echo "$LINE" | piper --model "$VOICE" --output_file "$WAV"
ffmpeg -nostdin -y -i "$WAV" -codec:a libmp3lame -q:a 3 "$OUT" >/dev/null 2>&1 || mv "$WAV" "$OUT"

# 5) Queue it in Liquidsoap (no quotes; must be file:// URI)
printf 'tts.push file://%s\n' "$OUT" | nc 127.0.0.1 1234

# (optional) print something for logs
echo "Queued DJ intro for: $TITLE — $ARTIST -> $OUT"