#!/usr/bin/env python3
"""
Track just-played songs by monitoring /api/next endpoint
Maintains a history of recently played tracks with metadata
"""

import json
import requests
import time
import threading
from collections import deque
from datetime import datetime
import os

class TrackHistory:
    def __init__(self, max_history=50):
        self.history = deque(maxlen=max_history)
        self.current_track = None
        self.api_url = "http://127.0.0.1:5055/api/next"
        self.running = False
        self.lock = threading.Lock()
        
    def get_next_tracks(self):
        """Fetch upcoming tracks from API"""
        try:
            response = requests.get(self.api_url, timeout=5)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException as e:
            print(f"Error fetching next tracks: {e}")
        return []
    
    def is_music_track(self, track):
        """Check if track is actual music (not DJ/TTS)"""
        return (track.get('artist', '').lower() != 'ai dj' and 
                not track.get('filename', '').startswith('/opt/ai-radio/tts/'))
    
    def track_to_dict(self, track):
        """Convert track to standardized dict format"""
        return {
            'artist': track.get('artist', 'Unknown'),
            'title': track.get('title', 'Unknown'),
            'album': track.get('album', ''),
            'filename': track.get('filename', ''),
            'artwork_url': track.get('artwork_url', ''),
            'played_at': datetime.now().isoformat()
        }
    
    def monitor_tracks(self):
        """Monitor track changes and update history"""
        while self.running:
            try:
                tracks = self.get_next_tracks()
                if tracks:
                    # First track in queue is currently playing/about to play
                    current = tracks[0]
                    
                    if self.is_music_track(current):
                        # Check if this is a new track
                        track_id = f"{current.get('artist', '')}|{current.get('title', '')}"
                        
                        if self.current_track != track_id:
                            # New track detected
                            if self.current_track is not None:
                                # Previous track just finished, add to history
                                with self.lock:
                                    self.history.appendleft(self.track_to_dict(current))
                            
                            self.current_track = track_id
                            print(f"Now playing: {current.get('title')} by {current.get('artist')}")
                
                time.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                print(f"Error in monitor loop: {e}")
                time.sleep(30)  # Wait longer on error
    
    def get_just_played(self, count=10):
        """Get recently played tracks"""
        with self.lock:
            return list(self.history)[:count]
    
    def start_monitoring(self):
        """Start the monitoring thread"""
        self.running = True
        self.thread = threading.Thread(target=self.monitor_tracks, daemon=True)
        self.thread.start()
    
    def stop_monitoring(self):
        """Stop the monitoring thread"""
        self.running = False

# Global instance
track_history = TrackHistory()

def save_history_to_file():
    """Save current history to JSON file"""
    try:
        with open('/opt/ai-radio/track_history.json', 'w') as f:
            json.dump({
                'last_updated': datetime.now().isoformat(),
                'history': list(track_history.history)
            }, f, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")

def load_history_from_file():
    """Load history from JSON file"""
    try:
        if os.path.exists('/opt/ai-radio/track_history.json'):
            with open('/opt/ai-radio/track_history.json', 'r') as f:
                data = json.load(f)
                track_history.history.extend(data.get('history', []))
                print(f"Loaded {len(track_history.history)} tracks from history")
    except Exception as e:
        print(f"Error loading history: {e}")

if __name__ == '__main__':
    # Load existing history
    load_history_from_file()
    
    # Start monitoring
    track_history.start_monitoring()
    
    # Save history periodically
    while True:
        time.sleep(60)  # Save every minute
        save_history_to_file()