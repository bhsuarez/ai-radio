#!/bin/bash
# TTS Queue Monitor - Prevents queue blocking
# Add to cron: */5 * * * * /opt/ai-radio/monitor_tts_queue.sh

set -euo pipefail

LOGFILE="/var/log/tts_queue_monitor.log"
MAX_QUEUE_SIZE=3
TELNET_HOST="127.0.0.1"
TELNET_PORT=1234

# Function to log with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOGFILE"
}

# Get current queue size
get_queue_size() {
    local output
    output=$(timeout 5 bash -c "echo -e 'tts.queue\nquit' | nc $TELNET_HOST $TELNET_PORT" 2>/dev/null)
    
    # If output is empty or only contains "END"/"Bye!", queue is empty
    if [[ -z "$output" ]] || echo "$output" | grep -qE "^(END|Bye!)$"; then
        echo "0"
        return
    fi
    
    # Count the number of words in lines that aren't control messages
    # Exclude "END", "Bye!", and empty lines
    local size
    size=$(echo "$output" | grep -v -E '^(END|Bye!|$)' | wc -w)
    
    echo "$size"
}

# Check if sine wave is playing (indicating fallback)
check_sine_fallback() {
    # Sample the audio stream and check for repetitive patterns
    local temp_file="/tmp/audio_sample.$$"
    
    if timeout 3 curl -s http://127.0.0.1:8000/stream.mp3 2>/dev/null | head -c 1000 > "$temp_file" 2>/dev/null; then
        local unique_bytes
        unique_bytes=$(od -tu1 "$temp_file" | awk '{for(i=2;i<=NF;i++) print $i}' | sort -u | wc -l)
        rm -f "$temp_file"
        
        # Sine waves typically have very few unique byte patterns (usually < 20)
        # Normal music has much more variety (> 50)
        # Being more aggressive with detection threshold
        if [[ $unique_bytes -lt 50 ]]; then
            return 0  # Sine wave detected
        else
            return 1  # Normal audio
        fi
    else
        # If we can't sample the stream, assume it's OK
        rm -f "$temp_file" 2>/dev/null
        return 1
    fi
}

# Main monitoring logic
main() {
    queue_size=$(get_queue_size)
    
    # Check if we got a valid number
    if ! [[ "$queue_size" =~ ^[0-9]+$ ]]; then
        log "WARNING: Could not get queue size, liquidsoap may be down"
        exit 1
    fi
    
    log "Queue size: $queue_size"
    
    # Check for sine wave fallback even with normal queue size
    if check_sine_fallback; then
        log "ALERT: Sine wave fallback detected, forcing music skip..."
        if timeout 5 bash -c "echo -e 'library_clean_m3u.skip\nquit' | nc $TELNET_HOST $TELNET_PORT" &>/dev/null; then
            log "SUCCESS: Forced music skip to recover from sine fallback"
            sleep 2  # Give it time to switch
        else
            log "ERROR: Failed to send skip command for sine recovery"
        fi
    fi
    
    if [[ $queue_size -gt $MAX_QUEUE_SIZE ]]; then
        log "ALERT: Queue size ($queue_size) exceeds threshold ($MAX_QUEUE_SIZE), flushing..."
        
        # Try to flush the entire queue multiple times if necessary
        local attempts=0
        local max_attempts=3
        
        while [[ $attempts -lt $max_attempts ]]; do
            attempts=$((attempts + 1))
            log "Flush attempt $attempts/$max_attempts..."
            
            # Flush and skip current item
            if timeout 5 bash -c "echo -e 'tts.flush_and_skip\nquit' | nc $TELNET_HOST $TELNET_PORT" &>/dev/null; then
                # Wait a moment and check queue size again
                sleep 1
                local new_size=$(get_queue_size)
                
                if [[ $new_size -le 1 ]]; then
                    log "SUCCESS: Queue flushed, size now $new_size"
                    break
                else
                    log "WARNING: Queue still has $new_size items after flush attempt $attempts"
                fi
            else
                log "ERROR: Failed to send flush command on attempt $attempts"
            fi
            
            # If this was the last attempt and queue is still too big
            if [[ $attempts -eq $max_attempts ]]; then
                local final_size=$(get_queue_size)
                if [[ $final_size -gt $MAX_QUEUE_SIZE ]]; then
                    log "CRITICAL: Queue still clogged after $max_attempts attempts (size: $final_size)"
                    # Don't exit 1 - let the system keep trying on next run
                fi
            fi
        done
    fi
}

# Run the monitor
main "$@"