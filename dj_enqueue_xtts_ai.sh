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
  echo "  mode: intro (default), outro, or custom" >&2
  exit 2
fi

VENV="/opt/ai-radio/xtts-venv"
PY="$VENV/bin/python"
APP="/opt/ai-radio/tts_xtts.py"
OUT_DIR="/opt/ai-radio/tts"
mkdir -p "${OUT_DIR}"

TS=$(date +%s)
OUT="${OUT_DIR}/${MODE}_${TS}.mp3"

echo "DEBUG: Starting AI+XTTS generation for ${MODE}: '${TITLE}' by '${ARTIST}'" >&2

# Generate AI DJ line based on mode
if [[ "$MODE" == "intro" ]]; then
    echo "DEBUG: Generating AI intro for upcoming track" >&2
    export DJ_INTRO_MODE=1
    export DJ_CUSTOM_PROMPT=""  # Use default intro prompt
elif [[ "$MODE" == "outro" ]]; then
    echo "DEBUG: Generating AI outro for completed track" >&2
    export DJ_INTRO_MODE=0
    export DJ_CUSTOM_PROMPT=""  # Use default outro prompt
else
    echo "DEBUG: Using custom mode" >&2
    export DJ_INTRO_MODE=0
fi

# Call the AI DJ line generation script
echo "DEBUG: Calling gen_ai_dj_line.sh with env DJ_INTRO_MODE=${DJ_INTRO_MODE}" >&2
AI_TEXT=$(/opt/ai-radio/gen_ai_dj_line.sh "${TITLE}" "${ARTIST}" 2>/dev/null || echo "")

# Clean up the AI text (remove any control characters, extra whitespace)
if [[ -n "$AI_TEXT" ]]; then
    # Remove ANSI color codes and clean whitespace
    TEXT=$(echo "$AI_TEXT" | sed 's/\x1b\[[0-9;]*m//g' | tr -d '\r\n' | sed 's/^[[:space:]]\+//; s/[[:space:]]\+$//' | sed 's/  */ /g')
else
    TEXT=""
fi

# Fallback if AI generation failed or returned empty
if [[ -z "$TEXT" || "$TEXT" == *"error"* || "$TEXT" == *"ERROR"* ]]; then
    if [[ "$MODE" == "intro" ]]; then
        TEXT="Up next: ${TITLE} by ${ARTIST}."
    else
        TEXT="That was ${TITLE} by ${ARTIST}."
    fi
    echo "DEBUG: Using fallback text due to AI generation issue" >&2
else
    echo "DEBUG: Using AI-generated text: '$TEXT'" >&2
fi

# Ensure text isn't too long (XTTS works better with shorter text)
if [[ ${#TEXT} -gt 200 ]]; then
    echo "DEBUG: Text too long (${#TEXT} chars), truncating" >&2
    TEXT="${TEXT:0:200}..."
fi

echo "DEBUG: Final text: '$TEXT'" >&2
echo "DEBUG: Speaker: '$SPEAKER'" >&2
echo "DEBUG: Output file: '$OUT'" >&2

# Run XTTS synthesis
echo "DEBUG: Starting XTTS synthesis..." >&2
if "${PY}" "${APP}" --text "${TEXT}" --lang "${LANG}" --speaker "${SPEAKER}" --out "${OUT}" 2>&1; then
    if [[ -f "${OUT}" && -s "${OUT}" ]]; then
        file_size=$(stat -c%s "${OUT}")
        # Validate it's a valid audio file and minimum size
        if [[ $file_size -lt 1000 ]]; then
            echo "ERROR: Output file too small (${file_size} bytes), likely corrupted" >&2
            rm -f "${OUT}"
            exit 1
        fi
        # Quick validation that it's actually an MP3
        if ! file "${OUT}" | grep -q "Audio\|MPEG\|MP3"; then
            echo "ERROR: Output file is not valid audio" >&2
            rm -f "${OUT}"
            exit 1
        fi
        echo "DEBUG: Successfully created ${OUT} (${file_size} bytes)" >&2
        
        # Save the transcript to a .txt file
        TXT_FILE="${OUT%.mp3}.txt"
        echo "$TEXT" > "$TXT_FILE"
        echo "DEBUG: Saved transcript to ${TXT_FILE}" >&2
        
        # Auto-queue in Liquidsoap if this is an intro (via Flask API)
        if [[ "$MODE" == "intro" ]]; then
            echo "DEBUG: Enqueuing intro via Flask API..." >&2
            safe_text=$(echo "$TEXT" | sed 's/"/\\"/g' | sed "s/'/\\'/g")
            
            curl -s -X POST "http://127.0.0.1:5055/api/enqueue" \
                -H "Content-Type: application/json" \
                -d "{\"file\":\"${OUT}\",\"title\":\"DJ Intro\",\"artist\":\"AI DJ\",\"comment\":\"${safe_text}\"}" \
                >/dev/null 2>&1 || {
                echo "DEBUG: Failed to queue via API, but file created successfully" >&2
            }
        fi
        
        # Output the file path for any calling script
        echo "${OUT}"
        exit 0
    else
        echo "ERROR: XTTS completed but no valid output file" >&2
        exit 1
    fi
else
    echo "ERROR: XTTS synthesis failed" >&2
    exit 1
fi