#!/usr/bin/env python3
"""
Initialize SQLite database for AI Radio
"""

import sqlite3
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime

DATABASE_PATH = "/opt/ai-radio/ai_radio.db"
SCHEMA_PATH = "/opt/ai-radio/db_init.sql"
HISTORY_JSON = "/opt/ai-radio/play_history.json"
TTS_DIR = "/opt/ai-radio/tts"

def init_database():
    """Initialize the database with schema"""
    print("Initializing AI Radio database...")
    
    # Create database connection
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Read and execute schema
    with open(SCHEMA_PATH, 'r') as f:
        schema = f.read()
    
    cursor.executescript(schema)
    conn.commit()
    print("✅ Database schema created")
    
    return conn

def extract_timestamp_from_filename(filename):
    """Extract timestamp from TTS filename like custom_1756054435.mp3"""
    match = re.search(r'(\d{10,})', filename)
    return int(match.group(1)) if match else None

def scan_tts_files():
    """Scan TTS directory and create entries for existing files"""
    print("Scanning TTS files...")
    
    tts_entries = []
    
    if not os.path.exists(TTS_DIR):
        print(f"TTS directory {TTS_DIR} not found")
        return tts_entries
    
    # Find all mp3 files
    mp3_files = list(Path(TTS_DIR).glob("*.mp3"))
    
    for mp3_file in mp3_files:
        timestamp = extract_timestamp_from_filename(mp3_file.name)
        if not timestamp:
            continue
            
        # Look for corresponding text file
        txt_file = mp3_file.with_suffix('.txt')
        
        text_content = ""
        if txt_file.exists():
            try:
                with open(txt_file, 'r', encoding='utf-8') as f:
                    text_content = f.read().strip()
            except Exception as e:
                print(f"Warning: Could not read {txt_file}: {e}")
        
        # Determine mode from filename
        mode = 'custom'
        if mp3_file.name.startswith('intro_'):
            mode = 'intro'
        elif mp3_file.name.startswith('outro_'):
            mode = 'outro'
        
        # Get file size
        file_size = mp3_file.stat().st_size if mp3_file.exists() else 0
        
        tts_entries.append({
            'timestamp': timestamp,
            'text': text_content,
            'audio_filename': mp3_file.name,
            'text_filename': txt_file.name,
            'track_title': None,
            'track_artist': None,
            'mode': mode,
            'status': 'active',
            'file_size': file_size
        })
    
    print(f"✅ Found {len(tts_entries)} TTS entries")
    return tts_entries

def migrate_history_data():
    """Migrate existing JSON history data"""
    print("Migrating history data...")
    
    if not os.path.exists(HISTORY_JSON):
        print(f"History file {HISTORY_JSON} not found")
        return []
    
    try:
        with open(HISTORY_JSON, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return []
            
            # Fix JSON format if it's missing brackets
            if not content.startswith('['):
                content = '[' + content
            if not content.endswith(']'):
                content = content + ']'
            
            history_data = json.loads(content)
    except Exception as e:
        print(f"Error reading history JSON: {e}")
        return []
    
    history_entries = []
    
    for item in history_data:
        # Convert timestamps (some might be in different formats)
        timestamp = item.get('time', 0)
        
        # Extract metadata
        metadata = {}
        if 'metadata' in item:
            metadata = item['metadata']
        
        history_entries.append({
            'type': item.get('type', 'song'),
            'timestamp': timestamp,
            'title': item.get('title', ''),
            'artist': item.get('artist', ''),
            'album': item.get('album', ''),
            'filename': item.get('filename', ''),
            'artwork_url': item.get('artwork_url', ''),
            'metadata': json.dumps(metadata) if metadata else None,
            'tts_entry_id': None  # Will be linked later
        })
    
    print(f"✅ Migrated {len(history_entries)} history entries")
    return history_entries

def link_tts_to_history(conn, tts_entries, history_entries):
    """Link TTS entries to history entries based on timestamps and audio URLs"""
    print("Linking TTS entries to history...")
    
    cursor = conn.cursor()
    linked_count = 0
    
    for history_entry in history_entries:
        if history_entry['type'] != 'dj':
            continue
            
        # Look for matching TTS entry by audio URL or timestamp
        audio_url = None
        
        # Try to extract filename from audio URL in the original data
        for item in json.loads(open(HISTORY_JSON, 'r').read()):
            if item.get('time') == history_entry['timestamp'] and item.get('type') == 'dj':
                audio_url = item.get('audio_url', '')
                break
        
        if audio_url and '/tts/' in audio_url:
            # Extract filename from URL like "/tts/custom_1756054435.mp3"
            filename = audio_url.split('/tts/')[-1]
            
            # Find matching TTS entry
            for tts_entry in tts_entries:
                if tts_entry['audio_filename'] == filename:
                    # Get the TTS entry ID from database
                    cursor.execute(
                        "SELECT id FROM tts_entries WHERE timestamp = ?",
                        (tts_entry['timestamp'],)
                    )
                    result = cursor.fetchone()
                    if result:
                        history_entry['tts_entry_id'] = result[0]
                        linked_count += 1
                    break
    
    print(f"✅ Linked {linked_count} TTS entries to history")

def insert_data(conn, tts_entries, history_entries):
    """Insert data into database"""
    cursor = conn.cursor()
    
    # Insert TTS entries
    print("Inserting TTS entries...")
    for entry in tts_entries:
        cursor.execute("""
            INSERT OR IGNORE INTO tts_entries 
            (timestamp, text, audio_filename, text_filename, track_title, track_artist, mode, status, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry['timestamp'],
            entry['text'],
            entry['audio_filename'],
            entry['text_filename'],
            entry['track_title'],
            entry['track_artist'],
            entry['mode'],
            entry['status'],
            entry['file_size']
        ))
    
    conn.commit()
    print(f"✅ Inserted {len(tts_entries)} TTS entries")
    
    # Insert history entries
    print("Inserting history entries...")
    for entry in history_entries:
        cursor.execute("""
            INSERT INTO play_history 
            (type, timestamp, title, artist, album, filename, artwork_url, tts_entry_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry['type'],
            entry['timestamp'],
            entry['title'],
            entry['artist'],
            entry['album'],
            entry['filename'],
            entry['artwork_url'],
            entry['tts_entry_id'],
            entry['metadata']
        ))
    
    conn.commit()
    print(f"✅ Inserted {len(history_entries)} history entries")

def create_backup():
    """Create backup of existing files"""
    print("Creating backups...")
    
    # Backup history JSON
    if os.path.exists(HISTORY_JSON):
        backup_path = f"{HISTORY_JSON}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.rename(HISTORY_JSON, backup_path)
        print(f"✅ Backed up history to {backup_path}")

def main():
    """Main initialization function"""
    print("🚀 AI Radio Database Migration")
    print("=" * 40)
    
    # Initialize database
    conn = init_database()
    
    try:
        # Scan existing TTS files
        tts_entries = scan_tts_files()
        
        # Migrate history data
        history_entries = migrate_history_data()
        
        # Link TTS to history
        if tts_entries and history_entries:
            link_tts_to_history(conn, tts_entries, history_entries)
        
        # Insert all data
        insert_data(conn, tts_entries, history_entries)
        
        # Create backups
        create_backup()
        
        print("\n🎉 Database migration completed successfully!")
        print(f"📊 Database created at: {DATABASE_PATH}")
        print(f"📈 TTS entries: {len(tts_entries)}")
        print(f"📜 History entries: {len(history_entries)}")
        
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()