#!/usr/bin/env bash
set -euo pipefail

# Ensure Ollama uses the right model directory
export OLLAMA_MODELS="/mnt/music/ai-dj/ollama"

TITLE="${1:-}"
ARTIST="${2:-}"

# Default to a smaller, RAM-friendly model
MODEL="${MODEL:-llama3.2:3b}"
PROVIDER="${PROVIDER:-ollama}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"

# Check if we're in intro mode
INTRO_MODE="${DJ_INTRO_MODE:-0}"
CUSTOM_PROMPT="${DJ_CUSTOM_PROMPT:-}"

if [[ "$INTRO_MODE" == "1" ]]; then
    # INTRO MODE - Generate introduction before song plays
    STYLES=("energetic" "upbeat" "smooth" "excited" "warm" "friendly")
    STYLE="${STYLE:-$(printf '%s\n' "${STYLES[@]}" | shuf -n1)}"
    
    if [[ -n "$CUSTOM_PROMPT" ]]; then
        PROMPT="$CUSTOM_PROMPT"
    else
        # Disable trivia to prevent made-up facts
        TRIVIA_LINE=""
        
        PROMPT="You are a ${STYLE} radio DJ introducing the next song. 
In 1-2 sentences (under 25 words), introduce '${TITLE:-this track}' by ${ARTIST:-an unknown artist}.
CRITICAL: Use the artist name EXACTLY as written: '${ARTIST:-an unknown artist}'. Copy it character-for-character. Do not change spelling, punctuation, or wording.
Do NOT describe the song's genre, style, instruments, or musical elements unless you are 100% certain.
Use phrases like 'Coming up next', 'Here's', 'Time for', 'Let's hear', etc.
Keep it brief, energetic, and natural. No emojis or hashtags. NEVER invent details about the music.
Never mention AI, computers, databases, archives, or digital systems. Speak as a human DJ would."
    fi
else
    # NORMAL MODE - Generate commentary after song plays
    STYLES=("energetic hype" "laid-back chill" "warm late-night" "quirky college-radio" "BBC-style concise" "retro 90s alt" "clubby electronic")
    STYLE="${STYLE:-$(printf '%s\n' "${STYLES[@]}" | shuf -n1)}"

    # Disable trivia to prevent made-up facts
    TRIVIA_LINE=""

    PROMPT="You are a radio DJ. Style: ${STYLE}.
In 1–2 sentences, speak about the song '${TITLE:-this track}' by ${ARTIST:-an unknown artist} that just played.
CRITICAL: Use the artist name EXACTLY as written: '${ARTIST:-an unknown artist}'. Copy it character-for-character. Do not change spelling, punctuation, or wording.
Do NOT describe the song's genre, style, instruments, or musical elements unless you are 100% certain. 
Keep it simple and conversational. No emojis or hashtags. NEVER invent details about the music.
Never mention AI, computers, databases, archives, digital systems, or phrases like 'as far as I know'. Speak as a human DJ would."
fi

collapse_line() {
  # Remove ANSI escape sequences and clean whitespace
  sed 's/\x1b\[[0-9;]*[mKhlABCDEFGHJK]//g' | \
  tr -d '\r' | \
  sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | \
  tr '\n' ' ' | \
  sed 's/[[:space:]]\+/ /g; s/^[[:space:]]*//; s/[[:space:]]*$//'
}

run_ollama() {
  # Use a temporary file to capture output cleanly
  local temp_file=$(mktemp)
  
  # Run ollama and capture to file, then clean it up
  if timeout 30s ollama run "$MODEL" "$PROMPT" > "$temp_file" 2>/dev/null; then
    cat "$temp_file" | collapse_line
  else
    echo "Error: Ollama generation failed or timed out" >&2
    echo ""
  fi
  
  rm -f "$temp_file"
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
  "max_tokens": 80,
  "messages": [
    {"role":"system","content":"You are a concise, engaging human radio DJ. No emojis or hashtags. Never invent facts. Never mention AI, computers, databases, archives, or digital systems. Speak naturally as a human DJ would."},
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

case "$PROVIDER" in
  openai) run_openai ;;
  *)      run_ollama ;;
esac