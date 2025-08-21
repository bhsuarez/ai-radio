#!/usr/bin/env bash
set -euo pipefail

ARTIST=${1:-}
TITLE=${2:-}
LANG=${3:-en}
# Fix: Properly handle speaker name with spaces
SPEAKER="${4:-${XTTS_SPEAKER:-Damien Black}}"

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
echo "DEBUG: Expected output file: '$OUT'" >&2
echo "DEBUG: Full command: $PY $APP --text '$TEXT' --lang '$LANG' --speaker '$SPEAKER' --out '$OUT'" >&2

# Run the Python script and capture both stdout and stderr
# Fix: Properly quote the speaker parameter
if "${PY}" "${APP}" --text "${TEXT}" --lang "${LANG}" --speaker "${SPEAKER}" --out "${OUT}" 2>&1; then
    if [[ -f "${OUT}" ]]; then
        echo "DEBUG: Successfully created ${OUT}" >&2
        echo "DEBUG: File size: $(stat -c%s "${OUT}") bytes" >&2
        echo "DEBUG: File permissions: $(ls -la "${OUT}")" >&2
        # This is the critical line - output the file path for the calling script
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