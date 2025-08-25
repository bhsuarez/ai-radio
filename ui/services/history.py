"""
History service for managing play history using SQLite database
"""
import sys
import os
import threading
import time
from collections import deque
from typing import Dict, List

# Add parent directory to path for database import
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from database import db_manager

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
        event_type = track_data.get("type", "song")
        event = {
            "type": event_type,
            "time": int(track_data.get("time", time.time() * 1000)),
        }
        
        if event_type == "dj":
            # DJ commentary event
            event.update({
                "text": track_data.get("text", "").strip(),
                "audio_url": track_data.get("audio_url", "").strip(),
            })
        else:
            # Song event
            event.update({
                "title": track_data.get("title", "").strip(),
                "artist": track_data.get("artist", "").strip(),
                "album": track_data.get("album", "").strip(),
                "filename": track_data.get("filename", "").strip(),
                "artwork_url": track_data.get("artwork_url", "").strip(),
            })
        
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
        try:
            with self._history_lock:
                self._history.clear()
            
            # Clear database
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM play_history")
                conn.commit()
            
            return True
        except Exception as e:
            print(f"Error clearing history: {e}")
            return False
    
    def _generate_event_key(self, event: Dict) -> str:
        """
        Generate stable key for event deduplication.
        
        For songs: Priority: filename > title|artist|album
        For DJ: Use text + time (since DJ can repeat same text)
        """
        if event.get("type") == "dj":
            text = event.get("text", "").strip().lower()
            time = event.get("time", 0)
            return f"dj|{text}|{time}"
        
        filename = event.get("filename", "").strip()
        if filename:
            return f"f|{filename}"
        
        title = event.get("title", "").strip().lower()
        artist = event.get("artist", "").strip().lower() 
        album = event.get("album", "").strip().lower()
        
        return f"t|{title}|{artist}|{album}"
    
    def _load_history(self):
        """Load history from database on startup"""
        try:
            # Get recent history from database
            history_data = db_manager.get_history(limit=config.MAX_HISTORY)
            
            with self._history_lock:
                self._history.clear()
                # Convert database entries to the format expected by the service
                for entry in reversed(history_data):  # Database returns newest first, we want oldest first in deque
                    event = {
                        "type": entry.get("type", "song"),
                        "time": entry.get("timestamp", 0),
                        "title": entry.get("title", ""),
                        "artist": entry.get("artist", ""),
                        "album": entry.get("album", ""),
                        "filename": entry.get("filename", ""),
                        "artwork_url": entry.get("artwork_url", ""),
                    }
                    if event["type"] == "dj":
                        event["text"] = entry.get("text", "")
                        event["audio_url"] = entry.get("audio_url", "")
                    
                    self._history.append(event)
        except Exception as e:
            print(f"Error loading history from database: {e}")
            # Fallback to empty history
            with self._history_lock:
                self._history.clear()
    
    def _save_to_disk(self, event: Dict):
        """Save single event to database"""
        try:
            # For DJ events, store text/audio_url in metadata field
            metadata = None
            if event.get("type") == "dj":
                metadata = {
                    "text": event.get("text", ""),
                    "audio_url": event.get("audio_url", "")
                }
            
            db_manager.add_history_entry(
                entry_type=event.get("type", "song"),
                timestamp=event.get("time", int(time.time() * 1000)),
                title=event.get("title", ""),
                artist=event.get("artist", ""),
                album=event.get("album", ""),
                filename=event.get("filename", ""),
                artwork_url=event.get("artwork_url", ""),
                metadata=metadata
            )
        except Exception as e:
            print(f"Error saving event to database: {e}")