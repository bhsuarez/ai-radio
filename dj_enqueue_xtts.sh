#!/usr/bin/env bash
set -euo pipefail

ARTIST=${1:-}
TITLE=${2:-}
LANG=${3:-en}

# Get speaker from dj_settings.json if not provided as parameter
if [[ -n "${4:-}" ]]; then
    SPEAKER="$4"
elif [[ -n "${XTTS_SPEAKER:-}" ]]; then
    SPEAKER="$XTTS_SPEAKER"
else
    # Read from dj_settings.json
    SETTINGS_SPEAKER=$(python3 -c "
import json
try:
    with open('/opt/ai-radio/dj_settings.json', 'r') as f:
        settings = json.load(f)
    print(settings.get('tts_voice', 'Damien Black'))
except:
    print('Damien Black')
" 2>/dev/null)
    SPEAKER="${SETTINGS_SPEAKER:-Damien Black}"
fi

MODE="${5:-intro}"  # intro, outro, or custom

if [[ -z "${ARTIST}" || -z "${TITLE}" ]]; then
  echo "Usage: $0 \"Artist\" \"Title\" [lang] [speaker] [mode]" >&2
  exit 2
fi

VENV="/opt/ai-radio/xtts-venv"
PY="$VENV/bin/python"
APP="/opt/ai-radio/tts_xtts.py"
OUT_DIR="/opt/ai-radio/tts"
mkdir -p "${OUT_DIR}"

TS=$(date +%s)
OUT="${OUT_DIR}/${MODE}_${TS}.mp3"

# Generate AI DJ line based on mode
if [[ "$MODE" == "intro" ]]; then
    echo "DEBUG: Generating AI intro for upcoming track: ${TITLE} by ${ARTIST}" >&2
    export DJ_INTRO_MODE=1
    AI_TEXT=$(/opt/ai-radio/gen_ai_dj_line.sh "${TITLE}" "${ARTIST}" 2>/dev/null || echo "Up next: ${TITLE} by ${ARTIST}.")
elif [[ "$MODE" == "outro" ]]; then
    echo "DEBUG: Generating AI outro for completed track: ${TITLE} by ${ARTIST}" >&2
    export DJ_INTRO_MODE=0
    AI_TEXT=$(/opt/ai-radio/gen_ai_dj_line.sh "${TITLE}" "${ARTIST}" 2>/dev/null || echo "That was ${TITLE} by ${ARTIST}.")
else
    # Custom mode - use provided text or fallback
    AI_TEXT="${CUSTOM_TEXT:-Up next: ${TITLE} by ${ARTIST}.}"
    echo "DEBUG: Using custom text from environment: ${AI_TEXT}" >&2
fi

# Clean up the AI text (remove any control characters, extra whitespace)
TEXT=$(echo "$AI_TEXT" | tr -d '\r\n' | sed 's/^[[:space:]]\+//; s/[[:space:]]\+$//' | sed 's/  */ /g')

# Fallback if AI generation failed or returned empty
if [[ -z "$TEXT" || "$TEXT" == *"error"* || "$TEXT" == *"ERROR"* ]]; then
    if [[ "$MODE" == "intro" ]]; then
        TEXT="Up next: ${TITLE} by ${ARTIST}."
    else
        TEXT="That was ${TITLE} by ${ARTIST}."
    fi
    echo "DEBUG: Using fallback text due to AI generation issue" >&2
fi

echo "DEBUG: Speaker parameter: '$SPEAKER'" >&2
echo "DEBUG: Mode: '$MODE'" >&2
echo "DEBUG: AI generated text: '$TEXT'" >&2
echo "DEBUG: Expected output file: '$OUT'" >&2
echo "DEBUG: Full command: $PY $APP --text '$TEXT' --lang '$LANG' --speaker '$SPEAKER' --out '$OUT'" >&2

# Run the Python script and capture both stdout and stderr
if "${PY}" "${APP}" --text "${TEXT}" --lang "${LANG}" --speaker "${SPEAKER}" --out "${OUT}" 2>&1; then
    if [[ -f "${OUT}" ]]; then
        echo "DEBUG: Successfully created ${OUT}" >&2
        echo "DEBUG: File size: $(stat -c%s "${OUT}") bytes" >&2
        echo "DEBUG: File permissions: $(ls -la "${OUT}")" >&2
        
        # Create database entry for TTS
        echo "DEBUG: Creating database entry for TTS" >&2
        AUDIO_FILENAME=$(basename "${OUT}")
        TEXT_FILENAME="${AUDIO_FILENAME%.mp3}.txt"
        
        python3 -c "
import sys
sys.path.append('/opt/ai-radio')
from database import create_tts_entry
create_tts_entry(
    timestamp=${TS},
    text='${TEXT//\'/\\\'}',
    audio_filename='${AUDIO_FILENAME}',
    text_filename='${TEXT_FILENAME}',
    track_title='${TITLE//\'/\\\'}',
    track_artist='${ARTIST//\'/\\\'}',
    mode='${MODE}'
)
print('Database entry created successfully')
" || echo "WARNING: Failed to create database entry" >&2
        
        # Send TTS file to Liquidsoap via Harbor HTTP (replaces telnet)
        echo "DEBUG: Submitting TTS to Harbor at http://127.0.0.1:8002/tts" >&2
        
        if curl -f -X PUT "http://127.0.0.1:8002/tts" \
           -H "Content-Type: audio/mpeg" \
           --data-binary "@${OUT}" 2>&1; then
            echo "DEBUG: Successfully submitted TTS to Harbor" >&2
        else
            echo "WARNING: Failed to submit TTS to Harbor - file created but not queued" >&2
        fi
        
        # Output the file path for the calling script
        echo "${OUT}"
        exit 0
    else
        echo "ERROR: Python script succeeded but no output file found at ${OUT}" >&2
        echo "DEBUG: Contents of ${OUT_DIR}:" >&2
        ls -la "${OUT_DIR}" >&2 || true
        exit 1
    fi
else
    echo "ERROR: Python script failed" >&2
    echo "DEBUG: Contents of ${OUT_DIR}:" >&2
    ls -la "${OUT_DIR}" >&2 || true
    exit 1
fi