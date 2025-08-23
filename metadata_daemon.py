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
from pathlib import Path

# Configuration
LIQUIDSOAP_HOST = "127.0.0.1"
LIQUIDSOAP_PORT = 1234
CACHE_DIR = "/opt/ai-radio/cache"
UPDATE_INTERVAL = 5  # seconds - much more frequent than before
LIQUIDSOAP_TIMEOUT = 2.0

# Cache files
NOW_CACHE = os.path.join(CACHE_DIR, "now_metadata.json")
NEXT_CACHE = os.path.join(CACHE_DIR, "next_metadata.json")
REMAINING_CACHE = os.path.join(CACHE_DIR, "remaining_time.json")

# Global lock to prevent concurrent Liquidsoap access
liquidsoap_lock = threading.Lock()

def setup_cache_dir():
    """Ensure cache directory exists"""
    os.makedirs(CACHE_DIR, exist_ok=True)

def liquidsoap_command(cmd, timeout=LIQUIDSOAP_TIMEOUT):
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
    """Get current track metadata from Liquidsoap"""
    try:
        # Get metadata from output.icecast.metadata
        lines = liquidsoap_command("output.icecast.metadata")
        raw_text = '\n'.join(lines)
        
        # Parse the "--- 1 ---" section (current track)
        current_block = []
        in_current = False
        
        for line in raw_text.splitlines():
            line = line.strip()
            if line == "--- 1 ---":
                in_current = True
                continue
            elif line.startswith("--- ") and line != "--- 1 ---":
                in_current = False
                break
            elif in_current and "=" in line:
                current_block.append(line)
        
        metadata = parse_kv_lines(current_block)
        
        # Get remaining time
        remaining_lines = liquidsoap_command("output.icecast.remaining")
        remaining_time = None
        if remaining_lines:
            try:
                remaining_str = remaining_lines[0].strip()
                if remaining_str.replace('.', '').replace('-', '').isdigit():
                    remaining_time = float(remaining_str)
            except (ValueError, IndexError):
                pass
        
        # Get filename from request metadata if not in main metadata
        filename = metadata.get("filename", "")
        if not filename:
            try:
                # Get current request ID and its metadata for filename
                rid_lines = liquidsoap_command("request.all")
                if rid_lines:
                    rids = []
                    for line in rid_lines:
                        rids.extend(x for x in line.strip().split() if x.isdigit())
                    if rids:
                        current_rid = rids[0]
                        rid_metadata = get_metadata_for_rid(current_rid)
                        filename = rid_metadata.get("filename", "")
            except Exception:
                pass
        
        # Enhanced metadata with timing
        result = {
            "title": metadata.get("title", "Unknown"),
            "artist": metadata.get("artist", "Unknown"),
            "album": metadata.get("album", ""),
            "filename": filename,
            "comment": metadata.get("comment", ""),
            "time": int(time.time() * 1000),
            "remaining_seconds": remaining_time,
            "duration_ms": None,
            "elapsed_ms": None,
            "cached_at": time.time()
        }
        
        # Try to get duration from file if we have a filename and remaining time
        if filename and remaining_time:
            try:
                import mutagen
                clean_filename = filename
                if clean_filename.startswith("file://"):
                    clean_filename = clean_filename[7:]
                    
                if os.path.isfile(clean_filename) and clean_filename.lower().endswith(('.mp3', '.m4a', '.flac', '.wav', '.ogg')):
                    audio = mutagen.File(clean_filename)
                    if audio and hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                        total_seconds = audio.info.length
                        result["duration_ms"] = int(total_seconds * 1000)
                        result["elapsed_ms"] = max(0, int((total_seconds - remaining_time) * 1000))
                        print(f"Added timing: duration={total_seconds:.1f}s, remaining={remaining_time:.1f}s, elapsed={total_seconds - remaining_time:.1f}s")
            except Exception as e:
                print(f"Error getting duration from {clean_filename}: {e}")
        
        return result
        
    except Exception as e:
        print(f"Error getting current metadata: {e}")
        return None

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
    """Get upcoming tracks from Liquidsoap queue"""
    try:
        # Get all request IDs
        rid_lines = liquidsoap_command("request.all")
        rids = []
        for line in rid_lines:
            rids.extend(x for x in line.strip().split() if x.isdigit())
        
        # Skip first RID (currently playing) and get metadata for upcoming tracks
        upcoming_rids = rids[1:] if len(rids) > 1 else []
        upcoming = []
        
        for rid in upcoming_rids:
            metadata = get_metadata_for_rid(rid)
            if metadata.get("title") or metadata.get("filename"):
                upcoming.append(metadata)
        
        return upcoming
        
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