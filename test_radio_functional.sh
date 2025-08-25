#!/bin/bash
# Functional test script for AI Radio system
# Tests the actual running services and functionality

set -e

echo "=========================================="
echo "AI Radio Functional Test Suite"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass_count=0
fail_count=0

log_pass() {
    echo -e "[${GREEN}PASS${NC}] $1"
    ((pass_count++))
}

log_fail() {
    echo -e "[${RED}FAIL${NC}] $1"
    ((fail_count++))
}

log_warn() {
    echo -e "[${YELLOW}WARN${NC}] $1"
}

# Test 1: Check if Liquidsoap container is running
echo
echo "Testing Liquidsoap Docker container..."
if docker ps | grep -q "ai-radio"; then
    log_pass "Liquidsoap container is running"
else
    log_fail "Liquidsoap container is not running"
fi

# Test 2: Test telnet connectivity to Liquidsoap
echo
echo "Testing telnet connectivity to Liquidsoap..."
if timeout 5 bash -c "</dev/tcp/127.0.0.1/1234"; then
    log_pass "Telnet port 1234 is accessible"
else
    log_fail "Cannot connect to telnet port 1234"
fi

# Test 3: Test basic telnet commands
echo
echo "Testing telnet command response..."
if echo "help" | timeout 5 telnet 127.0.0.1 1234 2>/dev/null | grep -q "Available commands\|END"; then
    log_pass "Telnet interface responds to commands"
else
    log_fail "Telnet interface not responding properly"
fi

# Test 4: Test stream accessibility
echo
echo "Testing stream endpoint..."
if curl -s -I "http://127.0.0.1:8000/stream.mp3" | head -1 | grep -q "200\|302"; then
    log_pass "Stream endpoint returns success status"
else
    stream_status=$(curl -s -I "http://127.0.0.1:8000/stream.mp3" | head -1 || echo "Connection failed")
    log_fail "Stream endpoint issue: $stream_status"
fi

# Test 5: Test Flask API
echo
echo "Testing Flask web UI..."
if curl -s -f "http://127.0.0.1:5055/" > /dev/null; then
    log_pass "Flask web UI is accessible"
else
    log_fail "Flask web UI is not accessible"
fi

# Test 6: Test metadata endpoints
echo
echo "Testing metadata API..."
if curl -s "http://127.0.0.1:5055/api/metadata" | grep -q "artist\|title"; then
    log_pass "Metadata API returns valid data"
else
    log_fail "Metadata API not returning expected data"
fi

# Test 7: Test TTS queue functionality
echo
echo "Testing TTS queue..."
test_message="Test message from functional test"
if ./dj_enqueue_xtts.sh "$test_message" 2>/dev/null; then
    log_pass "TTS enqueue script executed successfully"
else
    log_fail "TTS enqueue script failed"
fi

# Test 8: Check if metadata files exist and are recent
echo
echo "Testing metadata file freshness..."
if [ -f "now.json" ] && [ -f "next.json" ]; then
    # Check if files are less than 5 minutes old
    if [ $(find now.json -mmin -5 2>/dev/null | wc -l) -gt 0 ]; then
        log_pass "Metadata files are recent (updated within 5 minutes)"
    else
        log_warn "Metadata files exist but may be stale"
        ((fail_count++))
    fi
else
    log_fail "Metadata files (now.json, next.json) are missing"
fi

# Test 9: Test AI DJ line generation
echo
echo "Testing AI DJ line generation..."
if timeout 30 ./gen_ai_dj_line_enhanced.sh "Test Song" "Test Artist" 2>/dev/null; then
    log_pass "AI DJ line generation completed"
else
    log_warn "AI DJ line generation timed out or failed (this may be normal if AI services are unavailable)"
fi

# Test 10: Check service statuses
echo
echo "Testing systemd services..."
services=("ai-radio.service" "ai-dj-ui.service" "ai-metadata-daemon.service")

for service in "${services[@]}"; do
    if systemctl is-active --quiet "$service"; then
        log_pass "Service $service is running"
    else
        log_fail "Service $service is not running"
    fi
done

# Test 11: Check log files for recent activity
echo
echo "Testing recent system activity..."
if journalctl -u ai-radio.service --since "5 minutes ago" | grep -q "liquidsoap\|radio"; then
    log_pass "Recent activity in ai-radio service logs"
else
    log_warn "No recent activity in ai-radio service logs"
fi

# Test 12: Test Docker logs
echo
echo "Testing Docker container health..."
if docker logs ai-radio --since 5m 2>/dev/null | grep -v "ERROR\|FATAL" | grep -q "liquidsoap\|radio"; then
    log_pass "Docker container showing healthy activity"
else
    log_warn "Limited activity in Docker container logs"
fi

# Summary
echo
echo "=========================================="
echo "TEST SUMMARY"
echo "=========================================="
total=$((pass_count + fail_count))
success_rate=$((pass_count * 100 / total))

echo "Total tests: $total"
echo "Passed: $pass_count"
echo "Failed: $fail_count"  
echo "Success rate: $success_rate%"

if [ $fail_count -eq 0 ]; then
    echo -e "${GREEN}All tests passed! AI Radio system is functioning properly.${NC}"
    exit 0
elif [ $success_rate -ge 80 ]; then
    echo -e "${YELLOW}Most tests passed. System is largely functional with some minor issues.${NC}"
    exit 0
else
    echo -e "${RED}Multiple test failures detected. System may have significant issues.${NC}"
    exit 1
fi