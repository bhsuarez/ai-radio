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

# 1) fetch RIDs currently queued
RID_LINES="$({
  printf 'request.all\n'
  printf 'quit\n'
} | telnet "$LS_HOST" "$LS_PORT" 2>>"$LOG" | tr -d '\r')"

# Extract numeric RIDs (space/newline separated list, ends with "END")
RIDS="$(echo "$RID_LINES" | awk '/^[0-9]+([ \t]+[0-9]+)*$/{print}')" || true
RIDS="$(echo "$RIDS" | tr ' ' '\n' | grep -E '^[0-9]+$' | head -n "$LIMIT" || true)"

if [[ -z "${RIDS}" ]]; then
  echo "[]" > "$NEXT_JSON"
  echo "[$(ts)] Wrote 0 tracks to $NEXT_JSON (queue empty)" | tee -a "$LOG"
  exit 0
fi

# 2) for each RID, pull metadata and turn into JSON
json_items=()
i=0
while read -r rid; do
  [[ -z "$rid" ]] && continue
  ((i++))

  META="$({
    printf 'request.metadata %s\n' "$rid"
    printf 'quit\n'
  } | telnet "$LS_HOST" "$LS_PORT" 2>>"$LOG" | tr -d '\r')"

  # pull key="value" pairs into shell vars
  getv(){ echo "$META" | awk -v k="$1" -F'=' '$1==k{ sub(/^"/,"",$2); sub(/"$/,"",$2); print $2; exit }'; }
  title="$(getv title)"
  artist="$(getv artist)"
  album="$(getv album)"
  filename="$(getv filename)"
  [[ -z "$filename" ]] && filename="$(getv initial_uri | sed 's#^file://##')"

  # build a JSON object safely with jq
  obj="$(jq -n --arg title "${title:-}" \
               --arg artist "${artist:-}" \
               --arg album "${album:-}" \
               --arg filename "${filename:-}" \
               --arg rid "$rid" \
               '{title:$title, artist:$artist, album:$album, filename:$filename, rid:$rid|tonumber}')"
  json_items+=("$obj")
done <<< "$RIDS"

# 3) write array
jq -n --argjson a "[${json_items[*]:-}]" '$a' > "$NEXT_JSON"
count="$(jq 'length' < "$NEXT_JSON")"
echo "jq . $NEXT_JSON"
jq . "$NEXT_JSON"
echo "[$(ts)] Wrote ${count} tracks to $NEXT_JSON" | tee -a "$LOG"