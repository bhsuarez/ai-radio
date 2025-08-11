#!/bin/bash
set -euo pipefail

ARTIST="$1"
TITLE="$2"
FILE="$3"

# 1) Generate the DJ intro (make_intro.py prints the intro text on line 1)
INTRO_TEXT="$(python3 /opt/ai-radio/make_intro.py "$ARTIST" "$TITLE" "$FILE" | head -n 1)"

# 2) Wait until the MP3 actually exists and is non-empty
while [ ! -s "$FILE" ]; do sleep 0.2; done

# 3) Post the DJ event to the UI backend (port 5055)
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"type\":\"dj\",\"text\":\"$INTRO_TEXT\",\"audio_url\":\"$FILE\"}" \
  http://127.0.0.1:5055/api/log_event >/dev/null

# 4) Push the file to Liquidsoap TTS queue via telnet as a proper file:// URI
URI="$(python3 - <<'PY'
import sys, pathlib, urllib.parse
p = pathlib.Path(sys.argv[1]).resolve()
print(urllib.parse.urljoin('file:', urllib.parse.quote(str(p))))
PY
"$FILE")"

# send CRLF to be extra safe with telnet parsing
printf "tts.push %s\r\nquit\r\n" "$URI" | nc 127.0.0.1 1234