"""
TTS (Text-to-Speech) service for managing DJ commentary generation
"""
import os
import threading
import time
import subprocess
from pathlib import Path
from typing import Optional, List

from config import config

class TTSService:
    """Service for managing TTS operations and DJ commentary"""
    
    def __init__(self):
        self._generation_lock = threading.Lock()
        self._last_generation_time = 0
        self._last_track_key = ""
    
    def can_generate_dj_intro(self, track_data: dict) -> bool:
        """
        Check if we can generate a DJ intro for this track.
        
        Args:
            track_data: Current track metadata
            
        Returns:
            True if generation is allowed, False if throttled
        """
        current_time = time.time()
        track_key = self._get_track_key(track_data)
        
        # Throttle based on time and track uniqueness
        time_ok = current_time - self._last_generation_time >= config.DJ_GENERATION_COOLDOWN
        track_different = track_key != self._last_track_key
        
        # Allow generation if track is different OR enough time has passed
        return track_different or time_ok
    
    def generate_dj_intro(self, track_data: dict) -> bool:
        """
        Generate DJ intro for current track.
        
        Args:
            track_data: Current track metadata
            
        Returns:
            True if generation was triggered, False otherwise
        """
        if not self.can_generate_dj_intro(track_data):
            return False
        
        with self._generation_lock:
            # Double-check after acquiring lock
            if not self.can_generate_dj_intro(track_data):
                return False
            
            success = self._trigger_dj_generation(track_data)
            
            if success:
                self._last_generation_time = time.time()
                self._last_track_key = self._get_track_key(track_data)
            
            return success
    
    def get_tts_queue_status(self) -> dict:
        """
        Get current TTS queue status.
        
        Returns:
            Dictionary with queue information
        """
        tts_dir = config.tts_root
        
        try:
            # Count files in TTS directory
            if tts_dir.exists():
                tts_files = list(tts_dir.glob("*.mp3"))
                queue_size = len(tts_files)
                
                # Get most recent file for status
                if tts_files:
                    most_recent = max(tts_files, key=lambda f: f.stat().st_mtime)
                    return {
                        "queue_size": queue_size,
                        "latest_file": most_recent.name,
                        "latest_time": most_recent.stat().st_mtime
                    }
            
            return {"queue_size": 0, "latest_file": None, "latest_time": None}
            
        except (OSError, ValueError):
            return {"queue_size": 0, "latest_file": None, "latest_time": None, "error": True}
    
    def list_tts_files(self, limit: int = 10) -> List[dict]:
        """
        List recent TTS files.
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List of file information dictionaries
        """
        tts_dir = config.tts_root
        files = []
        
        try:
            if tts_dir.exists():
                tts_files = list(tts_dir.glob("*.mp3"))
                # Sort by modification time, most recent first
                tts_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                
                for tts_file in tts_files[:limit]:
                    stat = tts_file.stat()
                    files.append({
                        "filename": tts_file.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "path": str(tts_file)
                    })
        
        except OSError:
            pass
        
        return files
    
    def _get_track_key(self, track_data: dict) -> str:
        """Generate unique key for track to prevent duplicate generations"""
        title = track_data.get("title", "").strip()
        artist = track_data.get("artist", "").strip()
        filename = track_data.get("filename", "").strip()
        
        if filename:
            return f"file:{filename}"
        else:
            return f"track:{artist}|{title}"
    
    def _trigger_dj_generation(self, track_data: dict) -> bool:
        """
        Trigger external DJ generation script and TTS processing.
        
        Args:
            track_data: Track metadata for generation
            
        Returns:
            True if script was triggered successfully
        """
        try:
            title = track_data.get("title", "Unknown")
            artist = track_data.get("artist", "Unknown")
            
            # Use the basic TTS generation script with AI text generation
            script_path = "/opt/ai-radio/dj_enqueue_xtts.sh"
            
            if not os.path.exists(script_path):
                return False
            
            # Execute script in background (this handles both AI generation and TTS)
            # dj_enqueue_xtts.sh expects: artist, title, language, [speaker], [mode]
            cmd = [script_path, artist, title, "en"]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd="/opt/ai-radio"
            )
            
            return True
            
        except (OSError, subprocess.SubprocessError):
            return False