#!/bin/bash
set -euo pipefail

ARTIST="$1"
TITLE="$2"
FILE="$3"

# Optional delay (in seconds) before posting/pushing the clip
DELAY="${DELAY:-0}"
if [ "$DELAY" -gt 0 ] 2>/dev/null; then
  sleep "$DELAY"
fi

# 1) Generate the DJ intro (make_intro.py prints the intro text on line 1)
INTRO_TEXT="$(python3 /opt/ai-radio/make_intro.py "$ARTIST" "$TITLE" "$FILE" | head -n 1)"

# 2) Wait until the MP3 actually exists and is non-empty
while [ ! -s "$FILE" ]; do sleep 0.2; done

# 3) Post the DJ event to the UI backend (port 5055)
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"type\":\"dj\",\"text\":\"$INTRO_TEXT\",\"audio_url\":\"$FILE\"}" \
  http://127.0.0.1:5055/api/log_event >/dev/null

# 4) Push the file to Liquidsoap TTS queue via telnet as a proper file:// URI
URI="$(FILE="$FILE" python3 - <<'PY'
import os, pathlib, urllib.parse
p = pathlib.Path(os.environ['FILE']).resolve()
print(urllib.parse.urljoin('file:', urllib.parse.quote(str(p))))
PY
)"

printf "tts.push %s\r\nquit\r\n" "$URI" | nc 127.0.0.1 1234