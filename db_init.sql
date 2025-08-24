-- AI Radio Database Schema
-- SQLite database for managing TTS entries and play history

-- TTS entries table with proper relationships
CREATE TABLE IF NOT EXISTS tts_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL UNIQUE,
    text TEXT NOT NULL,
    audio_filename TEXT NOT NULL,
    text_filename TEXT NOT NULL,
    track_title TEXT,
    track_artist TEXT,
    mode TEXT DEFAULT 'custom', -- custom, intro, outro
    status TEXT DEFAULT 'active', -- active, deleted, failed
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_size INTEGER,
    audio_duration REAL
);

-- Play history table (replaces JSON file)
CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('song', 'dj')),
    timestamp INTEGER NOT NULL,
    title TEXT,
    artist TEXT,
    album TEXT,
    filename TEXT,
    artwork_url TEXT,
    tts_entry_id INTEGER,
    metadata TEXT, -- JSON for additional metadata
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tts_entry_id) REFERENCES tts_entries(id) ON DELETE SET NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_tts_timestamp ON tts_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_tts_status ON tts_entries(status);
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON play_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_history_type ON play_history(type);
CREATE INDEX IF NOT EXISTS idx_history_tts ON play_history(tts_entry_id);

-- Artwork cache table (to replace file-based cache eventually)
CREATE TABLE IF NOT EXISTS artwork_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT UNIQUE NOT NULL, -- hash of artist+album or filename
    artist TEXT,
    album TEXT,
    filename TEXT,
    artwork_url TEXT,
    local_path TEXT,
    file_size INTEGER,
    cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_accessed DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'failed', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_artwork_cache_key ON artwork_cache(cache_key);
CREATE INDEX IF NOT EXISTS idx_artwork_accessed ON artwork_cache(last_accessed);