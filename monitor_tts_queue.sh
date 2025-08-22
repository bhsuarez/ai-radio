#!/bin/bash
# TTS Queue Monitor - Prevents queue blocking
# Add to cron: */5 * * * * /opt/ai-radio/monitor_tts_queue.sh

set -euo pipefail

LOGFILE="/var/log/tts_queue_monitor.log"
MAX_QUEUE_SIZE=5
TELNET_HOST="127.0.0.1"
TELNET_PORT=1234

# Function to log with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOGFILE"
}

# Get current queue size
get_queue_size() {
    local output
    output=$(timeout 5 bash -c "echo 'tts.queue' | nc $TELNET_HOST $TELNET_PORT" 2>/dev/null)
    # Extract just the number, ignoring "END" line
    echo "$output" | head -1 | grep -o '^[0-9]\+' || echo "0"
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
    
    if [[ $queue_size -gt $MAX_QUEUE_SIZE ]]; then
        log "ALERT: Queue size ($queue_size) exceeds threshold ($MAX_QUEUE_SIZE), flushing..."
        
        # Flush the queue
        if timeout 5 bash -c "echo 'tts.flush_and_skip' | nc $TELNET_HOST $TELNET_PORT" &>/dev/null; then
            log "SUCCESS: Queue flushed"
        else
            log "ERROR: Failed to flush queue"
            exit 1
        fi
    fi
}

# Run the monitor
main "$@"