#!/usr/bin/env bash
set -euo pipefail

# Ensure Ollama uses the right model directory
export OLLAMA_MODELS="/mnt/music/ai-dj/ollama"

TITLE="${1:-}"
ARTIST="${2:-}"

# ElevenLabs Configuration
ELEVENLABS_API_KEY="${ELEVENLABS_API_KEY:?ELEVENLABS_API_KEY environment variable is required}"
ELEVENLABS_VOICE_ID="${ELEVENLABS_VOICE_ID:-21m00Tcm4TlvDq8ikWAM}" # Default to Rachel voice
ELEVENLABS_MODEL="${ELEVENLABS_MODEL:-eleven_monolingual_v1}"

# AI Model Configuration  
MODEL="${MODEL:-llama3.2:3b}"
PROVIDER="${PROVIDER:-ollama}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"

STYLES=("energetic hype" "laid-back chill" "warm late-night" "quirky college-radio" "BBC-style concise" "retro 90s alt" "clubby electronic")
STYLE="${STYLE:-$(printf '%s\n' "${STYLES[@]}" | shuf -n1)}"

if (( RANDOM % 100 < 60 )); then
  TRIVIA_LINE="If you genuinely know one short, widely known fact about ${ARTIST:-the artist} or the song '${TITLE:-this track}', include it; if not, skip trivia."
else
  TRIVIA_LINE=""
fi

PROMPT="You are a radio DJ. Style: ${STYLE}.
In 1–2 sentences, speak about the song '${TITLE:-this track}' by ${ARTIST:-an unknown artist}.
${TRIVIA_LINE}
Keep it natural, conversational, and clean. No emojis or hashtags. Do not invent facts."

collapse_line() {
  tr -d '\r' | sed 's/^[[:space:]]\+//; s/[[:space:]]\+$//' | awk 'NF' | paste -sd' ' - | sed 's/  */ /g'
}

run_ollama() {
  ollama run "$MODEL" "$PROMPT" | collapse_line
}

run_openai() {
  : "${OPENAI_API_KEY:?OPENAI_API_KEY is required when PROVIDER=openai}"
  resp="$(curl -sS https://api.openai.com/v1/chat/completions \
    -H "Authorization: Bearer ${OPENAI_API_KEY}" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "model": "${OPENAI_MODEL}",
  "temperature": 0.8,
  "max_tokens": 120,
  "messages": [
    {"role":"system","content":"You are a concise, engaging radio DJ. No emojis or hashtags. Never invent facts."},
    {"role":"user","content": ${PROMPT@Q} }
  ]
}
EOF
)"
  python3 - "$resp" <<'PY' | collapse_line
import sys, json
data = json.loads(sys.argv[1])
print(data["choices"][0]["message"]["content"])
PY
}

# Generate the DJ line text
case "$PROVIDER" in
  openai) DJ_TEXT="$(run_openai)" ;;
  *)      DJ_TEXT="$(run_ollama)" ;;
esac

echo "$DJ_TEXT"