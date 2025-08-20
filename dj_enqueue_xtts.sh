#!/usr/bin/env bash
set -euo pipefail

ARTIST=${1:-}
TITLE=${2:-}
LANG=${3:-en}   # optional, defaults to English

if [[ -z "${ARTIST}" || -z "${TITLE}" ]]; then
  echo "Usage: $0 \"Artist\" \"Title\" [lang]" >&2
  exit 2
fi

VENV="/opt/ai-radio/xtts-venv"
PY="$VENV/bin/python"
APP="/opt/ai-radio/tts_xtts.py"
OUT_DIR="/opt/ai-radio/tts"
mkdir -p "${OUT_DIR}"

TS=$(date +%s)
OUT="${OUT_DIR}/intro_${TS}.mp3"

TEXT="Up next: ${TITLE} by ${ARTIST}."

# If your script accepts --voice, you can add:  --voice xtts
"${PY}" "${APP}" --text "${TEXT}" --lang "${LANG}" --out "${OUT}"

# Print the path for callers
echo "${OUT}"
