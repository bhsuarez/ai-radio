#!/usr/bin/env python3
"""
Efficient DJ Intro Generation System

This script generates DJ intros for the NEXT track while the current track is playing,
using efficient batched telnet queries to avoid connection spam.
"""

import os
import sys
import json
import time
import socket
import subprocess
import threading
import re
import requests
from typing import Dict, List, Optional, Tuple

# Configuration
LS_HOST = "127.0.0.1"
LS_PORT = 1234
TTS_DIR = "/opt/ai-radio/tts"
INTRO_CACHE_FILE = "/opt/ai-radio/intro_cache.json"
LOCK_FILE = "/tmp/dj_intro_generation.lock"

class EfficientTelnetManager:
    """Manages efficient telnet connections with batched queries"""
    
    def __init__(self, host: str = LS_HOST, port: int = LS_PORT, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
    
    def batch_query(self, commands: List[str]) -> Dict[str, str]:
        """Execute multiple commands in one telnet session"""
        results = {}
        
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                
                # Consume any banner
                try:
                    s.recv(1024)
                except:
                    pass
                
                for cmd in commands:
                    # Send command
                    s.sendall(f"{cmd}\n".encode("utf-8"))
                    
                    # Read response - simple approach, just collect everything until timeout
                    response = b""
                    start_time = time.time()
                    
                    while time.time() - start_time < 2.0:  # 2 second timeout per command
                        try:
                            chunk = s.recv(4096)
                            if not chunk:
                                break
                            response += chunk
                            
                            # Check if we got END marker
                            if b"END" in response:
                                break
                                
                        except socket.timeout:
                            break
                    
                    # Parse the response - data comes directly, ends with END
                    response_text = response.decode('utf-8', errors='ignore')
                    lines = response_text.replace('\r', '').split('\n')
                    
                    # Collect all lines before "END"
                    collected_lines = []
                    
                    for line in lines:
                        line = line.strip()
                        if line == "END":
                            break
                        elif line:  # Skip empty lines
                            collected_lines.append(line)
                    
                    results[cmd] = '\n'.join(collected_lines)
                
                # Clean disconnect
                s.sendall(b"quit\n")
                
        except Exception as e:
            print(f"ERROR: Telnet batch query failed: {e}", file=sys.stderr)
            
        return results

class IntroCache:
    """Manages intro generation cache to avoid duplicate work"""
    
    def __init__(self, cache_file: str = INTRO_CACHE_FILE):
        self.cache_file = cache_file
        self.cache = self._load_cache()
    
    def _load_cache(self) -> Dict[str, Dict]:
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"WARNING: Could not save cache: {e}", file=sys.stderr)
    
    def get_intro_path(self, artist: str, title: str) -> Optional[str]:
        """Get cached intro file path if it exists and is recent"""
        key = f"{artist}|{title}".lower()
        
        if key in self.cache:
            entry = self.cache[key]
            file_path = entry.get('file_path')
            
            # Check if file exists and is less than 1 hour old
            if (file_path and os.path.exists(file_path) and 
                time.time() - entry.get('timestamp', 0) < 3600):
                return file_path
        
        return None
    
    def cache_intro(self, artist: str, title: str, file_path: str):
        """Cache a generated intro file"""
        key = f"{artist}|{title}".lower()
        self.cache[key] = {
            'artist': artist,
            'title': title,
            'file_path': file_path,
            'timestamp': time.time()
        }
        self._save_cache()

def get_queue_metadata() -> Tuple[Optional[Dict], Optional[Dict]]:
    """Get current and next track metadata using Flask API (more reliable than telnet)"""
    try:
        # Use Flask API which handles telnet efficiently
        current_response = requests.get("http://127.0.0.1:5055/api/now", timeout=5)
        next_response = requests.get("http://127.0.0.1:5055/api/next", timeout=5)
        
        current = current_response.json() if current_response.ok else None
        next_list = next_response.json() if next_response.ok else []
        
        # Get the first actual song (not DJ content) from next list
        next_track = None
        for item in next_list:
            if item.get('type') == 'song':
                next_track = item
                break
        
        print(f"DEBUG: Current track: {current.get('title', 'Unknown') if current else 'None'} by {current.get('artist', 'Unknown') if current else 'None'}", file=sys.stderr)
        print(f"DEBUG: Next track: {next_track.get('title', 'Unknown') if next_track else 'None'} by {next_track.get('artist', 'Unknown') if next_track else 'None'}", file=sys.stderr)
        
        return current, next_track
        
    except Exception as e:
        print(f"ERROR: Failed to get track metadata via API: {e}", file=sys.stderr)
        return None, None

