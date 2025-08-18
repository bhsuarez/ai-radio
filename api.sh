#!/bin/bash

# Comprehensive API testing script for ai-radio

# This will test all endpoints safely

echo “=== AI Radio API Testing ===”
echo “Server: http://localhost:5055”
echo “Date: $(date)”
echo “”

# Function to test an endpoint

test_endpoint() {
local method=$1
local endpoint=$2
local description=$3

```
echo "Testing: $method $endpoint ($description)"
echo "----------------------------------------"

# Use timeout to prevent hanging
timeout 10s curl -s -X $method \
    -H "Content-Type: application/json" \
    -w "\nHTTP Status: %{http_code}\nTime: %{time_total}s\n" \
    http://localhost:5055$endpoint 2>/dev/null

if [ $? -eq 124 ]; then
    echo "TIMEOUT after 10 seconds"
fi
echo ""
echo "=========================================="
echo ""
```

}

# Test basic endpoints first (should be safe)

echo “🔍 Testing safe endpoints first…”

test_endpoint “GET” “/” “Root endpoint”
test_endpoint “GET” “/health” “Health check”
test_endpoint “GET” “/api/status” “API status”
test_endpoint “GET” “/api/current-track” “Current track info”

# Test the problematic endpoint with timeout

echo “⚠️  Testing the problematic DJ endpoint…”
echo “This is the one that was causing issues:”

timeout 60s curl -X POST   
-H “Content-Type: application/json”   
-w “\nHTTP Status: %{http_code}\nTime: %{time_total}s\n”   
“http://localhost:5055/api/dj-now” 2>/dev/null &

curl_pid=$!
echo “Started POST /api/dj-now with PID: $curl_pid”
echo “Waiting up to 60 seconds…”

# Monitor the request

for i in {1..60}; do
if ! kill -0 $curl_pid 2>/dev/null; then
echo “✅ Request completed in ${i} seconds”
wait $curl_pid
break
fi

```
if [ $((i % 10)) -eq 0 ]; then
    echo "⏳ Still waiting... ${i}s elapsed"
fi

sleep 1
```

done

# If still running after 60s, kill it

if kill -0 $curl_pid 2>/dev/null; then
echo “❌ Request hung for 60+ seconds, killing it”
kill $curl_pid 2>/dev/null
fi

echo “”
echo “=== Test Complete ===”
echo “”
echo “💡 Next steps:”
echo “1. If /api/dj-now hung, the issue is confirmed in Flask”
echo “2. If it worked, the issue might be intermittent”
echo “3. Check the app logs: journalctl -u gunicorn -f”
