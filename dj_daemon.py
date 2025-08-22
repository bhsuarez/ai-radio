#!/usr/bin/env python3
"""
AI DJ Daemon

A persistent daemon process that handles DJ intro generation without causing
telnet spam or process proliferation. Uses connection pooling and intelligent
timing to generate intros for upcoming tracks.
"""

import os
import sys
import time
import json
import signal
import threading
import requests
from typing import Dict, Optional, Tuple
from liquidsoap_connection_pool import get_liquidsoap_pool, liquidsoap_query, liquidsoap_batch_query

class DJDaemon:
    """Persistent daemon for AI DJ intro generation"""
    
    def __init__(self):
        self.running = True
        self.current_track = None
        self.next_track = None
        self.last_generation_time = 0
        self.generation_cooldown = 60  # Minimum 60 seconds between generations
        
        # Paths and configuration
        self.status_file = "/opt/ai-radio/dj_status.json"
        self.cache_file = "/opt/ai-radio/intro_cache.json"
        self.lock_file = "/tmp/dj_daemon.lock"
        self.api_base = "http://127.0.0.1:5055"
        
        # Load configuration
        self.load_cache()
        
        # Signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def create_lock(self) -> bool:
        """Create daemon lock file"""
        try:
            if os.path.exists(self.lock_file):
                # Check if existing process is still running
                with open(self.lock_file, 'r') as f:
                    old_pid = int(f.read().strip())
                
                try:
                    os.kill(old_pid, 0)  # Test if process exists
                    print(f"DJ daemon already running (PID {old_pid})")
                    return False
                except ProcessLookupError:
                    # Process doesn't exist, remove stale lock
                    os.remove(self.lock_file)
            
            # Create new lock
            with open(self.lock_file, 'w') as f:
                f.write(str(os.getpid()))
            
            return True
            
        except Exception as e:
            print(f"Failed to create lock file: {e}")
            return False
    
    def remove_lock(self):
        """Remove daemon lock file"""
        try:
            os.remove(self.lock_file)
        except:
            pass
    
    def load_cache(self):
        """Load intro cache from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    self.intro_cache = json.load(f)
            else:
                self.intro_cache = {}
        except:
            self.intro_cache = {}
    
    def save_cache(self):
        """Save intro cache to disk"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.intro_cache, f, indent=2)
        except Exception as e:
            print(f"Failed to save cache: {e}")
    
    def update_status(self, status_type: str, data: dict = None):
        """Update DJ status file for web UI"""
        try:
            if os.path.exists(self.status_file):
                with open(self.status_file, 'r') as f:
                    status = json.load(f)
            else:
                status = {
                    "current_generation": None,
                    "generation_queue": [],
                    "recent_intros": [],
                    "last_updated": 0
                }
            
            status["last_updated"] = time.time()
            
            if status_type == "start_generation":
                status["current_generation"] = {
                    "artist": data.get("artist"),
                    "title": data.get("title"),
                    "started_at": time.time(),
                    "status": "generating"
                }
                # Clear previous intro history when starting new generation
                status["recent_intros"] = []
            elif status_type == "complete_generation":
                if status["current_generation"]:
                    completed = status["current_generation"].copy()
                    completed["completed_at"] = time.time()
                    completed["status"] = "completed"
                    completed["intro_file"] = data.get("intro_file")
                    
                    status["recent_intros"].insert(0, completed)
                    status["recent_intros"] = status["recent_intros"][:5]
                    status["current_generation"] = None
            elif status_type == "fail_generation":
                if status["current_generation"]:
                    failed = status["current_generation"].copy()
                    failed["completed_at"] = time.time()
                    failed["status"] = "failed"
                    failed["error"] = data.get("error", "Unknown error")
                    
                    status["recent_intros"].insert(0, failed)
                    status["recent_intros"] = status["recent_intros"][:5]
                    status["current_generation"] = None
            
            with open(self.status_file, 'w') as f:
                json.dump(status, f, indent=2)
                
        except Exception as e:
            print(f"Failed to update status: {e}")
    
    def get_current_and_next_tracks(self) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Get current and next tracks from APIs (not telnet)"""
        try:
            # Use existing APIs to avoid telnet spam
            current_response = requests.get(f"{self.api_base}/api/now", timeout=5)
            next_response = requests.get(f"{self.api_base}/api/next", timeout=5)
            
            current = current_response.json() if current_response.ok else None
            next_list = next_response.json() if next_response.ok else []
            
            next_track = next_list[0] if next_list else None
            
            return current, next_track
            
        except Exception as e:
            print(f"Failed to get track info: {e}")
            return None, None
    
    def is_intro_cached(self, artist: str, title: str) -> Optional[str]:
        """Check if intro is cached and still valid"""
        key = f"{artist}|{title}".lower()
        
        if key in self.intro_cache:
            entry = self.intro_cache[key]
            file_path = entry.get('file_path')
            
            # Check if file exists and is less than 24 hours old
            if (file_path and os.path.exists(file_path) and 
                time.time() - entry.get('timestamp', 0) < 86400):
                return file_path
        
        return None
    
    def cache_intro(self, artist: str, title: str, file_path: str):
        """Cache generated intro"""
        key = f"{artist}|{title}".lower()
        self.intro_cache[key] = {
            'artist': artist,
            'title': title,
            'file_path': file_path,
            'timestamp': time.time()
        }
        self.save_cache()
    
    def generate_intro(self, artist: str, title: str) -> Optional[str]:
        """Generate intro using existing XTTS script"""
        print(f"Generating intro for '{title}' by {artist}")
        
        self.update_status("start_generation", {"artist": artist, "title": title})
        
        try:
            import subprocess
            result = subprocess.run([
                "/opt/ai-radio/dj_enqueue_xtts.sh",
                artist, title, "en", os.getenv("XTTS_SPEAKER", "Damien Black")
            ], capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                # Extract the output file path from stdout - it appears at the end
                output_lines = result.stdout.strip().split('\n')
                output_file = None
                for line in reversed(output_lines):
                    line = line.strip()
                    if line.startswith('/opt/ai-radio/tts/') and line.endswith('.mp3'):
                        output_file = line
                        break
                
                if output_file and os.path.exists(output_file):
                    self.cache_intro(artist, title, output_file)
                    self.update_status("complete_generation", {"intro_file": output_file})
                    print(f"Successfully generated: {output_file}")
                    return output_file
            
            error_msg = f"Script failed: {result.stderr}"
            self.update_status("fail_generation", {"error": error_msg})
            print(f"Generation failed: {error_msg}")
            
        except Exception as e:
            error_msg = f"Exception: {e}"
            self.update_status("fail_generation", {"error": error_msg})
            print(f"Generation error: {error_msg}")
        
        return None
    
    def enqueue_intro(self, intro_file: str) -> bool:
        """Enqueue intro to Liquidsoap TTS queue"""
        try:
            pool = get_liquidsoap_pool()
            command = f'tts.push file://{intro_file}'
            result = pool.execute_command(command)
            print(f"Enqueued intro: {intro_file}")
            return True
        except Exception as e:
            print(f"Failed to enqueue intro: {e}")
            return False
    
    def should_generate_intro(self, current: Dict, next_track: Dict) -> bool:
        """Determine if we should generate an intro now"""
        if not current or not next_track:
            return False
        
        # Cooldown check
        if time.time() - self.last_generation_time < self.generation_cooldown:
            return False
        
        # Check if intro already exists
        artist = next_track.get('artist', '')
        title = next_track.get('title', '')
        
        if not artist or not title:
            return False
        
        if self.is_intro_cached(artist, title):
            print(f"Intro already cached for '{title}' by {artist}")
            return False
        
        # Check remaining time (need at least 90 seconds to generate)
        remaining = current.get('remaining_seconds', 0)
        if remaining > 0 and remaining < 90:
            print(f"Not enough time remaining ({remaining}s) to generate intro")
            return False
        
        return True
    
    def run(self):
        """Main daemon loop"""
        if not self.create_lock():
            sys.exit(1)
        
        print(f"DJ Daemon started (PID {os.getpid()})")
        
        try:
            while self.running:
                try:
                    # Get current track info
                    current, next_track = self.get_current_and_next_tracks()
                    
                    if self.should_generate_intro(current, next_track):
                        artist = next_track['artist']
                        title = next_track['title']
                        
                        print(f"Generating intro for next track: '{title}' by {artist}")
                        
                        # Check cache first
                        cached_intro = self.is_intro_cached(artist, title)
                        if cached_intro:
                            self.enqueue_intro(cached_intro)
                        else:
                            # Generate new intro
                            intro_file = self.generate_intro(artist, title)
                            if intro_file:
                                self.enqueue_intro(intro_file)
                        
                        self.last_generation_time = time.time()
                    
                    # Sleep before next check
                    time.sleep(30)
                    
                except Exception as e:
                    print(f"Daemon loop error: {e}")
                    time.sleep(60)  # Longer sleep on error
                    
        finally:
            self.remove_lock()
            print("DJ Daemon stopped")

def main():
    """Main entry point"""
    daemon = DJDaemon()
    daemon.run()

if __name__ == "__main__":
    main()