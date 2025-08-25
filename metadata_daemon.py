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
import sys

# Add current directory to path for database import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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

# Track change detection
last_track_id = None
track_started_at = None

def setup_cache_dir():
    """Ensure cache directory exists"""
    os.makedirs(CACHE_DIR, exist_ok=True)

def liquidsoap_command(cmd, timeout=None):
    """
    DEPRECATED: Telnet-based command execution replaced by Harbor HTTP.
    This function now returns empty results to maintain compatibility.
    Use Icecast status API for metadata instead.
    """
    print(f"WARNING: liquidsoap_command({cmd}) called - telnet deprecated, returning empty result")
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

def parse_metadata_sections(lines):
    """Parse Liquidsoap metadata sections (--- 1 ---, --- 2 ---, etc.) into dict"""
    sections = {}
    current_section = {}
    current_section_num = None
    
    for line in lines:
        line = line.strip()
        if line.startswith("--- ") and line.endswith(" ---"):
            # Save previous section if it exists
            if current_section_num is not None and current_section:
                sections[current_section_num] = current_section
            
            # Extract section number
            try:
                section_str = line[4:-4].strip()  # Remove "--- " and " ---"
                current_section_num = int(section_str)
                current_section = {}
            except ValueError:
                print(f"Warning: Could not parse section number from '{line}'")
                continue
                
        elif "=" in line and current_section_num is not None:
            # Parse key=value lines within section
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"')
            current_section[key] = value
    
    # Don't forget the last section
    if current_section_num is not None and current_section:
        sections[current_section_num] = current_section
    
    print(f"Parsed {len(sections)} metadata sections")
    return sections

