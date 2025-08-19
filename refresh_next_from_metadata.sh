#!/usr/bin/env bash
set -euo pipefail

NEXT_JSON="${NEXT_JSON:-/opt/ai-radio/next.json}"
QUEUE_HOST="${QUEUE_HOST:-127.0.0.1}"
QUEUE_PORT="${QUEUE_PORT:-1234}"
LS_CMD="${LS_CMD:-output.icecast.metadata}"
MAX="${MAX:-12}"  # cap how many upcoming to keep

mkdir -p "$(dirname "$NEXT_JSON")"

# 1) Ask Liquidsoap via telnet (same pattern as your enqueue script)
RAW="$(
  {
    printf '%s\n' "$LS_CMD"
    printf 'quit\n'
    sleep 1
  } | telnet "$QUEUE_HOST" "$QUEUE_PORT" 2>/dev/null || true
)"

# Drop a debug copy for troubleshooting
echo "$RAW" > /var/tmp/ls_meta.txt

# 2) Parse the blocks and write next.json
python3 - "$NEXT_JSON" "$MAX" <<'PY'
import json, re, sys

out_path = sys.argv[1]
max_items = int(sys.argv[2])
raw = sys.stdin.read()

# Strip telnet noise lines
clean = []
for line in raw.splitlines():
    s = line.strip()
    if not s or "Escape character is" in s or s == "Connection closed." or s == "Bye!":
        continue
    if s.startswith("Trying ") or s.startswith("Connected to") or s.startswith("Liquidsoap"):
        continue
    clean.append(line.rstrip("\r"))
raw = "\n".join(clean)

# Parse blocks like --- 10 --- then key="value" lines
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
        k, v = kv.group(1).lower(), kv.group(2)
        cur[k] = v

# If Liquidsoap prints 10..1 (1 = currently playing),
# sort ascending so 1,2,3,... and DROP 1 so only "upcoming" remain.
blocks = [(i, d) for (i, d) in sorted(blocks, key=lambda t: (t[0] is None, t[0])) if i != 1]

tracks = []
for _, meta in blocks[:max_items]:
    title  = (meta.get("title")  or "Unknown Title").strip()
    artist = (meta.get("artist") or "Unknown Artist").strip()
    album  = (meta.get("album")  or "").strip()
    # filename/artwork_url unknown from this endpoint. UI can enrich later.
    tracks.append({
        "title": title,
        "artist": artist,
        "album": album,
        "filename": "",
        "artwork_url": ""
    })

with open(out_path, "w") as f:
    json.dump(tracks, f)

print(f"Wrote {len(tracks)} tracks to {out_path}")
PY

# quick UX
echo "Wrote $(jq 'length' "$NEXT_JSON" 2>/dev/null || echo 0) tracks to $NEXT_JSON"