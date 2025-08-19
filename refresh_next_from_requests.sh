#!/usr/bin/env bash
set -euo pipefail

LS_HOST=127.0.0.1
LS_PORT=1234
NEXT_JSON="/opt/ai-radio/next.json"
LIMIT=8

# helper to send a command via /dev/tcp
ls_cmd() {
  local cmd="$1"
  exec 3<>"/dev/tcp/$LS_HOST/$LS_PORT"
  printf "%s\r\nquit\r\n" "$cmd" >&3
  # give LS a moment to respond
  sleep 0.3
  timeout 1s cat <&3 || true
  exec 3<&-
  exec 3>&-
}

# 1) get request.all
RAW="$(ls_cmd 'request.all')"
echo "$RAW" > /var/tmp/req_all_debug.txt

# extract RIDs (just numbers on first line)
RIDS="$(printf '%s\n' "$RAW" | grep -Eo '^[0-9 ]+' | head -n1)"

if [[ -z "${RIDS// }" ]]; then
  echo "[]" > "$NEXT_JSON"
  echo "[refresh_next_from_requests] No upcoming requests found"
  exit 0
fi

json_items=()
for rid in $RIDS; do
  META="$(ls_cmd "request.metadata $rid")"
  obj="$(python3 - <<'PY' "$META" "$rid"
import json, re, sys, urllib.parse
meta, rid = sys.argv[1], sys.argv[2]
pairs = dict(re.findall(r'^([^=\n]+)="(.*)"$', meta, flags=re.M))
title = pairs.get('title','')
artist = pairs.get('artist') or pairs.get('albumartist','')
album = pairs.get('album','')
filename = pairs.get('filename') or pairs.get('initial_uri','').replace('file://','')
filename = filename.encode('utf-8','ignore').decode('unicode_escape')
art = f"/api/cover?file={urllib.parse.quote(filename)}" if filename else ""
print(json.dumps({
  "rid": int(rid),
  "title": title,
  "artist": artist,
  "album": album,
  "filename": filename,
  "artwork_url": art
}, ensure_ascii=False))
PY
)"
  [[ -n "$obj" ]] && json_items+=("$obj")
done

# write array
printf '[\n' >"$NEXT_JSON"
for i in "${!json_items[@]}"; do
  [[ $i -gt 0 ]] && printf ',\n' >>"$NEXT_JSON"
  printf '  %s' "${json_items[$i]}" >>"$NEXT_JSON"
done
printf '\n]\n' >>"$NEXT_JSON"

jq . "$NEXT_JSON" || cat "$NEXT_JSON"