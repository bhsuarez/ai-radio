#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-1234}"
OUT="${OUT:-/opt/ai-radio/next.json}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# --- 1) get the queued request IDs (not the now-playing) ---
RID_LINE="$(
  {
    printf 'request.all\n'
    printf 'END\n'
    printf 'quit\n'
  } | telnet "$HOST" "$PORT" 2>/dev/null \
    | awk '/^request\.all/ {capture=1; next} /^END$/ {capture=0} capture' \
    | tail -n1
)"

# RID_LINE looks like: "53 54 55"
if [[ -z "${RID_LINE// /}" ]]; then
  echo "[]" > "$OUT"
  echo "Wrote 0 tracks to $OUT"
  exit 0
fi

# --- 2) expand each RID to metadata blocks ---
RIDS=($RID_LINE)

# helper: fetch one RID's metadata in key="value" lines
fetch_meta() {
  local rid="$1"
  {
    printf 'request.metadata %s\n' "$rid"
    printf 'END\n'
    printf 'quit\n'
  } | telnet "$HOST" "$PORT" 2>/dev/null \
    | awk '/^request\.metadata/{capture=1; next} /^END$/{capture=0} capture'
}

# --- 3) collect, parse, and emit JSON array ---
{
  echo '[]'
} | python3 - "$OUT" "${RIDS[@]}" <<'PY'
import json, os, sys, subprocess, shlex, re

OUT=sys.argv[1]
rids=sys.argv[2:]

def telnet_cmd(cmd):
    p = subprocess.run(
        ["telnet", os.environ.get("HOST","127.0.0.1"), os.environ.get("PORT","1234")],
        input=(cmd + "\nEND\nquit\n").encode(),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
    )
    # Extract only the block between our command echo and END
    lines=p.stdout.decode(errors="ignore").splitlines()
    block=[]
    capture=False
    for ln in lines:
        if ln.strip().startswith(cmd):
            capture=True
            continue
        if ln.strip()=="END":
            capture=False
            break
        if capture:
            block.append(ln.rstrip())
    return "\n".join(block)

items=[]
for rid in rids:
    block = telnet_cmd(f"request.metadata {rid}")
    if not block.strip():
        continue
    meta={}
    # lines are like: key="value"
    for ln in block.splitlines():
        m=re.match(r'^([^=]+)="(.*)"$', ln.strip())
        if not m: 
            continue
        k,v=m.group(1).strip(), m.group(2)
        # unescape \u0000 etc.
        try:
            v=bytes(v, "utf-8").decode("unicode_escape")
        except Exception:
            pass
        meta[k]=v

    # build a compact track object
    title   = meta.get("title") or ""
    artist  = meta.get("artist") or meta.get("albumartist") or ""
    album   = meta.get("album") or ""
    fname   = meta.get("filename") or meta.get("initial_uri","").replace("file://","")
    artwork = ""  # your /api/cover can fill this in client-side

    items.append({
        "rid": rid,
        "title": title,
        "artist": artist,
        "album": album,
        "filename": fname,
        "artwork_url": artwork
    })

# Optional: drop empties and dedupe by title|artist
seen=set()
clean=[]
for it in items:
    key=(it["title"].lower(), it["artist"].lower())
    if key in seen or (not it["title"] and not it["filename"]):
        continue
    seen.add(key)
    clean.append(it)

with open(OUT,"w") as f:
    json.dump(clean, f, indent=2)

print(f"Wrote {len(clean)} tracks to {OUT}")
PY