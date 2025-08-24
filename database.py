#!/usr/bin/env python3
"""
Database helper module for AI Radio
Provides SQLite database operations for TTS entries and play history
"""

import sqlite3
import json
import os
import time
from contextlib import contextmanager
from threading import Lock
from typing import List, Dict, Optional, Any

DATABASE_PATH = "/opt/ai-radio/ai_radio.db"

# Thread-safe database operations
_db_lock = Lock()

class DatabaseManager:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
    
    @contextmanager
    def get_connection(self):
        """Thread-safe database connection context manager"""
        with _db_lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Enable dict-like access
            try:
                yield conn
            finally:
                conn.close()
    
    def create_tts_entry(self, timestamp: int, text: str, audio_filename: str, 
                        text_filename: str, track_title: str = None, 
                        track_artist: str = None, mode: str = 'custom') -> int:
        """Create a new TTS entry and return its ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tts_entries 
                (timestamp, text, audio_filename, text_filename, track_title, track_artist, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, text, audio_filename, text_filename, track_title, track_artist, mode))
            
            conn.commit()
            return cursor.lastrowid
    
    def get_tts_entry_by_timestamp(self, timestamp: int) -> Optional[Dict]:
        """Get TTS entry by timestamp"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tts_entries WHERE timestamp = ?", (timestamp,))
            result = cursor.fetchone()
            return dict(result) if result else None
    
    def get_tts_entry_by_filename(self, filename: str) -> Optional[Dict]:
        """Get TTS entry by audio filename"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tts_entries WHERE audio_filename = ?", (filename,))
            result = cursor.fetchone()
            return dict(result) if result else None
    
    def add_history_entry(self, entry_type: str, timestamp: int, title: str = "", 
                         artist: str = "", album: str = "", filename: str = "", 
                         artwork_url: str = "", tts_entry_id: int = None, 
                         metadata: Dict = None) -> int:
        """Add a new history entry"""
        metadata_json = json.dumps(metadata) if metadata else None
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO play_history 
                (type, timestamp, title, artist, album, filename, artwork_url, tts_entry_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (entry_type, timestamp, title, artist, album, filename, artwork_url, tts_entry_id, metadata_json))
            
            conn.commit()
            return cursor.lastrowid
    
    def get_history(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get play history with optional pagination"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT h.*, t.text as tts_text, t.audio_filename as tts_audio
                FROM play_history h
                LEFT JOIN tts_entries t ON h.tts_entry_id = t.id
                ORDER BY h.timestamp DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                
                # Parse metadata JSON
                if row_dict.get('metadata'):
                    try:
                        row_dict['metadata'] = json.loads(row_dict['metadata'])
                    except:
                        row_dict['metadata'] = {}
                
                # For DJ entries, ensure we have the right text and audio URL
                if row_dict['type'] == 'dj' and row_dict.get('tts_text'):
                    row_dict['text'] = row_dict['tts_text']
                    if row_dict.get('tts_audio'):
                        row_dict['audio_url'] = f"/tts/{row_dict['tts_audio']}"
                
                # Clean up internal fields
                if 'tts_text' in row_dict:
                    del row_dict['tts_text']
                if 'tts_audio' in row_dict:
                    del row_dict['tts_audio']
                
                results.append(row_dict)
            
            return results
    
    def get_recent_tts_entries(self, limit: int = 50) -> List[Dict]:
        """Get recent TTS entries"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tts_entries 
                WHERE status = 'active'
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def link_tts_to_history(self, history_timestamp: int, tts_filename: str) -> bool:
        """Link a TTS entry to a history entry"""
        try:
            # Get TTS entry ID
            tts_entry = self.get_tts_entry_by_filename(tts_filename)
            if not tts_entry:
                return False
            
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE play_history 
                    SET tts_entry_id = ?
                    WHERE timestamp = ? AND type = 'dj'
                """, (tts_entry['id'], history_timestamp))
                
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Error linking TTS to history: {e}")
            return False
    
    def cleanup_orphaned_files(self, tts_directory: str) -> List[str]:
        """Find TTS files that are not in the database"""
        import os
        import glob
        
        # Get all MP3 files in TTS directory
        mp3_files = glob.glob(os.path.join(tts_directory, "*.mp3"))
        mp3_filenames = [os.path.basename(f) for f in mp3_files]
        
        # Get all TTS entries from database
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT audio_filename FROM tts_entries WHERE status = 'active'")
            db_filenames = set(row[0] for row in cursor.fetchall())
        
        # Find orphaned files
        orphaned_files = [f for f in mp3_filenames if f not in db_filenames]
        return orphaned_files
    
    def mark_tts_deleted(self, timestamp: int):
        """Mark a TTS entry as deleted instead of physically removing it"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tts_entries 
                SET status = 'deleted' 
                WHERE timestamp = ?
            """, (timestamp,))
            conn.commit()
    
    def lookup_track_info(self, artist: str, title: str) -> Optional[Dict]:
        """Look up track information by artist and title from history"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT filename, album, timestamp 
                FROM play_history 
                WHERE LOWER(artist) = LOWER(?) 
                AND LOWER(title) = LOWER(?) 
                AND type = 'song'
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (artist, title))
            result = cursor.fetchone()
            
            if result:
                return {
                    'filename': result[0],
                    'album': result[1],
                    'last_played': result[2]
                }
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get TTS stats
            cursor.execute("SELECT COUNT(*) FROM tts_entries WHERE status = 'active'")
            active_tts = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM tts_entries")
            total_tts = cursor.fetchone()[0]
            
            # Get history stats
            cursor.execute("SELECT COUNT(*) FROM play_history WHERE type = 'song'")
            song_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM play_history WHERE type = 'dj'")
            dj_count = cursor.fetchone()[0]
            
            # Get recent activity
            cursor.execute("""
                SELECT COUNT(*) FROM play_history 
                WHERE timestamp > ? 
            """, (int((time.time() - 86400) * 1000),))  # Last 24 hours
            recent_activity = cursor.fetchone()[0]
            
            return {
                'active_tts_entries': active_tts,
                'total_tts_entries': total_tts,
                'song_history_count': song_count,
                'dj_history_count': dj_count,
                'recent_activity_24h': recent_activity,
                'database_path': self.db_path
            }

# Global instance
db_manager = DatabaseManager()

# Helper functions for backward compatibility
def create_tts_entry(timestamp: int, text: str, audio_filename: str, text_filename: str, 
                    track_title: str = None, track_artist: str = None, mode: str = 'custom') -> int:
    return db_manager.create_tts_entry(timestamp, text, audio_filename, text_filename, 
                                      track_title, track_artist, mode)

def add_history_entry(entry_type: str, timestamp: int, **kwargs) -> int:
    return db_manager.add_history_entry(entry_type, timestamp, **kwargs)

def get_history(limit: int = 100, offset: int = 0) -> List[Dict]:
    return db_manager.get_history(limit, offset)

def get_tts_entry_by_filename(filename: str) -> Optional[Dict]:
    return db_manager.get_tts_entry_by_filename(filename)

def lookup_track_info(artist: str, title: str) -> Optional[Dict]:
    return db_manager.lookup_track_info(artist, title)