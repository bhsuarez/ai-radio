#!/usr/bin/env bash
set -euo pipefail

NEXT_JSON="${NEXT_JSON:-/opt/ai-radio/next.json}"
QUEUE_HOST="${QUEUE_HOST:-127.0.0.1}"
QUEUE_PORT="${QUEUE_PORT:-1234}"
# This is the command you tested manually:
LS_CMD="${LS_CMD:-output.icecast.metadata}"

mkdir -p "$(dirname "$NEXT_JSON")"

# 1) Ask Liquidsoap via telnet using the same pattern as dj_enqueue.sh
RAW="$(
  {
    printf '%s\n' "$LS_CMD"
    printf 'quit\n'
    # tiny pause to let telnet flush output like you do in dj_enqueue.sh
    sleep 1
  } | telnet "$QUEUE_HOST" "$QUEUE_PORT" 2>/dev/null || true
)"

# drop a debug copy so we can see exactly what came back
echo "$RAW" > /var/tmp/ls_meta.txt

# 2) Parse the '--- N ---' blocks into the array your UI expects
python3 - "$NEXT_JSON" <<'PY'
import json, re, sys

out_path = sys.argv[1]
raw = sys.stdin.read()

# Strip common telnet noise if present
# (banner lines, "Escape character", "Connection closed.", prompts)
clean = []
for line in raw.splitlines():
    if "Escape character is" in line: 
        continue
    if line.strip().startswith("Liquidsoap"):
        continue
    if line.strip().startswith("Trying ") or line.strip().startswith("Connected to"):
        continue
    if line.strip() == "Connection closed.":
        continue
    clean.append(line.rstrip("\r"))

raw = "\n".join(clean)

blocks = []
cur = {}
cur_idx = None

for line in raw.splitlines():
    m = re.match(r'^\s*---\s*(\d+)\s*---\s*$', line)
    if m:
        if cur:
            blocks.append((cur_idx, cur))
        cur = {}
        cur_idx = int(m.group(1))
        continue
    if line.strip() == "END":
        if cur:
            blocks.append((cur_idx, cur))
        break
    kv = re.match(r'^\s*([A-Za-z0-9_]+)="?(.*?)"?\s*$', line)
    if kv:
        k, v = kv.group(1), kv.group(2)
        cur[k.lower()] = v

# If LS prints highest first (e.g., 10..1), sort so 1 is first/up next
blocks.sort(key=lambda t: t[0] if t[0] is not None else 99999)

tracks = []
for _, meta in blocks:
    title  = (meta.get("title")  or "Unknown Title").strip()
    artist = (meta.get("artist") or "Unknown Artist").strip()
    album  = (meta.get("album")  or "").strip()
    tracks.append({
        "title": title,
        "artist": artist,
        "album": album,
        "filename": "",       # not provided by this endpoint
        "artwork_url": ""     # UI will try cover/test_find to fill this
    })

with open(out_path, "w") as f:
    json.dump(tracks, f)

print(f"Wrote {len(tracks)} tracks to {out_path}")
PY

# quick echo for shell UX
echo "Wrote $(jq 'length' "$NEXT_JSON") tracks to $NEXT_JSON"