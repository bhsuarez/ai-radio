#!/usr/bin/env python3
"""
Check for pending intros when tracks change

This script is called by the Flask UI when a track changes
to check if there's a pending intro for the current track.
"""

import sys
import os
import json
import time
from efficient_dj_intro import check_and_play_pending_intro

def main():
    if len(sys.argv) != 3:
        print("Usage: check_pending_intro.py <artist> <title>")
        sys.exit(1)
    
    artist = sys.argv[1]
    title = sys.argv[2]
    
    print(f"Checking for pending intro: '{title}' by {artist}")
    
    # Check and play pending intro if it exists
    if check_and_play_pending_intro(artist, title):
        print(f"Played pending intro for '{title}' by {artist}")
    else:
        print(f"No pending intro found for '{title}' by {artist}")

if __name__ == "__main__":
    main()