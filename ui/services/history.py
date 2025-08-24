"""
History service for managing play history
"""
import threading
import time
from collections import deque
from typing import Dict, List

from config import config
from utils.file import safe_json_read, locked_json_append, atomic_write

class HistoryService:
    """Service for managing track play history"""
    
    def __init__(self):
        self._history = deque(maxlen=config.MAX_HISTORY)
        self._history_lock = threading.Lock()
        self._last_event_key = None
        self._last_event_time = 0
        
        # Load existing history on startup
        self._load_history()
    
    def add_track(self, track_data: Dict) -> bool:
        """
        Add a track to play history with deduplication.
        
        Args:
            track_data: Track metadata dictionary
            
        Returns:
            True if track was added, False if duplicate
        """
        # Create standardized event
        event = {
            "type": "song",
            "time": int(track_data.get("time", time.time() * 1000)),
            "title": track_data.get("title", "").strip(),
            "artist": track_data.get("artist", "").strip(),
            "album": track_data.get("album", "").strip(),
            "filename": track_data.get("filename", "").strip(),
            "artwork_url": track_data.get("artwork_url", "").strip(),
        }
        
        # Generate deduplication key
        event_key = self._generate_event_key(event)
        current_time = time.time() * 1000
        
        # Skip if duplicate within time window
        if (event_key == self._last_event_key and 
            current_time - self._last_event_time < config.DEDUP_WINDOW_MS):
            return False
        
        # Add to history
        with self._history_lock:
            self._history.append(event)
        
        # Update deduplication tracking
        self._last_event_key = event_key
        self._last_event_time = current_time
        
        # Persist to disk
        self._save_to_disk(event)
        
        return True
    
    def get_history(self, limit: int = None) -> List[Dict]:
        """
        Get recent play history.
        
        Args:
            limit: Maximum number of entries to return
            
        Returns:
            List of track events, most recent first
        """
        with self._history_lock:
            history_list = list(self._history)
        
        # Return most recent first
        history_list.reverse()
        
        if limit:
            history_list = history_list[:limit]
        
        return history_list
    
    def clear_history(self) -> bool:
        """
        Clear all history.
        
        Returns:
            True if successful
        """
        with self._history_lock:
            self._history.clear()
        
        # Clear disk storage
        return atomic_write(config.HISTORY_FILE, [])
    
    def _generate_event_key(self, event: Dict) -> str:
        """
        Generate stable key for event deduplication.
        
        Priority: filename > title|artist|album
        """
        filename = event.get("filename", "").strip()
        if filename:
            return f"f|{filename}"
        
        title = event.get("title", "").strip().lower()
        artist = event.get("artist", "").strip().lower() 
        album = event.get("album", "").strip().lower()
        
        return f"t|{title}|{artist}|{album}"
    
    def _load_history(self):
        """Load history from disk on startup"""
        history_data = safe_json_read(config.HISTORY_FILE, [])
        
        if isinstance(history_data, list):
            with self._history_lock:
                self._history.clear()
                # Only keep recent entries within our limit
                recent_entries = history_data[-config.MAX_HISTORY:]
                self._history.extend(recent_entries)
    
    def _save_to_disk(self, event: Dict):
        """Save single event to persistent history file"""
        # Use atomic append with file locking
        locked_json_append(config.HISTORY_FILE, event, max_entries=500)