def generate_intro_for_track(artist: str, title: str, cache: IntroCache) -> Optional[str]:
    """Generate TTS intro for a track, using cache if available"""
    
    # Check cache first
    cached_path = cache.get_intro_path(artist, title)
    if cached_path:
        print(f"Using cached intro for '{title}' by {artist}: {cached_path}")
        return cached_path
    
    # Generate new intro
    print(f"Generating new intro for '{title}' by {artist}")
    
    # Update status - starting generation
    update_dj_status("start_generation", {"artist": artist, "title": title})
    
    try:
        # Use existing XTTS generation script
        result = subprocess.run([
            "/opt/ai-radio/dj_enqueue_xtts.sh",
            artist, title, "en", os.getenv("XTTS_SPEAKER", "Damien Black")
        ], capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0:
            # Extract the output file path from stdout (last line should be the file path)
            stdout_lines = result.stdout.strip().split('\n')
            output_file = stdout_lines[-1].strip() if stdout_lines else ""
            
            # Validate it's a file path and exists
            if output_file and output_file.startswith('/') and os.path.exists(output_file):
                cache.cache_intro(artist, title, output_file)
                print(f"Successfully generated intro: {output_file}")
                
                # Update status - generation complete
                update_dj_status("complete_generation", {"intro_file": output_file})
                return output_file
        
        error_msg = f"Script failed: {result.stderr}"
        print(f"ERROR: Intro generation failed: {error_msg}")
        update_dj_status("fail_generation", {"error": error_msg})
        
    except Exception as e:
        error_msg = f"Exception during generation: {e}"
        print(f"ERROR: {error_msg}")
        update_dj_status("fail_generation", {"error": error_msg})
    
    return None

def update_dj_status(status_type: str, data: dict = None):
    """Update the DJ generation status file for web UI"""
    try:
        status_file = "/opt/ai-radio/dj_status.json"
        
        # Load current status
        if os.path.exists(status_file):
            with open(status_file, 'r') as f:
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
        elif status_type == "complete_generation":
            if status["current_generation"]:
                # Move to recent intros
                completed = status["current_generation"].copy()
                completed["completed_at"] = time.time()
                completed["status"] = "completed"
                completed["intro_file"] = data.get("intro_file")
                
                status["recent_intros"].insert(0, completed)
                status["recent_intros"] = status["recent_intros"][:5]  # Keep last 5
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
        
        # Write back to file
        with open(status_file, 'w') as f:
            json.dump(status, f, indent=2)
            
    except Exception as e:
        print(f"WARNING: Could not update DJ status: {e}", file=sys.stderr)

def enqueue_intro_to_liquidsoap(intro_file: str) -> bool:
    """Enqueue generated intro to Liquidsoap TTS queue"""
    if not os.path.exists(intro_file):
        print(f"ERROR: Intro file does not exist: {intro_file}")
        return False
    
    try:
        # Use simple subprocess approach like the Flask API does
        command = f'tts.push file://{intro_file}'
        result = subprocess.run(
            ["nc", "127.0.0.1", "1234"],
            input=f"{command}\nquit\n".encode(),
            capture_output=True,
            timeout=5
        )
        
        if result.returncode == 0:
            print(f"Successfully enqueued intro to Liquidsoap: {intro_file}")
            if result.stdout:
                print(f"Liquidsoap response: {result.stdout.decode().strip()}")
            return True
        else:
            print(f"ERROR: Failed to enqueue intro, return code: {result.returncode}")
            if result.stderr:
                print(f"Stderr: {result.stderr.decode()}")
            return False
        
    except Exception as e:
        print(f"ERROR: Exception while enqueueing intro: {e}")
        return False

def store_intro_mapping(artist: str, title: str, intro_file: str):
    """Store mapping between track and intro file for later playback"""
    mapping_file = "/opt/ai-radio/intro_mapping.json"
    
    try:
        # Load existing mappings
        mappings = {}
        if os.path.exists(mapping_file):
            with open(mapping_file, 'r') as f:
                mappings = json.load(f)
        
        # Store new mapping
        key = f"{artist}|{title}".lower()
        mappings[key] = {
            "artist": artist,
            "title": title,
            "intro_file": intro_file,
            "created_at": time.time(),
            "played": False
        }
        
        # Save mappings
        with open(mapping_file, 'w') as f:
            json.dump(mappings, f, indent=2)
            
        print(f"Stored intro mapping: {artist} - {title} -> {intro_file}")
        
    except Exception as e:
        print(f"ERROR: Failed to store intro mapping: {e}")

def check_and_play_pending_intro(artist: str, title: str) -> bool:
    """Check if there's a pending intro for this track and play it"""
    mapping_file = "/opt/ai-radio/intro_mapping.json"
    
    try:
        if not os.path.exists(mapping_file):
            return False
            
        with open(mapping_file, 'r') as f:
            mappings = json.load(f)
        
        key = f"{artist}|{title}".lower()
        
        if key in mappings and not mappings[key].get("played", False):
            intro_info = mappings[key]
            intro_file = intro_info["intro_file"]
            
            # Check if intro file still exists
            if os.path.exists(intro_file):
                # Enqueue the intro
                if enqueue_intro_to_liquidsoap(intro_file):
                    # Mark as played
                    mappings[key]["played"] = True
                    mappings[key]["played_at"] = time.time()
                    
                    # Save updated mappings
                    with open(mapping_file, 'w') as f:
                        json.dump(mappings, f, indent=2)
                    
                    print(f"Successfully played pending intro for '{title}' by {artist}")
                    return True
                else:
                    print(f"Failed to enqueue pending intro for '{title}' by {artist}")
            else:
                print(f"Intro file missing for '{title}' by {artist}: {intro_file}")
        
    except Exception as e:
        print(f"ERROR: Failed to check pending intro: {e}")
    
    return False

def main():
    """Main intro generation logic"""
    
    # Simple file lock to prevent multiple instances
    if os.path.exists(LOCK_FILE):
        print("Another intro generation process is running, exiting.")
        sys.exit(0)
    
    try:
        # Create lock
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
        
        cache = IntroCache()
        
        # Get current and next track metadata
        current_track, next_track = get_queue_metadata()
        
        if not next_track:
            print("No next track found, nothing to generate")
            return
        
        next_artist = next_track.get('artist', 'Unknown Artist')
        next_title = next_track.get('title', 'Unknown Title')
        
        if not next_title or next_title == 'Unknown Title':
            print("Next track has no title, skipping intro generation")
            return
        
        print(f"Current track: {current_track.get('title', 'Unknown')} by {current_track.get('artist', 'Unknown')}")
        print(f"Next track: {next_title} by {next_artist}")
        
        # Generate intro for next track
        intro_file = generate_intro_for_track(next_artist, next_title, cache)
        
        if intro_file:
            # Immediately enqueue the intro to Liquidsoap TTS queue (whether cached or newly generated)
            if enqueue_intro_to_liquidsoap(intro_file):
                print(f"Successfully prepared and enqueued intro for '{next_title}' by {next_artist}")
                # Also store mapping as backup
                store_intro_mapping(next_artist, next_title, intro_file)
            else:
                print(f"Generated intro file but failed to enqueue it: {intro_file}")
        else:
            print("Failed to generate intro")
    
    finally:
        # Remove lock
        try:
            os.remove(LOCK_FILE)
        except:
            pass

if __name__ == "__main__":
    main()