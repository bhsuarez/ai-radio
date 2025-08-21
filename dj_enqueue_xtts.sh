#!/usr/bin/env bash
set -euo pipefail

ARTIST=${1:-}
TITLE=${2:-}
LANG=${3:-en}
SPEAKER=${4:-${XTTS_SPEAKER:-"Damien Black"}}

if [[ -z "${ARTIST}" || -z "${TITLE}" ]]; then
  echo "Usage: $0 \"Artist\" \"Title\" [lang] [speaker]" >&2
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

echo "DEBUG: Speaker parameter: '$SPEAKER'" >&2
echo "DEBUG: Full command: $PY $APP --text '$TEXT' --lang '$LANG' --speaker '$SPEAKER' --out '$OUT'" >&2

"${PY}" "${APP}" --text "${TEXT}" --lang "${LANG}" --speaker "${SPEAKER}" --out "${OUT}"

if [[ -f "${OUT}" ]]; then
    echo "DEBUG: Successfully created ${OUT}" >&2
    echo "DEBUG: File size: $(stat -c%s "${OUT}") bytes" >&2
    echo "${OUT}"
    exit 0
else
    echo "ERROR: XTTS script completed but no output file found" >&2
    exit 1
fi