def get_current_metadata():
    """Get current track metadata with Liquidsoap/Icecast validation"""
    global last_track_id, track_started_at
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
        
        # Since telnet is disabled in Liquidsoap, use Icecast metadata directly
        print("Using Icecast metadata only (telnet disabled in Liquidsoap)")
        
        current_track = {}
        filename = ""
        
        if icecast_title:
            # Parse Icecast title format: "Artist - Title" 
            if " - " in icecast_title and not icecast_title.startswith("AI DJ"):
                parts = icecast_title.split(" - ", 1)  # Split on first " - " only
                if len(parts) == 2:
                    current_track = {
                        "artist": parts[0].strip(),
                        "title": parts[1].strip(),
                        "album": "",
                        "genre": "",
                        "date": ""
                    }
                    print(f"Parsed track: {current_track['artist']} - {current_track['title']}")
            else:
                # Handle other formats or AI DJ titles
                current_track = {
                    "artist": "Unknown",
                    "title": icecast_title,
                    "album": "",
                    "genre": "",
                    "date": ""
                }
        
        # We already have the current track from Icecast parsing above
        # No need to search through sections since we parsed it directly
        
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
        
        # Detect track changes and update start time
        current_track_id = f"{metadata['artist']}|{metadata['title']}|{filename}"
        if current_track_id != last_track_id:
            print(f"Track changed: {last_track_id} -> {current_track_id}")
            last_track_id = current_track_id
            track_started_at = time.time()
        
        # Add track start timestamp
        metadata["track_started_at"] = track_started_at
        
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
                    # Use Harbor HTTP skip instead of telnet (more reliable)
                    import subprocess
                    import tempfile
                    
                    # Create very short silence to force skip via Harbor
                    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=True) as temp_file:
                        # Generate tiny silence track
                        result = subprocess.run([
                            'ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', 
                            '-t', '0.05', '-acodec', 'mp3', '-y', temp_file.name
                        ], capture_output=True, timeout=5)
                        
                        if result.returncode == 0:
                            # Send to Harbor music input to interrupt stuck DJ intro
                            skip_result = subprocess.run([
                                'curl', '-f', '-X', 'PUT', 'http://127.0.0.1:8001/music',
                                '-H', 'Content-Type: audio/mpeg',
                                '--data-binary', f'@{temp_file.name}'
                            ], capture_output=True, timeout=5)
                            
                            if skip_result.returncode == 0:
                                print("Applied fix: Harbor skip successful to clear stuck DJ intro")
                            else:
                                print(f"Harbor skip failed: {skip_result.stderr}")
                        else:
                            print("Failed to generate skip audio for stuck DJ fix")
                            
                except Exception as e:
                    print(f"Failed to apply Harbor fix: {e}")
        
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
    """Get upcoming tracks from Liquidsoap queue"""
    try:
        # Try Harbor cache first (if it exists)
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
        
        # Get actual queue from Liquidsoap icecast metadata
        print("Getting actual queue from Liquidsoap output.icecast.metadata")
        lines = liquidsoap_command("output.icecast.metadata")
        if not lines:
            print("No response from Liquidsoap metadata command")
            return [
                {"title": "Random Selection", "artist": "Surprise Track Coming Up!", "album": "🎲 Shuffle Mode", "filename": "", "artwork_url": None}
            ]
        
        # Parse metadata sections (--- 1 ---, --- 2 ---, --- 3 ---)
        sections = parse_metadata_sections(lines)
        if not sections:
            print("No metadata sections found")
            return [
                {"title": "Random Selection", "artist": "Surprise Track Coming Up!", "album": "🎲 Shuffle Mode", "filename": "", "artwork_url": None}
            ]
        
        # Get current track to exclude it from next tracks
        current_metadata = get_current_metadata()
        current_track_id = f"{current_metadata.get('artist', '')}|{current_metadata.get('title', '')}" if current_metadata else ""
        
        # Build next tracks list from sections (skip current track)
        next_tracks = []
        
        # Sort sections by their number (1, 2, 3...)
        sorted_sections = sorted([(k, v) for k, v in sections.items() if k > 0])
        
        for section_num, track_data in sorted_sections:
            title = track_data.get('title', 'Unknown Title')
            artist = track_data.get('artist', 'Unknown Artist')
            album = track_data.get('album', '')
            filename = track_data.get('filename', '') or track_data.get('initial_uri', '')
            
            # Clean up filename if it starts with file://
            if filename.startswith("file://"):
                filename = filename[7:]
            
            # Skip DJ intros and the currently playing track
            track_id = f"{artist}|{title}"
            if (artist != "AI DJ" and title != "DJ Intro" and 
                track_id != current_track_id):
                
                # Try to enhance with database information
                enhanced_filename = filename
                enhanced_album = album
                
                if not enhanced_filename:  # Only lookup if we don't have filename
                    try:
                        from database import lookup_track_info
                        db_info = lookup_track_info(artist, title)
                        if db_info:
                            enhanced_filename = db_info.get('filename', '')
                            if not enhanced_album:  # Use DB album if none from metadata
                                enhanced_album = db_info.get('album', '')
                            print(f"Enhanced {artist} - {title} with database info")
                    except Exception as e:
                        print(f"Database lookup failed for {artist} - {title}: {e}")
                
                track = {
                    "title": title,
                    "artist": artist,
                    "album": enhanced_album,
                    "filename": enhanced_filename,
                    "artwork_url": f"/api/cover?file={urllib.parse.quote(enhanced_filename)}" if enhanced_filename else None
                }
                next_tracks.append(track)
                
                # Limit to 5 upcoming tracks
                if len(next_tracks) >= 5:
                    break
        
        if next_tracks:
            print(f"Found {len(next_tracks)} upcoming tracks in Liquidsoap queue")
            return next_tracks
        else:
            print("No music tracks found in queue (only DJ intros), using fallback")
            return [
                {"title": "Random Selection", "artist": "Surprise Track Coming Up!", "album": "🎲 Shuffle Mode", "filename": "", "artwork_url": None}
            ]
        
    except Exception as e:
        print(f"Error getting next tracks: {e}")
        return [
            {"title": "Random Selection", "artist": "Surprise Track Coming Up!", "album": "🎲 Shuffle Mode", "filename": "", "artwork_url": None}
        ]

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