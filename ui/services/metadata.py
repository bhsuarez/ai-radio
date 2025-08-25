"""
Metadata service for handling current track and history information
"""
import json
import socket
import time
import os
from pathlib import Path
from contextlib import closing
from typing import Dict, List, Optional

from config import config
from utils.file import safe_json_read, locked_json_read
from utils.text import parse_kv_text

class MetadataService:
    """Service for retrieving and managing track metadata"""
    
    def __init__(self):
        self._last_telnet_time = 0
        self._telnet_cache = {}
        self._telnet_cache_ttl = 5  # Cache for 5 seconds
    
    def get_current_track(self) -> Dict:
        """
        Get current track metadata from multiple sources with fallback chain.
        
        Priority: JSON file -> text file -> telnet -> defaults
        
        Returns:
            Dictionary with track metadata
        """
        data = {}
        
        # Primary source: JSON metadata file
        if config.NOW_JSON.exists():
            json_data = safe_json_read(config.NOW_JSON, {})
            if isinstance(json_data, dict):
                data.update(json_data)
        
        # Fallback 1: Text file (key=value or "Artist - Title")
        if config.NOW_TXT.exists() and not (data.get("title") and data.get("artist")):
            data.update(self._read_text_metadata())
        
        # Fallback 2: Liquidsoap telnet (cached)
        if not data.get("title"):
            telnet_data = self._get_telnet_metadata()
            data.update(telnet_data)
        
        # Apply defaults
        data.setdefault("title", "Unknown title")
        data.setdefault("artist", "Unknown artist") 
        data.setdefault("album", "")
        data.setdefault("artwork_url", "")
        data.setdefault("filename", "")
        
        # Add frontend compatibility fields
        data.setdefault("type", "song")
        data.setdefault("timestamp", int(time.time()))
        
        # Add track timing if available (from started_at field)
        if "started_at" in data:
            try:
                data["track_started_at"] = float(data["started_at"])
            except (ValueError, TypeError):
                pass
        
        # Try to get actual duration from audio file
        if not data.get("duration") and data.get("title") and data.get("artist"):
            duration = self._get_track_duration(data["title"], data["artist"], data.get("album"))
            if duration:
                data["duration"] = duration
        
        return data
    
    def get_next_track(self) -> Optional[Dict]:
        """
        Get next track information if available.
        
        Returns:
            Dictionary with next track info or None
        """
        return safe_json_read(config.NEXT_JSON)
    
    def _read_text_metadata(self) -> Dict:
        """Read metadata from text file (key=value or Artist - Title format)"""
        try:
            with open(config.NOW_TXT, 'r', encoding='utf-8') as f:
                raw = f.read().strip()
            
            data = {}
            
            # Try key=value format first
            if "=" in raw:
                kv_data = parse_kv_text(raw)
                for key in ("title", "artist", "album", "artwork_url", "started_at", "duration", "filename"):
                    if key in kv_data:
                        data[key] = kv_data[key]
            
            # Try "Artist - Title" format
            elif " - " in raw and "\n" not in raw:
                parts = raw.split(" - ", 1)
                if len(parts) == 2:
                    data["artist"] = parts[0].strip()
                    data["title"] = parts[1].strip()
            
            return data
            
        except (OSError, UnicodeDecodeError):
            return {}
    
    def _get_telnet_metadata(self) -> Dict:
        """Get metadata from Liquidsoap telnet with caching"""
        current_time = time.time()
        
        # Return cached data if still valid
        if current_time - self._last_telnet_time < self._telnet_cache_ttl:
            return self._telnet_cache.copy()
        
        try:
            lines = self._liquidsoap_command("output.icecast.metadata")
            raw = '\n'.join(lines)
            
            # Parse telnet response for current track (section 1)
            data = self._parse_telnet_metadata(raw)
            
            # Update cache
            self._telnet_cache = data
            self._last_telnet_time = current_time
            
            return data
            
        except Exception:
            # Return cached data on error, or empty dict
            return self._telnet_cache.copy()
    
    def _parse_telnet_metadata(self, raw: str) -> Dict:
        """Parse Liquidsoap telnet metadata response"""
        lines = raw.split('\n')
        current_track = {}
        in_current_section = False
        
        for line in lines:
            line = line.strip()
            
            # Look for current track section (--- 1 ---)
            if line == "--- 1 ---":
                in_current_section = True
                continue
            elif line.startswith("--- ") and line.endswith(" ---"):
                in_current_section = False
                continue
            
            # Parse metadata in current section
            if in_current_section and "=" in line:
                try:
                    key, value = line.split("=", 1)
                    key = key.strip().strip('"')
                    value = value.strip().strip('"')
                    current_track[key] = value
                except ValueError:
                    continue
        
        return current_track
    
    def _liquidsoap_command(self, command: str) -> List[str]:
        """Execute Liquidsoap telnet command"""
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                sock.settimeout(2)
                sock.connect((config.TELNET_HOST, config.TELNET_PORT))
                
                # Send command
                sock.sendall(f"{command}\n".encode())
                
                # Read response
                response = b""
                while True:
                    try:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk
                        if b"END" in response:
                            break
                    except socket.timeout:
                        break
                
                # Process response
                lines = response.decode('utf-8', errors='ignore').splitlines()
                # Remove "END" marker if present
                return [line for line in lines if line.strip() != "END"]
                
        except (socket.error, socket.timeout):
            return []
    
    def _get_track_duration(self, title: str, artist: str, album: str = None) -> Optional[float]:
        """
        Find and extract duration from audio file by searching for track metadata.
        
        Args:
            title: Track title
            artist: Artist name
            album: Album name (optional)
            
        Returns:
            Duration in seconds or None if not found
        """
        try:
            from mutagen import File as MutaFile
        except ImportError:
            return None
        
        # Common music directories to search
        music_dirs = ["/mnt/music/Music", "/mnt/music/media"]
        
        # Generate possible filename patterns
        patterns = []
        
        # Sanitize strings for filename matching
        clean_title = self._sanitize_for_search(title)
        clean_artist = self._sanitize_for_search(artist)
        clean_album = self._sanitize_for_search(album) if album else ""
        
        # Try different search patterns
        if album:
            patterns.extend([
                f"*{clean_artist}*{clean_album}*{clean_title}*",
                f"*{clean_artist}*{clean_album}*",
                f"*{clean_album}*{clean_title}*",
            ])
        
        patterns.extend([
            f"*{clean_artist}*{clean_title}*",
            f"*{clean_title}*{clean_artist}*",
            f"*{clean_title}*",
        ])
        
        # Search in music directories
        for music_dir in music_dirs:
            if not os.path.exists(music_dir):
                continue
                
            for pattern in patterns:
                try:
                    # Use find command for efficient searching - search for audio files first, then filter by name
                    import subprocess
                    
                    # First find all audio files in the directory
                    cmd = ["find", music_dir, "-type", "f", 
                          "(", "-iname", "*.mp3", "-o", "-iname", "*.m4a", "-o", 
                          "-iname", "*.flac", "-o", "-iname", "*.wav", ")",
                          "-iname", pattern]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and result.stdout.strip():
                        files = result.stdout.strip().split('\n')
                        
                        # Try to extract duration from first matching file
                        for file_path in files[:3]:  # Limit to first 3 matches
                            file_path = file_path.strip()
                            if file_path and os.path.exists(file_path):
                                try:
                                    audio = MutaFile(file_path)
                                    if audio and audio.info and hasattr(audio.info, 'length'):
                                        duration = float(audio.info.length)
                                        if duration > 0:
                                            print(f"Found duration {duration:.1f}s for '{title}' in: {file_path}")
                                            return duration
                                except Exception as e:
                                    print(f"Error reading {file_path}: {e}")
                                    continue
                                    
                except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
                    print(f"Find command error: {e}")
                    continue
        
        return None
    
    def _sanitize_for_search(self, text: str) -> str:
        """Sanitize text for file search patterns"""
        if not text:
            return ""
        
        # Remove special characters that could interfere with shell patterns
        import re
        # Keep only alphanumeric, spaces, and basic punctuation
        sanitized = re.sub(r'[^\w\s\-\.]', '', text)
        # Replace multiple spaces with single space
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        return sanitized