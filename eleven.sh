#!/bin/bash

echo “Setting up ElevenLabs TTS for AI Radio”
echo “======================================”

# API key

ELEVENLABS_API_KEY=“sk_d01bef4514af5eb37db55763d18325386f0f24afbf7f3cb9”

echo “Testing API key…”

# Get available voices

VOICES_RESPONSE=$(curl -s -H “xi-api-key: $ELEVENLABS_API_KEY” “https://api.elevenlabs.io/v1/voices”)

# Check if response contains error

if echo “$VOICES_RESPONSE” | grep -q “error”; then
echo “API key test failed:”
echo “$VOICES_RESPONSE”
exit 1
fi

if echo “$VOICES_RESPONSE” | grep -q “unauthorized”; then
echo “API key unauthorized”
exit 1
fi

echo “API key is valid!”
echo “”

# Parse voices with simpler approach

echo “Getting voice list…”
echo “$VOICES_RESPONSE” > /tmp/voices.json

# Extract voices using a separate Python script

cat > /tmp/parse_voices.py << ‘PYEND’
import json
import sys

try:
with open(’/tmp/voices.json’, ‘r’) as f:
data = json.load(f)

```
voices = data.get('voices', [])

print("RECOMMENDED DJ VOICES:")
recommended = ["Rachel", "Adam", "Antoni", "Bella", "Josh"]

for voice in voices:
    if voice['name'] in recommended:
        print(f"  {voice['name']:10} - {voice['voice_id']}")

print("")
print("ALL AVAILABLE VOICES:")
for i, voice in enumerate(voices[:10], 1):
    print(f"{i:2d}. {voice['name']:15} - {voice['voice_id']}")
```

except Exception as e:
print(f”Error parsing voices: {e}”)
sys.exit(1)
PYEND

python3 /tmp/parse_voices.py

echo “”
echo “Recommended voices:”
echo “  Adam (pNInz6obpgDQGcFmaJgB) - Male DJ voice”
echo “  Rachel (21m00Tcm4TlvDq8ikWAM) - Female DJ voice”
echo “”
echo “Press Enter for Adam, or type a voice ID:”
read -p “Voice ID: “ VOICE_ID

# Default to Adam

if [ -z “$VOICE_ID” ]; then
VOICE_ID=“pNInz6obpgDQGcFmaJgB”
echo “Using Adam voice”
fi

echo “”
echo “Testing voice: $VOICE_ID”

# Test voice

curl -s -o /tmp/test_voice.mp3   
-H “xi-api-key: $ELEVENLABS_API_KEY”   
-H “Content-Type: application/json”   
-d ‘{“text”:“Hey everyone! This is your AI DJ bringing you the best music.”,“model_id”:“eleven_monolingual_v1”,“voice_settings”:{“stability”:0.6,“similarity_boost”:0.8}}’   
“https://api.elevenlabs.io/v1/text-to-speech/$VOICE_ID”

if [ -f /tmp/test_voice.mp3 ] && [ -s /tmp/test_voice.mp3 ]; then
echo “Voice test successful! Sample saved to /tmp/test_voice.mp3”
echo “Play with: mpv /tmp/test_voice.mp3”
echo “”
read -p “Continue? (y/n): “ CONFIRM
if [ “$CONFIRM” != “y” ]; then
echo “Cancelled”
exit 0
fi
else
echo “Voice test failed”
exit 1
fi

echo “”
echo “Updating AI Radio configuration…”

# Backup

cp /opt/ai-radio/app.py /opt/ai-radio/app.py.backup.$(date +%Y%m%d_%H%M%S)

# Update config with sed

sed -i ‘s/TTS_PROVIDER = “.*”/TTS_PROVIDER = “elevenlabs”/’ /opt/ai-radio/app.py
sed -i “s/ELEVENLABS_API_KEY = ".*"/ELEVENLABS_API_KEY = "$ELEVENLABS_API_KEY"/” /opt/ai-radio/app.py
sed -i “s/ELEVENLABS_VOICE_ID = ".*"/ELEVENLABS_VOICE_ID = "$VOICE_ID"/” /opt/ai-radio/app.py

echo “Configuration updated”

echo “”
echo “Restarting AI Radio service…”
systemctl restart ai-radio

sleep 5

if systemctl is-active –quiet ai-radio; then
echo “Service restarted successfully!”
else
echo “Service restart failed. Check logs with:”
echo “journalctl -u ai-radio -f”
exit 1
fi

echo “”
echo “Testing integration…”
sleep 3

TEST_RESULT=$(curl -s -X POST “http://localhost:5055/api/dj-now”)

if echo “$TEST_RESULT” | grep -q “ok”; then
echo “SUCCESS! ElevenLabs is now active”
else
echo “Test result: $TEST_RESULT”
fi

echo “”
echo “SETUP COMPLETE!”
echo “Provider: ElevenLabs”
echo “Voice ID: $VOICE_ID”
echo “”
echo “Monitor logs: journalctl -u ai-radio -f”
echo “Check usage: https://elevenlabs.io/usage”

# Cleanup

rm -f /tmp/voices.json /tmp/parse_voices.py /tmp/test_voice.mp3
