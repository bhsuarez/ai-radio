#!/usr/bin/env bash
set -euo pipefail

NEXT_JSON="${NEXT_JSON:-/opt/ai-radio/next.json}"
QUEUE_HOST="${QUEUE_HOST:-127.0.0.1}"
QUEUE_PORT="${QUEUE_PORT:-1234}"
LS_CMD="${LS_CMD:-output.icecast.metadata}"

mkdir -p "$(dirname "$NEXT_JSON")"

# 1) Ask Liquidsoap for the metadata blocks (like your sample) via netcat
RAW="$(
  { printf '%s\n' "$LS_CMD"; printf 'quit\n'; } \
  | nc -w 1 "$QUEUE_HOST" "$QUEUE_PORT" || true
)"

# Optional: drop a debug copy so we can inspect what came back
echo "$RAW" > /var/tmp/ls_meta.txt

# 2) Parse the blocks and write next.json
python3 - "$NEXT_JSON" <<'PY'
import json, re, sys

out_path = sys.argv[1]
raw = sys.stdin.read()

# Expect blocks like:
# --- 10 ---
# album="..."
# artist="..."
# ...
# --- 9 ---
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
    kv = re.match(r'^\s*([a-zA-Z0-9_]+)="?(.*?)"?\s*$', line)
    if kv:
        k, v = kv.group(1), kv.group(2)
        cur[k.lower()] = v

# If LS printed 10..1, this puts 1 first (nearest/up next first)
blocks.sort(key=lambda t: t[0])

tracks = []
for _, meta in blocks:
    title  = meta.get("title")  or "Unknown Title"
    artist = meta.get("artist") or "Unknown Artist"
    album  = meta.get("album")  or ""
    tracks.append({
        "title": title,
        "artist": artist,
        "album": album,
        "filename": "",       # not provided by this command
        "artwork_url": ""     # UI will try /api/test_find to fill this
    })

with open(out_path, "w") as f:
    json.dump(tracks, f)

print(f"Wrote {len(tracks)} tracks to {out_path}")
PY