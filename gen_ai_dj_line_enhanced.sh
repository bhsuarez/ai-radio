#!/usr/bin/env bash
set -euo pipefail

# Enhanced DJ line generation with multi-tier fallback system
# Supports Ollama, OpenAI, and authentic template fallbacks

TITLE="${1:-}"
ARTIST="${2:-}"

# Configuration
CONFIG_FILE="/opt/ai-radio/dj_settings.json"
LOG_FILE="/opt/ai-radio/logs/dj_fallback.log"
STATS_FILE="/opt/ai-radio/logs/dj_stats.json"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$STATS_FILE")"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Load configuration
load_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log "ERROR: Config file not found: $CONFIG_FILE"
        exit 1
    fi
    
    # Check if jq is available, fallback to basic parsing if not
    if command -v jq >/dev/null 2>&1; then
        CONFIG_DATA=$(cat "$CONFIG_FILE")
    else
        log "WARNING: jq not available, using basic config parsing"
        return 1
    fi
}

# Update stats
update_stats() {
    local provider="$1"
    local model="$2"
    local success="$3"
    local timestamp=$(date +%s)
    
    if command -v jq >/dev/null 2>&1; then
        # Initialize stats file if it doesn't exist
        if [[ ! -f "$STATS_FILE" ]]; then
            echo '{"usage_stats": {}, "last_updated": 0}' > "$STATS_FILE"
        fi
        
        # Update stats
        jq --arg provider "$provider" \
           --arg model "$model" \
           --arg success "$success" \
           --arg timestamp "$timestamp" \
           '.usage_stats[$provider + "_" + $model] += 1 | 
            .last_updated = ($timestamp | tonumber)' \
           "$STATS_FILE" > "${STATS_FILE}.tmp" && mv "${STATS_FILE}.tmp" "$STATS_FILE"
    fi
}

# Text cleanup function
collapse_line() {
    sed 's/\x1b\[[0-9;]*[mKhlABCDEFGHJK]//g' | \
    tr -d '\r' | \
    sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | \
    tr '\n' ' ' | \
    sed 's/[[:space:]]\+/ /g; s/^[[:space:]]*//; s/[[:space:]]*$//'
}

