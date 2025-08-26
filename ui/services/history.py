"""
History service for managing play history
"""
import sys
sys.path.append('/opt/ai-radio')  # Add parent directory to path

import time
from typing import Dict, List

from config import config
from database import add_history_entry, get_history as get_db_history

class HistoryService:
    """Service for managing track play history"""
    
    def __init__(self):
        self._last_event_key = None
        self._last_event_time = 0
    
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
            "type": track_data.get("type", "song"),  # Preserve original type (song/dj)
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
        
        # Save to database immediately
        success = self._save_to_database(event)
        if not success:
            return False
        
        # Update deduplication tracking
        self._last_event_key = event_key
        self._last_event_time = current_time
        
        
        return True
    
    def get_history(self, limit: int = None) -> List[Dict]:
        """
        Get recent play history.
        
        Args:
            limit: Maximum number of entries to return
            
        Returns:
            List of track events, most recent first
        """
        try:
            # Get from database
            db_history = get_db_history(limit=limit or 100)
            
            # Convert database format to expected format
            history_list = []
            for row in db_history:
                item = {
                    "type": row.get("type", "song"),
                    "time": row.get("timestamp", 0),
                    "title": row.get("title", ""),
                    "artist": row.get("artist", ""),
                    "album": row.get("album", ""),
                    "filename": row.get("filename", ""),
                    "artwork_url": row.get("artwork_url", ""),
                    "audio_url": row.get("audio_url", ""),
                    "text": row.get("text", ""),  # Include transcript text for DJ entries
                    "created_at": row.get("created_at", "")
                }
                
                # For DJ entries without text, try to read from txt file
                if item["type"] == "dj" and not item["text"]:
                    filename = row.get("filename", "")
                    if filename and filename.endswith('.mp3'):
                        txt_file = filename.replace('.mp3', '.txt')
                        try:
                            if __import__('os').path.exists(txt_file):
                                with open(txt_file, 'r', encoding='utf-8') as f:
                                    content = f.read().strip()
                                    if content:
                                        item["text"] = content.strip('"\'').strip()
                        except Exception as e:
                            print(f"Error reading txt file {txt_file}: {e}")
                
                history_list.append(item)
            
            return history_list
            
        except Exception as e:
            print(f"Error: Failed to get history from database: {e}")
            return []
    
    def clear_history(self) -> bool:
        """
        Clear all history.
        
        Returns:
            True if successful
        """
        try:
            from database import DatabaseManager
            db_manager = DatabaseManager()
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
        
        Priority: filename > title|artist|album
        """
        filename = event.get("filename", "").strip()
        if filename:
            return f"f|{filename}"
        
        title = event.get("title", "").strip().lower()
        artist = event.get("artist", "").strip().lower() 
        album = event.get("album", "").strip().lower()
        
        return f"t|{title}|{artist}|{album}"
    
    def _save_to_database(self, event: Dict) -> bool:
        """Save single event to database"""
        try:
            add_history_entry(
                entry_type=event.get("type", "song"),
                timestamp=event.get("time", int(time.time() * 1000)),
                title=event.get("title", ""),
                artist=event.get("artist", ""),
                album=event.get("album", ""),
                filename=event.get("filename", ""),
                artwork_url=event.get("artwork_url", "")
            )
            return True
        except Exception as e:
            print(f"Error: Failed to save event to database: {e}")
            return False