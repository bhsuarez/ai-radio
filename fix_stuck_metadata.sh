#!/bin/bash

# Fix stuck AI DJ metadata by detecting mismatches between Icecast and Liquidsoap
# This script detects when Icecast shows "AI DJ - DJ Intro" but Liquidsoap shows music

# Get Icecast title
ICECAST_TITLE=$(curl -s http://localhost:8000/status-json.xsl | jq -r '.icestats.source.title' 2>/dev/null)

# Check if Icecast shows AI DJ intro
if [[ "$ICECAST_TITLE" == *"AI DJ"* ]] && [[ "$ICECAST_TITLE" == *"DJ Intro"* ]]; then
    echo "$(date): Detected stuck AI DJ metadata in Icecast: $ICECAST_TITLE"
    
    # Get current Liquidsoap metadata (section 1 is current)
    LIQUIDSOAP_DATA=$(echo -e "output.icecast.metadata\nquit" | nc localhost 1234 2>/dev/null)
    
    # Extract the first section (current track) from Liquidsoap
    CURRENT_SECTION=$(echo "$LIQUIDSOAP_DATA" | awk '/^--- 1 ---/{flag=1; next} /^--- [0-9]+ ---/{flag=0} flag && /^(artist|title)=/{print}' | head -2)
    
    if echo "$CURRENT_SECTION" | grep -q "artist=.*AI DJ"; then
        echo "$(date): Liquidsoap also shows AI DJ, this is correct"
    else
        echo "$(date): Liquidsoap shows music, but Icecast stuck on AI DJ - fixing"
        
        # Force skip to refresh metadata
        echo -e "output.icecast.skip\nquit" | nc localhost 1234 >/dev/null 2>&1
        
        sleep 2
        
        # Verify fix
        NEW_ICECAST_TITLE=$(curl -s http://localhost:8000/status-json.xsl | jq -r '.icestats.source.title' 2>/dev/null)
        echo "$(date): Fixed - now showing: $NEW_ICECAST_TITLE"
    fi
fi