# Quality validation
validate_quality() {
    local text="$1"
    local mode="$2"
    
    # Basic quality checks
    [[ ${#text} -gt 5 ]] || return 1  # Too short
    [[ ${#text} -lt 200 ]] || return 1  # Too long
    
    # Check for AI/technical mentions
    if echo "$text" | grep -qi -E "(ai|artificial|computer|database|digital|algorithm|model|generated)"; then
        log "QUALITY: Text contains AI/tech references: $text"
        return 1
    fi
    
    # Check for artist name accuracy (basic check)
    if [[ -n "$ARTIST" ]] && ! echo "$text" | grep -qi "$ARTIST"; then
        log "QUALITY: Artist name not found in text: $text"
        return 1
    fi
    
    return 0
}

# Enhanced Ollama runner with model fallback
run_ollama_tier() {
    local tier="$1"
    local models timeout max_retries
    
    if command -v jq >/dev/null 2>&1; then
        models=($(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.${tier}.models[]" 2>/dev/null || echo "llama3.2:3b"))
        timeout=$(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.${tier}.timeout // 30" 2>/dev/null)
        max_retries=$(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.${tier}.max_retries // 2" 2>/dev/null)
    else
        models=("llama3.2:3b" "llama3.2:1b")
        timeout=30
        max_retries=2
    fi
    
    export OLLAMA_MODELS="/mnt/music/ai-dj/ollama"
    
    for model in "${models[@]}"; do
        log "Trying Ollama model: $model (tier: $tier)"
        
        for ((retry=0; retry<=max_retries; retry++)); do
            if [[ $retry -gt 0 ]]; then
                local delay=$((retry * 2))
                log "Retry $retry for $model after ${delay}s delay"
                sleep $delay
            fi
            
            local temp_file=$(mktemp)
            
            if timeout "${timeout}s" ollama run "$model" "$PROMPT" > "$temp_file" 2>/dev/null; then
                local result=$(cat "$temp_file" | collapse_line)
                rm -f "$temp_file"
                
                if validate_quality "$result" "$INTRO_MODE"; then
                    log "SUCCESS: Ollama $model generated valid text"
                    update_stats "ollama" "$model" "success"
                    echo "$result"
                    return 0
                else
                    log "QUALITY: Ollama $model failed validation"
                    update_stats "ollama" "$model" "quality_fail"
                fi
            else
                log "ERROR: Ollama $model failed or timed out"
                update_stats "ollama" "$model" "error"
            fi
            
            rm -f "$temp_file"
        done
    done
    
    return 1
}

# Enhanced OpenAI runner with model fallback
run_openai_tier() {
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        log "WARNING: OPENAI_API_KEY not set, skipping OpenAI tier"
        return 1
    fi
    
    local models timeout max_retries rate_delay
    
    if command -v jq >/dev/null 2>&1; then
        models=($(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.tier1_openai.models[]" 2>/dev/null || echo "gpt-4o-mini"))
        timeout=$(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.tier1_openai.timeout // 15" 2>/dev/null)
        max_retries=$(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.tier1_openai.max_retries // 2" 2>/dev/null)
        rate_delay=$(echo "$CONFIG_DATA" | jq -r ".ai_fallback_config.tier1_openai.rate_limit_delay // 1" 2>/dev/null)
    else
        models=("gpt-4o-mini" "gpt-3.5-turbo")
        timeout=15
        max_retries=2
        rate_delay=1
    fi
    
    for model in "${models[@]}"; do
        log "Trying OpenAI model: $model"
        
        for ((retry=0; retry<=max_retries; retry++)); do
            if [[ $retry -gt 0 ]]; then
                local delay=$((retry * 3 + rate_delay))
                log "Retry $retry for $model after ${delay}s delay"
                sleep $delay
            fi
            
            local resp
            resp=$(timeout "${timeout}s" curl -sS https://api.openai.com/v1/chat/completions \
                -H "Authorization: Bearer ${OPENAI_API_KEY}" \
                -H "Content-Type: application/json" \
                -d @- <<EOF 2>/dev/null || echo ""
{
  "model": "${model}",
  "temperature": 0.8,
  "max_tokens": 80,
  "messages": [
    {"role":"system","content":"You are a concise, engaging human radio DJ. No emojis or hashtags. Never invent facts. Never mention AI, computers, databases, archives, or digital systems. Speak naturally as a human DJ would."},
    {"role":"user","content": $(printf '%q' "$PROMPT")}
  ]
}
EOF
            )
            
            if [[ -n "$resp" ]] && echo "$resp" | grep -q "choices"; then
                local result
                if command -v python3 >/dev/null 2>&1; then
                    result=$(python3 -c "
import sys, json
try:
    data = json.loads('''$resp''')
    print(data['choices'][0]['message']['content'])
except:
    sys.exit(1)
" 2>/dev/null | collapse_line)
                else
                    # Fallback parsing without python
                    result=$(echo "$resp" | grep -o '"content":"[^"]*"' | sed 's/"content":"//; s/"$//' | collapse_line)
                fi
                
                if [[ -n "$result" ]] && validate_quality "$result" "$INTRO_MODE"; then
                    log "SUCCESS: OpenAI $model generated valid text"
                    update_stats "openai" "$model" "success"
                    echo "$result"
                    return 0
                else
                    log "QUALITY: OpenAI $model failed validation"
                    update_stats "openai" "$model" "quality_fail"
                fi
            else
                log "ERROR: OpenAI $model API call failed"
                update_stats "openai" "$model" "error"
            fi
            
            sleep "$rate_delay"
        done
    done
    
    return 1
}

# Authentic template fallback
run_template_fallback() {
    log "Using authentic template fallback"
    
    local templates
    local mode_key
    
    if [[ "$INTRO_MODE" == "1" ]]; then
        mode_key="intro"
    else
        mode_key="outro"
    fi
    
    if command -v jq >/dev/null 2>&1; then
        readarray -t templates < <(echo "$CONFIG_DATA" | jq -r ".authentic_dj_templates.${mode_key}[]" 2>/dev/null)
    fi
    
    # Fallback templates if config fails
    if [[ ${#templates[@]} -eq 0 ]]; then
        if [[ "$INTRO_MODE" == "1" ]]; then
            templates=(
                "Coming up next, we've got {title} by {artist}"
                "Here's {title} from {artist}"
                "Time for some {artist} with {title}"
                "Let's hear {title} by {artist}"
            )
        else
            templates=(
                "That was {title} by {artist}"
                "You just heard {artist} with {title}"
                "{artist} there with {title}"
                "That's {title} from {artist}"
            )
        fi
    fi
    
    # Select random template
    local template="${templates[$((RANDOM % ${#templates[@]}))]}"
    
    # Replace placeholders
    local result="$template"
    result="${result//\{title\}/${TITLE:-this track}}"
    result="${result//\{artist\}/${ARTIST:-an unknown artist}}"
    
    log "SUCCESS: Template fallback generated text"
    update_stats "template" "fallback" "success"
    echo "$result"
}

# Main execution flow
main() {
    log "Starting DJ line generation for: '$TITLE' by '$ARTIST'"
    
    # Load configuration
    if ! load_config; then
        log "WARNING: Using fallback configuration"
    fi
    
    # Check if we're in intro mode
    INTRO_MODE="${DJ_INTRO_MODE:-0}"
    CUSTOM_PROMPT="${DJ_CUSTOM_PROMPT:-}"
    
    # Generate prompt based on mode
    if [[ "$INTRO_MODE" == "1" ]]; then
        if [[ -n "$CUSTOM_PROMPT" ]]; then
            PROMPT="$CUSTOM_PROMPT"
        else
            # Get intro prompt from config
            local active_intro_prompt="Default Energetic"
            local prompt_template=""
            
            if command -v jq >/dev/null 2>&1; then
                active_intro_prompt=$(echo "$CONFIG_DATA" | jq -r ".ai_prompts.active_intro_prompt // \"Default Energetic\"" 2>/dev/null)
                prompt_template=$(echo "$CONFIG_DATA" | jq -r ".ai_prompts.intro_prompts[] | select(.name == \"$active_intro_prompt\") | .prompt" 2>/dev/null)
            fi
            
            if [[ -n "$prompt_template" ]]; then
                # Replace placeholders in template
                PROMPT="${prompt_template//\{title\}/${TITLE:-this track}}"
                PROMPT="${PROMPT//\{artist\}/${ARTIST:-an unknown artist}}"
            else
                # Fallback to default prompt
                PROMPT="You are an energetic radio DJ introducing the next song. 
In 1-2 sentences (under 25 words), introduce '${TITLE:-this track}' by ${ARTIST:-an unknown artist}.
CRITICAL: Use the artist name EXACTLY as written: '${ARTIST:-an unknown artist}'. Copy it character-for-character.
Do NOT describe the song's genre, style, instruments, or musical elements unless you are 100% certain.
Use phrases like 'Coming up next', 'Here's', 'Time for', 'Let's hear', etc.
Keep it brief, energetic, and natural. No emojis or hashtags. NEVER invent details about the music.
Never mention AI, computers, databases, archives, or digital systems. Speak as a human DJ would."
            fi
        fi
    else
        # Get outro prompt from config
        local active_outro_prompt="Default Conversational"
        local prompt_template=""
        
        if command -v jq >/dev/null 2>&1; then
            active_outro_prompt=$(echo "$CONFIG_DATA" | jq -r ".ai_prompts.active_outro_prompt // \"Default Conversational\"" 2>/dev/null)
            prompt_template=$(echo "$CONFIG_DATA" | jq -r ".ai_prompts.outro_prompts[] | select(.name == \"$active_outro_prompt\") | .prompt" 2>/dev/null)
        fi
        
        if [[ -n "$prompt_template" ]]; then
            # Replace placeholders in template
            PROMPT="${prompt_template//\{title\}/${TITLE:-this track}}"
            PROMPT="${PROMPT//\{artist\}/${ARTIST:-an unknown artist}}"
        else
            # Fallback to default prompt
            PROMPT="You are a radio DJ. 
In 1–2 sentences, speak about the song '${TITLE:-this track}' by ${ARTIST:-an unknown artist} that just played.
CRITICAL: Use the artist name EXACTLY as written: '${ARTIST:-an unknown artist}'. Copy it character-for-character.
Do NOT describe the song's genre, style, instruments, or musical elements unless you are 100% certain. 
Keep it simple and conversational. No emojis or hashtags. NEVER invent details about the music.
Never mention AI, computers, databases, archives, digital systems, or phrases like 'as far as I know'. Speak as a human DJ would."
        fi
    fi
    
    # Tier 1: OpenAI/ChatGPT (Primary)
    if run_openai_tier; then
        return 0
    fi
    
    # Tier 2: Primary Ollama models
    if run_ollama_tier "tier2_ollama"; then
        return 0
    fi
    
    # Tier 3: Alternative Ollama models
    if run_ollama_tier "tier3_ollama_alt"; then
        return 0
    fi
    
    # Tier 4: Authentic templates
    run_template_fallback
}

# Execute main function
main "$@"