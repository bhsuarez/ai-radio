#!/bin/bash

# Fix stuck AI DJ metadata by detecting mismatches between Icecast and Liquidsoap
# This script detects when Icecast shows "AI DJ - DJ Intro" but Liquidsoap shows music

# Get Icecast title
ICECAST_TITLE=$(curl -s http://localhost:8000/status-json.xsl | jq -r '.icestats.source.title' 2>/dev/null)

# Check if Icecast shows AI DJ intro
if [[ "$ICECAST_TITLE" == *"AI DJ"* ]] && [[ "$ICECAST_TITLE" == *"DJ Intro"* ]]; then
    echo "$(date): Detected stuck AI DJ metadata in Icecast: $ICECAST_TITLE"
    
    # Harbor-based fix (more reliable than telnet)
    echo "$(date): Using Harbor HTTP to fix stuck metadata"
    
    # Create tiny silence track to force skip via Harbor
    TEMP_FILE=$(mktemp --suffix=.mp3)
    if command -v ffmpeg >/dev/null 2>&1; then
        ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 0.1 -acodec mp3 -y "$TEMP_FILE" >/dev/null 2>&1
        if [[ -f "$TEMP_FILE" ]]; then
            echo "$(date): Sending Harbor skip command..."
            if curl -f -X PUT http://127.0.0.1:8001/music -H "Content-Type: audio/mpeg" --data-binary "@$TEMP_FILE" >/dev/null 2>&1; then
                echo "$(date): Harbor skip successful"
                sleep 2
                
                # Verify fix
                NEW_ICECAST_TITLE=$(curl -s http://localhost:8000/status-json.xsl | jq -r '.icestats.source.title' 2>/dev/null)
                echo "$(date): Fixed - now showing: $NEW_ICECAST_TITLE"
            else
                echo "$(date): Harbor skip failed"
            fi
            rm -f "$TEMP_FILE"
        else
            echo "$(date): Failed to generate skip audio"
        fi
    else
        echo "$(date): ffmpeg not available for Harbor skip"
    fi
fi