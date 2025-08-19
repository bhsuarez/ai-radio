# /opt/ai-radio/refresh_next_from_requests.sh
#!/usr/bin/env bash
set -euo pipefail

LS_HOST="${LS_HOST:-127.0.0.1}"
LS_PORT="${LS_PORT:-1234}"
NEXT_JSON="${NEXT_JSON:-/opt/ai-radio/next.json}"
LIMIT="${LIMIT:-8}"                # max items to emit
LOG="/var/tmp/refresh_next_from_requests.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
echo "[$(ts)] START refresh via request.all" >> "$LOG"

# 1) Ask for request.all and keep the raw output for debugging
RAW="$({
  printf 'request.all\n'
  printf 'quit\n'
} | telnet "$LS_HOST" "$LS_PORT" 2>&1 | tr -d '\r')"

echo "$RAW" > /var/tmp/req_all_raw.txt

# 2) Extract ONLY the block between the echoed command and END, then pull all numbers
RID_BLOCK="$(printf '%s\n' "$RAW" | sed -n '/^request\.all$/,/^END$/p')"
RIDS="$(printf '%s\n' "$RID_BLOCK" | grep -Eo '[0-9]+' | head -n "$LIMIT" | tr '\n' ' ')"

if [[ -z "${RIDS// }" ]]; then
  echo "[]" > "$NEXT_JSON"
  echo "[$(ts)] Wrote 0 tracks to $NEXT_JSON (queue empty). See /var/tmp/req_all_raw.txt" | tee -a "$LOG"
  exit 0
fi

# 3) For each RID, fetch metadata and build objects
json_items=()
for rid in $RIDS; do
  META="$({
    printf 'request.metadata %s\n' "$rid"
    printf 'quit\n'
  } | telnet "$LS_HOST" "$LS_PORT" 2>/dev/null | tr -d '\r')"

  # convert key="value" lines to JSON
  obj="$(python3 - <<'PY' "$META" "$rid"
import json, re, sys, urllib.parse
text, rid = sys.argv[1], sys.argv[2]
pairs = dict(re.findall(r'^([^=\n]+)="(.*)"$', text, flags=re.M))
title   = pairs.get('title','')
artist  = pairs.get('artist') or pairs.get('albumartist','')
album   = pairs.get('album','')
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
  [[ -n "$obj" && "$obj" != "{}" ]] && json_items+=("$obj")
done

# 4) Write the array in order
printf '[\n' >"$NEXT_JSON"
for i in "${!json_items[@]}"; do
  [[ $i -gt 0 ]] && printf ',\n' >>"$NEXT_JSON"
  printf '  %s' "${json_items[$i]}" >>"$NEXT_JSON"
done
printf '\n]\n' >>"$NEXT_JSON"

count="$(jq 'length' "$NEXT_JSON" 2>/dev/null || echo 0)"
echo "[$(ts)] Wrote ${count} tracks to $NEXT_JSON" | tee -a "$LOG"