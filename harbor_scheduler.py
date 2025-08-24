#!/usr/bin/env python3
"""
Harbor-based music scheduler that replaces telnet queue management.
Streams music to Liquidsoap's Harbor input with predictable queue.
"""

import os
import random
import time
import json
import threading
import subprocess
from pathlib import Path
import urllib.parse

HARBOR_URL = "http://127.0.0.1:8001/music"
PLAYLIST_FILE = "/opt/ai-radio/library_clean.m3u"
CACHE_DIR = "/opt/ai-radio/cache"
QUEUE_CACHE = os.path.join(CACHE_DIR, "harbor_queue.json")

class HarborScheduler:
    def __init__(self):
        self.queue = []
        self.current_track = None
        self.streaming_process = None
        self.load_playlist()
        self.fill_queue()
    
    def load_playlist(self):
        """Load and filter playlist files"""
        self.playlist = []
        if os.path.exists(PLAYLIST_FILE):
            with open(PLAYLIST_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and os.path.exists(line):
                        self.playlist.append(line)
        print(f"Loaded {len(self.playlist)} tracks from playlist")
    
    def fill_queue(self):
        """Fill queue with random tracks"""
        while len(self.queue) < 5:
            if self.playlist:
                track = random.choice(self.playlist)
                metadata = self.extract_metadata(track)
                self.queue.append({
                    'file': track,
                    'metadata': metadata
                })
        self.save_queue_cache()
    
    def extract_metadata(self, filepath):
        """Extract metadata from file path and ffprobe"""
        metadata = {
            'title': 'Unknown',
            'artist': 'Unknown',
            'album': '',
            'filename': filepath
        }
        
        # Try ffprobe first
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                tags = data.get('format', {}).get('tags', {})
                metadata.update({
                    'title': tags.get('TITLE', tags.get('title', metadata['title'])),
                    'artist': tags.get('ARTIST', tags.get('artist', metadata['artist'])),
                    'album': tags.get('ALBUM', tags.get('album', metadata['album']))
                })
        except Exception as e:
            print(f"ffprobe failed for {filepath}: {e}")
        
        # Fallback to filename parsing
        if metadata['title'] == 'Unknown' or metadata['artist'] == 'Unknown':
            basename = os.path.basename(filepath)
            if ' - ' in basename:
                artist, title = basename.split(' - ', 1)
                title = title.rsplit('.', 1)[0]  # Remove extension
                metadata['title'] = title
                metadata['artist'] = artist
        
        return metadata
    
    def get_next_tracks(self, count=3):
        """Get upcoming tracks for API"""
        return [track['metadata'] for track in self.queue[:count]]
    
    def save_queue_cache(self):
        """Save queue state for web UI"""
        os.makedirs(CACHE_DIR, exist_ok=True)
        queue_data = {
            'current': self.current_track,
            'upcoming': self.get_next_tracks(),
            'updated_at': time.time()
        }
        with open(QUEUE_CACHE, 'w') as f:
            json.dump(queue_data, f)
    
    def stream_track(self, track_info):
        """Stream a single track to Harbor"""
        filepath = track_info['file']
        metadata = track_info['metadata']
        
        print(f"Streaming: {metadata['artist']} - {metadata['title']}")
        
        # Use ffmpeg to stream to Harbor with metadata
        cmd = [
            'ffmpeg', '-re', '-i', filepath,
            '-c:a', 'mp3', '-b:a', '128k',
            '-metadata', f"title={metadata['title']}",
            '-metadata', f"artist={metadata['artist']}",
            '-metadata', f"album={metadata['album']}",
            '-f', 'mp3',
            '-content_type', 'audio/mpeg',
            HARBOR_URL
        ]
        
        try:
            self.streaming_process = subprocess.run(
                cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                timeout=600  # 10 minute max per track
            )
            return True
        except subprocess.TimeoutExpired:
            print(f"Track timeout: {metadata['title']}")
            return False
        except Exception as e:
            print(f"Streaming error: {e}")
            return False
    
    def run(self):
        """Main scheduler loop"""
        print("Harbor scheduler starting...")
        
        while True:
            try:
                # Ensure queue is filled
                self.fill_queue()
                
                # Get next track
                if self.queue:
                    track_info = self.queue.pop(0)
                    self.current_track = track_info['metadata']
                    self.save_queue_cache()
                    
                    # Stream the track
                    self.stream_track(track_info)
                    
                    # Small gap between tracks
                    time.sleep(2)
                else:
                    print("Queue empty, waiting...")
                    time.sleep(5)
                    
            except KeyboardInterrupt:
                print("Scheduler stopped by user")
                break
            except Exception as e:
                print(f"Scheduler error: {e}")
                time.sleep(5)

if __name__ == '__main__':
    scheduler = HarborScheduler()
    scheduler.run()