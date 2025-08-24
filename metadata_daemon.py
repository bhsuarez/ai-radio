#!/usr/bin/env python3
"""
Metadata caching daemon to prevent telnet connection storms.
This daemon periodically queries Liquidsoap and caches the results in JSON files.
"""

import json
import socket
import time
import os
import re
import threading
import requests
import urllib.parse
from pathlib import Path

# Configuration
LIQUIDSOAP_HOST = "127.0.0.1"
LIQUIDSOAP_PORT = 1234
CACHE_DIR = "/opt/ai-radio/cache"
UPDATE_INTERVAL = 3  # seconds - frequent updates for responsiveness
TELNET_TIMEOUT = 2.0

# Cache files
NOW_CACHE = os.path.join(CACHE_DIR, "now_metadata.json")
NEXT_CACHE = os.path.join(CACHE_DIR, "next_metadata.json")
REMAINING_CACHE = os.path.join(CACHE_DIR, "remaining_time.json")

# Global lock to prevent concurrent Liquidsoap access
liquidsoap_lock = threading.Lock()

def setup_cache_dir():
    """Ensure cache directory exists"""
    os.makedirs(CACHE_DIR, exist_ok=True)

def liquidsoap_command(cmd, timeout=None):
    """
    Execute a single command against Liquidsoap telnet interface.
    Returns list of response lines (without 'END').
    """
    if timeout is None:
        timeout = TELNET_TIMEOUT
        
    with liquidsoap_lock:
        try:
            sock = socket.create_connection((LIQUIDSOAP_HOST, LIQUIDSOAP_PORT), timeout=timeout)
            sock.settimeout(timeout)
            
            # Send command
            sock.sendall((cmd + "\nquit\n").encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            
            # Read response
            data = b""
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\nEND" in data or data.rstrip().endswith(b"END"):
                        break
                except socket.timeout:
                    break
                    
            sock.close()
            
            # Parse response
            lines = data.decode("utf-8", errors="ignore").splitlines()
            return [line for line in lines if line.strip() and line.strip() != "END"]
            
        except (socket.error, ConnectionRefusedError, OSError) as e:
            print(f"Liquidsoap connection error for '{cmd}': {e}")
            return []

def parse_kv_lines(lines):
    """Parse Liquidsoap key="value" lines into dict"""
    result = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"')
            result[key] = value
    return result

def get_current_metadata():
    """Get current track metadata with Liquidsoap/Icecast validation"""
    try:
        # First get what Icecast thinks is playing (source of truth)
        icecast_title = None
        try:
            response = requests.get("http://127.0.0.1:8000/status-json.xsl", timeout=3)
            if response.status_code == 200:
                icecast_data = response.json()
                icecast_title = icecast_data.get("icestats", {}).get("source", {}).get("title", "")
                print(f"Icecast shows: {icecast_title}")
        except Exception as e:
            print(f"Could not get Icecast metadata: {e}")
        
        # Now get Liquidsoap metadata
        # Get current metadata directly from Liquidsoap
        lines = liquidsoap_command("output.icecast.metadata")
        if not lines:
            print("No response from Liquidsoap metadata command")
            return {}
        
        # Parse metadata sections (--- 1 ---, --- 2 ---, etc.)
        sections = []
        current_section = {}
        
        for line in lines:
            line = line.strip()
            if line.startswith("--- ") and line.endswith(" ---"):
                if current_section:
                    sections.append(current_section)
                current_section = {}
            elif "=" in line and not line.startswith("itunsmpb") and not line.startswith("cover"):
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"')
                current_section[key] = val
        
        if current_section:
            sections.append(current_section)
        
        # Find the currently playing music track (use LAST section, which is the current one)
        current_track = None
        # Liquidsoap returns sections in reverse order, so search from the end
        for section in reversed(sections):
            # Skip AI DJ/TTS content, look for actual music
            if section.get("artist", "") != "AI DJ" and section.get("title", "") != "DJ Intro":
                current_track = section
                break
        
        # If no music track found, use first section but mark as DJ intro
        if not current_track and sections:
            current_track = sections[0]
            # If it's a DJ intro, mark it as such
            if current_track.get("artist") == "AI DJ" or current_track.get("title") == "DJ Intro":
                current_track["is_dj_intro"] = True
        
        if not current_track:
            print("No current track found in Liquidsoap response")
            return {}
        
        # Clean up filename path
        filename = current_track.get("filename", "") or current_track.get("initial_uri", "")
        if filename.startswith("file://"):
            filename = filename[7:]
        
        # Validate Liquidsoap metadata against Icecast (source of truth)
        liquidsoap_title = f"{current_track.get('artist', '')} - {current_track.get('title', '')}"
        if icecast_title:
            if liquidsoap_title.lower().strip() != icecast_title.lower().strip():
                print(f"METADATA MISMATCH!")
                print(f"  Liquidsoap: {liquidsoap_title}")
                print(f"  Icecast:    {icecast_title}")
                print(f"  Using Icecast as source of truth")
                
                # Parse Icecast title if it has " - " format
                if " - " in icecast_title:
                    icecast_artist, icecast_track = icecast_title.split(" - ", 1)
                    # Try to find matching Liquidsoap section for additional metadata
                    for section in sections:
                        if (section.get("artist", "").lower() == icecast_artist.lower().strip() and 
                            section.get("title", "").lower() == icecast_track.lower().strip()):
                            current_track = section
                            print(f"  Found matching Liquidsoap section for {icecast_title}")
                            break
                    else:
                        # No matching section, use Icecast data with minimal metadata
                        current_track = {
                            "title": icecast_track.strip(),
                            "artist": icecast_artist.strip(),
                            "album": "",
                            "genre": "",
                            "date": "",
                            "filename": ""
                        }
                        print(f"  No matching Liquidsoap section, using Icecast data only")
            else:
                print(f"Metadata validated: {liquidsoap_title}")
        
        # Build metadata object
        metadata = {
            "title": current_track.get("title", "Unknown"),
            "artist": current_track.get("artist", "Unknown"),
            "album": current_track.get("album", ""),
            "genre": current_track.get("genre", ""),
            "date": current_track.get("date", ""),
            "filename": filename,
            "cached_at": time.time(),
            "source": "liquidsoap_validated" if icecast_title else "liquidsoap_direct"
        }
        
        # Add artwork URL if we have a filename
        if filename:
            metadata["artwork_url"] = f"/api/cover?file={urllib.parse.quote(filename)}"
        
        print(f"Direct Liquidsoap metadata: {metadata}")
        
        # Check for stuck AI DJ metadata and fix automatically
        if icecast_title and "AI DJ" in icecast_title and "DJ Intro" in icecast_title:
            # If Icecast shows AI DJ but we determined current track is music, fix it
            if metadata.get("artist", "") != "AI DJ":
                print("METADATA STUCK: Icecast shows AI DJ but current track is music - fixing")
                try:
                    # Use TTS flush_and_skip which is more effective for stuck DJ intros
                    sock = socket.create_connection(("127.0.0.1", 1234), timeout=3)
                    sock.sendall(b"tts.flush_and_skip\nquit\n")
                    sock.close()
                    print("Applied fix: flushed TTS and skipped to clear stuck DJ intro")
                except Exception as e:
                    print(f"Failed to apply fix: {e}")
        
        return metadata
        
    except Exception as e:
        print(f"Error getting current metadata from Liquidsoap: {e}")
        return {}

def get_metadata_for_rid(rid):
    """Get metadata for a specific request ID"""
    try:
        lines = liquidsoap_command(f"request.metadata {rid}")
        metadata = parse_kv_lines(lines)
        
        filename = metadata.get("filename") or metadata.get("initial_uri", "")
        if filename.startswith("file://"):
            filename = filename[7:]
            
        return {
            "title": metadata.get("title", ""),
            "artist": metadata.get("artist", ""),
            "album": metadata.get("album", ""),
            "filename": filename,
            "artwork_url": f"/api/cover?file={filename}" if filename else ""
        }
    except Exception as e:
        print(f"Error getting metadata for RID {rid}: {e}")
        return {}

def get_next_tracks():
    """Get upcoming tracks from Harbor scheduler cache"""
    try:
        harbor_cache = "/opt/ai-radio/cache/harbor_queue.json"
        if os.path.exists(harbor_cache):
            with open(harbor_cache, 'r') as f:
                harbor_data = json.load(f)
                next_tracks = harbor_data.get('upcoming', [])
                
                # Add artwork URLs if missing
                for track in next_tracks:
                    if track.get('filename') and not track.get('artwork_url'):
                        track['artwork_url'] = f"/api/cover?file={urllib.parse.quote(track['filename'])}"
                
                print(f"Harbor next tracks: {len(next_tracks)} upcoming")
                return next_tracks
        
        # Fallback to placeholder if Harbor cache unavailable
        print("Harbor cache not available, using fallback")
        return [
            {"title": "Next Track Loading...", "artist": "Harbor Queue", "album": "", "filename": "", "artwork_url": None}
        ]
        
    except Exception as e:
        print(f"Error getting next tracks from Harbor cache: {e}")
        return []

def write_cache_file(filepath, data):
    """Atomically write cache file"""
    try:
        temp_path = filepath + ".tmp"
        with open(temp_path, 'w') as f:
            json.dump(data, f)
        os.rename(temp_path, filepath)
        print(f"Updated cache: {os.path.basename(filepath)}")
    except Exception as e:
        print(f"Error writing cache file {filepath}: {e}")

def daemon_loop():
    """Main daemon loop"""
    print("Starting metadata caching daemon...")
    setup_cache_dir()
    
    while True:
        try:
            # Update current track metadata
            current_metadata = get_current_metadata()
            if current_metadata:
                write_cache_file(NOW_CACHE, current_metadata)
            
            # Update next tracks (less frequently to avoid overload)
            if time.time() % 15 < UPDATE_INTERVAL:  # Every 15 seconds
                next_tracks = get_next_tracks()
                write_cache_file(NEXT_CACHE, next_tracks)
            
            time.sleep(UPDATE_INTERVAL)
            
        except KeyboardInterrupt:
            print("Daemon stopped by user")
            break
        except Exception as e:
            print(f"Daemon error: {e}")
            time.sleep(UPDATE_INTERVAL)

if __name__ == "__main__":
    daemon_loop()