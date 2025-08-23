#!/usr/bin/env bash
set -euo pipefail

BASE="http://127.0.0.1:5055"
TTSDIR="/opt/ai-radio/tts"
mkdir -p "$TTSDIR"

# ElevenLabs configuration
ELEVENLABS_API_KEY="${ELEVENLABS_API_KEY:?ELEVENLABS_API_KEY environment variable is required}"
ELEVENLABS_VOICE_ID="${ELEVENLABS_VOICE_ID:-21m00Tcm4TlvDq8ikWAM}"
ELEVENLABS_MODEL="${ELEVENLABS_MODEL:-eleven_monolingual_v1}"

# Function to synthesize with ElevenLabs
synthesize_elevenlabs() {
    local text="$1"
    local output="$2"
    
    curl -X POST "https://api.elevenlabs.io/v1/text-to-speech/$ELEVENLABS_VOICE_ID" \
        -H "Accept: audio/mpeg" \
        -H "Content-Type: application/json" \
        -H "xi-api-key: $ELEVENLABS_API_KEY" \
        -d "{
            \"text\": $(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'),
            \"model_id\": \"$ELEVENLABS_MODEL\",
            \"voice_settings\": {
                \"stability\": 0.5,
                \"similarity_boost\": 0.5,
                \"style\": 0.0,
                \"use_speaker_boost\": true
            }
        }" \
        --output "$output" \
        --fail \
        --silent
}

# 1) Get the upcoming track
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

# 2) Safe filename + paths
ts=$(date +%s)
safe() { echo "$1" | tr -cs '[:alnum:]_-' '_' | sed 's/_\+/_/g'; }
OUT="$TTSDIR/intro_$(safe "$ARTIST")_$(safe "$TITLE")_${ts}.mp3"

# 3) Generate the spoken line
LINE="$(/opt/ai-radio/gen_ai_dj_line_elevenlabs.sh "$TITLE" "$ARTIST" 2>/dev/null || echo "Up next: $TITLE by $ARTIST.")"

# 4) Synthesize with ElevenLabs
if synthesize_elevenlabs "$LINE" "$OUT"; then
    echo "✓ ElevenLabs synthesis successful"
else
    echo "✗ ElevenLabs failed, falling back to Piper"
    # Fallback to Piper
    WAV="$TTSDIR/intro_${ts}.wav"
    echo "$LINE" | piper --model "$VOICE" --output_file "$WAV"
    ffmpeg -nostdin -y -i "$WAV" -codec:a libmp3lame -q:a 3 "$OUT" >/dev/null 2>&1 || mv "$WAV" "$OUT"
fi

# 5) Queue it in Liquidsoap (via Flask API)
curl -s -X POST "http://127.0.0.1:5055/api/enqueue" \
    -H "Content-Type: application/json" \
    -d "{\"file\":\"${OUT}\",\"title\":\"DJ Intro\",\"artist\":\"AI DJ\",\"comment\":\"${LINE}\"}" \
    >/dev/null 2>&1

echo "Queued DJ intro for: $TITLE – $ARTIST -> $OUT"