#!/bin/bash
# Emergency sine wave fallback fixer
# Run this script when you hear sine waves

set -euo pipefail

LOGFILE="/var/log/sine_recovery.log"
TELNET_HOST="127.0.0.1"
TELNET_PORT=1234

# Function to log with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOGFILE"
}

log "=== EMERGENCY SINE RECOVERY STARTED ==="

# Try multiple recovery methods
log "Step 1: Skipping current music track..."
if timeout 5 bash -c "echo -e 'library_clean_m3u.skip\nquit' | nc $TELNET_HOST $TELNET_PORT" &>/dev/null; then
    log "SUCCESS: Music skip command sent"
else
    log "ERROR: Failed to send music skip"
fi

sleep 3

# Check if it worked
log "Step 2: Checking if recovery worked..."
temp_file="/tmp/audio_check.$$"
if timeout 3 curl -s http://127.0.0.1:8000/stream.mp3 2>/dev/null | head -c 1000 > "$temp_file" 2>/dev/null; then
    unique_bytes=$(od -tu1 "$temp_file" | awk '{for(i=2;i<=NF;i++) print $i}' | sort -u | wc -l)
    rm -f "$temp_file"
    
    if [[ $unique_bytes -lt 50 ]]; then
        log "WARNING: Still detecting sine wave (unique bytes: $unique_bytes)"
        
        # Try harder recovery
        log "Step 3: Attempting harder recovery - flushing TTS and skipping again..."
        timeout 5 bash -c "echo -e 'tts.flush_and_skip\nquit' | nc $TELNET_HOST $TELNET_PORT" &>/dev/null || true
        sleep 2
        timeout 5 bash -c "echo -e 'library_clean_m3u.skip\nquit' | nc $TELNET_HOST $TELNET_PORT" &>/dev/null || true
        sleep 2
        
        # Final check
        if timeout 3 curl -s http://127.0.0.1:8000/stream.mp3 2>/dev/null | head -c 1000 > "$temp_file" 2>/dev/null; then
            final_unique=$(od -tu1 "$temp_file" | awk '{for(i=2;i<=NF;i++) print $i}' | sort -u | wc -l)
            rm -f "$temp_file"
            
            if [[ $final_unique -lt 50 ]]; then
                log "CRITICAL: Recovery failed - may need service restart"
                exit 1
            else
                log "SUCCESS: Recovery worked on second attempt (unique bytes: $final_unique)"
            fi
        fi
    else
        log "SUCCESS: Recovery successful (unique bytes: $unique_bytes)"
    fi
else
    log "ERROR: Cannot sample audio stream"
    rm -f "$temp_file" 2>/dev/null
fi

log "=== EMERGENCY SINE RECOVERY COMPLETED ==="