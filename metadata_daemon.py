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
from pathlib import Path

# Configuration
FLASK_API_BASE = "http://127.0.0.1:5055/api"
CACHE_DIR = "/opt/ai-radio/cache"
UPDATE_INTERVAL = 5  # seconds - much more frequent than before
API_TIMEOUT = 5.0

# Cache files
NOW_CACHE = os.path.join(CACHE_DIR, "now_metadata.json")
NEXT_CACHE = os.path.join(CACHE_DIR, "next_metadata.json")
REMAINING_CACHE = os.path.join(CACHE_DIR, "remaining_time.json")

# Global lock to prevent concurrent Liquidsoap access
liquidsoap_lock = threading.Lock()

def setup_cache_dir():
    """Ensure cache directory exists"""
    os.makedirs(CACHE_DIR, exist_ok=True)

def liquidsoap_command(cmd, timeout=2.0):
    """
    Execute a single command against Liquidsoap telnet interface.
    Returns list of response lines (without 'END').
    """
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
    """Get current track metadata from Flask API"""
    try:
        # Call Flask API for current metadata
        response = requests.get(f"{FLASK_API_BASE}/now", timeout=API_TIMEOUT)
        response.raise_for_status()
        
        metadata = response.json()
        print(f"Flask API metadata: {metadata}")
        
        # Add caching timestamp
        metadata["cached_at"] = time.time()
        
        return metadata
        
    except requests.exceptions.RequestException as e:
        print(f"Error calling Flask API for metadata: {e}")
        return {}
    except Exception as e:
        print(f"Error getting current metadata: {e}")
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
    """Get upcoming tracks from Flask API"""
    try:
        # Call Flask API for next tracks
        response = requests.get(f"{FLASK_API_BASE}/next", timeout=API_TIMEOUT)
        response.raise_for_status()
        
        next_tracks = response.json()
        print(f"Flask API next tracks: {len(next_tracks)} upcoming")
        
        return next_tracks
        
    except requests.exceptions.RequestException as e:
        print(f"Error calling Flask API for next tracks: {e}")
        return []
    except Exception as e:
        print(f"Error getting next tracks: {e}")
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