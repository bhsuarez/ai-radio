#!/usr/bin/env python3
"""
Fix TTS linking between history and TTS entries
"""

import json
import re
from database import db_manager

def fix_tts_linking():
    """Link existing TTS entries to history entries"""
    print("Fixing TTS linking...")
    
    # Read the original JSON to get audio URLs
    try:
        with open('/opt/ai-radio/play_history.json.backup.20250824_120129', 'r') as f:
            original_history = json.load(f)
    except Exception as e:
        print(f"Could not read original history: {e}")
        return
    
    linked_count = 0
    
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        
        # Get all DJ history entries that need linking
        cursor.execute("SELECT id, timestamp FROM play_history WHERE type = 'dj' AND tts_entry_id IS NULL")
        dj_entries = cursor.fetchall()
        
        for dj_entry in dj_entries:
            history_id, history_timestamp = dj_entry
            
            # Find matching entry in original JSON
            matching_item = None
            for item in original_history:
                if item.get('type') == 'dj' and abs(item.get('time', 0) - history_timestamp) < 1000:  # Within 1 second
                    matching_item = item
                    break
            
            if matching_item and matching_item.get('audio_url'):
                audio_url = matching_item['audio_url']
                if '/tts/' in audio_url:
                    # Extract filename
                    filename = audio_url.split('/tts/')[-1]
                    
                    # Find matching TTS entry
                    cursor.execute("SELECT id, text FROM tts_entries WHERE audio_filename = ?", (filename,))
                    tts_result = cursor.fetchone()
                    
                    if tts_result:
                        tts_id, tts_text = tts_result
                        
                        # Update history entry
                        cursor.execute("""
                            UPDATE play_history 
                            SET tts_entry_id = ?, title = ? 
                            WHERE id = ?
                        """, (tts_id, matching_item.get('text', ''), history_id))
                        
                        linked_count += 1
                        print(f"Linked history ID {history_id} to TTS ID {tts_id}: {tts_text[:50]}...")
        
        conn.commit()
    
    print(f"✅ Successfully linked {linked_count} TTS entries to history")

if __name__ == "__main__":
    fix_tts_linking()