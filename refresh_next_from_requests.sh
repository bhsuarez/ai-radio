#!/usr/bin/env bash
set -euo pipefail

# Where to write the JSON that /api/next serves
NEXT_JSON="${NEXT_JSON:-/opt/ai-radio/next.json}"

# Liquidsoap telnet
LS_HOST="${LS_HOST:-127.0.0.1}"
LS_PORT="${LS_PORT:-1234}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

# 1) Get the queued request RIDs (these are *upcoming* items)
# Output looks like:  "53 54" then "END"
rid_line="$(
  { printf 'request.all\n'; printf 'quit\n'; } \
  | telnet "$LS_HOST" "$LS_PORT" 2>/dev/null \
  | awk 'NF==0{next} /^[0-9 ]+$/ {print; exit}'
)"

# If nothing queued, write empty array and exit cleanly
if [[ -z "${rid_line// }" ]]; then
  printf '[]\n' > "$NEXT_JSON"
  log "Wrote 0 tracks to $NEXT_JSON (queue empty)"
  exit 0
fi

# Split RIDs
read -r -a RIDS <<<"$rid_line"

# 2) For each RID, fetch metadata and capture a small JSON object
# We’ll keep the fields your front-end expects: title, artist, album, filename, plus artwork_url
items=()
for rid in "${RIDS[@]}"; do
  md="$(
    { printf 'request.metadata %s\n' "$rid"; printf 'quit\n'; } \
    | telnet "$LS_HOST" "$LS_PORT" 2>/dev/null
  )"

  # Convert key="value" lines to JSON with Python (robust to missing fields)
  js="$(python3 - "$md" <<'PY'
import json, re, sys
text = sys.stdin.read()

# Grab key="value" pairs
pairs = dict(re.findall(r'^([a-zA-Z0-9_./:-]+)="(.*)"$', text, flags=re.M))

# Normalize fields
title   = pairs.get('title') or ''
artist  = pairs.get('artist') or pairs.get('albumartist') or ''
album   = pairs.get('album') or ''
fname   = pairs.get('filename') or pairs.get('initial_uri','').replace('file://','')

# If the filename is a file://-style path in initial_uri, decode \uXXXX etc
fname = fname.encode('utf-8','ignore').decode('unicode_escape')

# Minimal object expected by the UI for "UPCOMING"
obj = {
  "title": title,
  "artist": artist,
  "album": album,
  "filename": fname,
}

# If we have a filename, give the UI a cover URL it can try
if fname:
  from urllib.parse import quote
  obj["artwork_url"] = f"/api/cover?file={quote(fname)}"

print(json.dumps(obj, ensure_ascii=False))
PY
)"
  # Ignore completely empty records
  if [[ "$js" != "{}" && -n "$js" ]]; then
    items+=("$js")
  fi
done

# 3) Write array in the same order Liquidsoap returned (earliest first)
printf '[\n' >"$NEXT_JSON"
for i in "${!items[@]}"; do
  if [[ $i -gt 0 ]]; then printf ',\n' >>"$NEXT_JSON"; fi
  printf '  %s' "${items[$i]}" >>"$NEXT_JSON"
done
printf '\n]\n' >>"$NEXT_JSON"

# 4) Done
count=${#items[@]}
log "Wrote $count tracks to $NEXT_JSON"
jq -c '.' "$NEXT_JSON" >/dev/null 2>&1 || true