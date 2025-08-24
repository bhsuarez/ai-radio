"""
Metadata service for handling current track and history information
"""
import json
import socket
import time
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