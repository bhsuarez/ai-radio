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

# Liquidsoap connection constants
LS_HOST = "127.0.0.1"
LS_PORT = 1234

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
        """Get current and next tracks from cached metadata (no API calls)"""
        try:
            # First try to read from daemon cache
            now_cache_file = "/opt/ai-radio/cache/now_metadata.json"
            next_cache_file = "/opt/ai-radio/cache/next_metadata.json"
            
            current = None
            next_list = []
            
            # Read current track from cache
            if os.path.exists(now_cache_file):
                try:
                    with open(now_cache_file, 'r') as f:
                        cached_data = json.load(f)
                    
                    # Check if cache is fresh (less than 30 seconds old)
                    cache_age = time.time() - cached_data.get("cached_at", 0)
                    if cache_age < 30:
                        current = cached_data
                        print(f"DJ Daemon: Using cached current track (age: {cache_age:.1f}s)")
                    else:
                        print(f"DJ Daemon: Current track cache is stale (age: {cache_age:.1f}s)")
                except Exception as e:
                    print(f"DJ Daemon: Error reading current track cache: {e}")
            
            # Read next tracks from cache
            if os.path.exists(next_cache_file):
                try:
                    with open(next_cache_file, 'r') as f:
                        next_list = json.load(f)
                    
                    # Check if cache is fresh
                    cache_age = time.time() - os.path.getmtime(next_cache_file)
                    if cache_age > 60:  # Next tracks can be a bit more stale
                        print(f"DJ Daemon: Next tracks cache is stale (age: {cache_age:.1f}s)")
                        next_list = []
                    else:
                        print(f"DJ Daemon: Using cached next tracks (age: {cache_age:.1f}s)")
                except Exception as e:
                    print(f"DJ Daemon: Error reading next tracks cache: {e}")
                    next_list = []
            
            # If cache failed, fallback to API calls
            if not current or not next_list:
                print("DJ Daemon: Cache unavailable, falling back to API calls")
                current_response = requests.get(f"{self.api_base}/api/now", timeout=10)
                next_response = requests.get(f"{self.api_base}/api/next", timeout=10)
                
                if not current:
                    current = current_response.json() if current_response.ok else None
                if not next_list:
                    next_list = next_response.json() if next_response.ok else []
            
            # Get track that's further ahead - 2nd in queue to account for generation time
            next_track = next_list[1] if len(next_list) > 1 else (next_list[0] if next_list else None)
            
            return current, next_track
            
        except Exception as e:
            print(f"DJ Daemon: Failed to get track info: {e}")
            return None, None
    
    
    def push_to_tts_queue(self, file_path: str, target_track: Dict = None):
        """Push intro file to Liquidsoap TTS queue with proper metadata"""
        try:
            # Create annotated request with DJ intro metadata
            artist_info = ""
            title_info = ""
            if target_track:
                artist_info = f" (Introducing {target_track.get('artist', 'Unknown Artist')})"
                title_info = f" - {target_track.get('title', 'Unknown Title')}"
            
            # Use web API instead of direct telnet to avoid connection storms
            data = {
                "type": "dj",
                "text": f"DJ intro for upcoming track",
                "audio_file": file_path,
                "metadata": {
                    "artist": "AI DJ",
                    "title": f"DJ Intro{title_info}",
                    "album": "AI Radio"
                }
            }
            
            response = requests.post(f"{self.api_base}/api/tts_queue", json=data, timeout=10)
            
            if response.ok:
                print(f"DJ Daemon: Successfully queued intro via API with metadata")
            else:
                print(f"DJ Daemon: API queue failed ({response.status_code}): {response.text}")
                # Fallback to telnet with annotated request
                self._fallback_telnet_push_annotated(file_path, target_track)
                
        except Exception as e:
            print(f"DJ Daemon: API push failed: {e}")
            # Fallback to telnet with annotated request
            self._fallback_telnet_push_annotated(file_path, target_track)
    
    def _fallback_telnet_push_annotated(self, file_path: str, target_track: Dict = None):
        """Fallback telnet push with annotated metadata (only used when API fails)"""
        try:
            print("DJ Daemon: Falling back to telnet push with metadata (API unavailable)")
            import socket
            with socket.create_connection((LS_HOST, LS_PORT), timeout=5) as s:
                s.settimeout(5)
                # Consume banner
                try:
                    s.recv(1024)
                except:
                    pass
                
                # Create annotated request with DJ intro metadata
                title_info = ""
                if target_track:
                    title_info = f" - {target_track.get('title', 'Unknown Title')}"
                
                # Use annotate: prefix to set metadata for this request
                annotated_path = f'annotate:artist="AI DJ",title="DJ Intro{title_info}",album="AI Radio":{file_path}'
                
                # Push annotated file to TTS queue
                command = f'tts.push {annotated_path}\nquit\n'
                s.send(command.encode())
                
                # Read response
                response = s.recv(1024).decode().strip()
                print(f"DJ Daemon: Telnet annotated TTS queue response: {response}")
                
        except Exception as e:
            print(f"DJ Daemon: Fallback annotated telnet push failed: {e}")
    
    def _fallback_telnet_push(self, file_path: str):
        """Simple fallback telnet push (deprecated, use annotated version)"""
        try:
            print("DJ Daemon: Falling back to simple telnet push (API unavailable)")
            import socket
            with socket.create_connection((LS_HOST, LS_PORT), timeout=5) as s:
                s.settimeout(5)
                # Consume banner
                try:
                    s.recv(1024)
                except:
                    pass
                
                # Push file to TTS queue
                command = f"tts.push {file_path}\nquit\n"
                s.send(command.encode())
                
                # Read response
                response = s.recv(1024).decode().strip()
                print(f"DJ Daemon: Telnet TTS queue response: {response}")
                
        except Exception as e:
            print(f"DJ Daemon: Fallback telnet push failed: {e}")
    
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
    
    def enqueue_intro(self, intro_file: str, target_track: Dict) -> bool:
        """Push intro to TTS queue immediately with metadata"""
        try:
            # Push to TTS queue with target track metadata
            self.push_to_tts_queue(intro_file, target_track)
            print(f"Enqueued intro for upcoming track: {target_track.get('title')} by {target_track.get('artist')}")
            return True
            
        except Exception as e:
            print(f"Failed to enqueue intro: {e}")
            return False
    
    def should_generate_intro(self, current: Dict, next_track: Dict) -> bool:
        """Determine if we should generate an intro now"""
        if not current or not next_track:
            return False
        
        # Cooldown check - always check this first
        if time.time() - self.last_generation_time < self.generation_cooldown:
            return False
        
        # Check if intro already exists
        artist = next_track.get('artist', '')
        title = next_track.get('title', '')
        
        if not artist or not title:
            return False
        
        # Skip AI DJ tracks entirely
        if artist.lower() == 'ai dj' or title.lower() == 'dj intro':
            return False
        
        if self.is_intro_cached(artist, title):
            print(f"Intro already cached for '{title}' by {artist}")
            return False
        
        # Check remaining time (need at least 120 seconds to generate safely)
        remaining = current.get('remaining_seconds', 0)
        if remaining > 0 and remaining < 120:
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
                loop_start = time.time()
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
                            self.enqueue_intro(cached_intro, next_track)
                        else:
                            # Generate new intro
                            intro_file = self.generate_intro(artist, title)
                            if intro_file:
                                self.enqueue_intro(intro_file, next_track)
                        
                        self.last_generation_time = time.time()
                    
                except Exception as e:
                    print(f"Daemon loop error: {e}")
                
                # Always ensure minimum 30 second delay between loops
                loop_duration = time.time() - loop_start
                sleep_time = max(30 - loop_duration, 5)  # Minimum 5 second delay
                time.sleep(sleep_time)
                    
        finally:
            self.remove_lock()
            print("DJ Daemon stopped")

def main():
    """Main entry point"""
    daemon = DJDaemon()
    daemon.run()

if __name__ == "__main__":
    main()