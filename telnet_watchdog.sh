#!/usr/bin/env bash
#
# DEPRECATED: AI Radio Telnet Watchdog - No longer needed with Harbor HTTP
# Telnet has been replaced with Harbor HTTP for reliability
# This script is kept for backward compatibility but should not be used
#

LOGFILE="/opt/ai-radio/logs/telnet_watchdog.log"
HEALTH_URL="http://localhost:5055/api/health"
CHECK_INTERVAL=60  # Check every 60 seconds
MAX_FAILURES=3    # Restart after 3 consecutive failures

mkdir -p "$(dirname "$LOGFILE")"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOGFILE"
}

check_telnet_health() {
    local response
    response=$(curl -s -m 10 "$HEALTH_URL" 2>/dev/null)
    
    if [[ $? -eq 0 ]] && echo "$response" | grep -q '"telnet_connection":"ok"'; then
        return 0  # Healthy
    else
        return 1  # Unhealthy
    fi
}

fix_telnet_connection() {
    log "WARNING: Attempting gentle telnet recovery (no stream restart)"
    
    # First, try just waiting - sometimes telnet server recovers on its own
    log "Step 1: Waiting 30 seconds for self-recovery..."
    sleep 30
    
    if check_telnet_health; then
        log "SUCCESS: Telnet recovered automatically"
        return 0
    fi
    
    # Try sending a simple command to "wake up" the telnet server
    log "Step 2: Attempting to wake up telnet server..."
    echo -e "uptime\nquit" | timeout 5 nc 127.0.0.1 1234 >/dev/null 2>&1
    sleep 5
    
    if check_telnet_health; then
        log "SUCCESS: Telnet server woke up"
        return 0
    fi
    
    # Last resort: restart only if absolutely necessary
    log "Step 3: All gentle methods failed - considering service restart"
    log "CRITICAL: Would normally restart ai-radio service, but this interrupts stream"
    log "MANUAL INTERVENTION NEEDED: Telnet server unresponsive"
    
    # Send alert but don't restart automatically
    return 1
}

main() {
    log "DEPRECATED: Telnet watchdog is deprecated - Harbor HTTP has replaced telnet"
    log "STARTUP: AI Radio telnet watchdog started (PID: $$) - DEPRECATED"
    
    local failure_count=0
    
    while true; do
        if check_telnet_health; then
            if [[ $failure_count -gt 0 ]]; then
                log "RECOVERY: Telnet connectivity restored after $failure_count failures"
            fi
            failure_count=0
        else
            failure_count=$((failure_count + 1))
            log "WARNING: Telnet connectivity failed (attempt $failure_count/$MAX_FAILURES)"
            
            if [[ $failure_count -ge $MAX_FAILURES ]]; then
                if fix_telnet_connection; then
                    failure_count=0
                else
                    log "ALERT: Telnet still unresponsive - stream continues but metadata may be stale"
                    # Don't reset failure_count - keep trying gentle recovery
                fi
            fi
        fi
        
        sleep "$CHECK_INTERVAL"
    done
}

# Handle signals gracefully
trap 'log "SHUTDOWN: Telnet watchdog stopping"; exit 0' SIGTERM SIGINT

main "$@"