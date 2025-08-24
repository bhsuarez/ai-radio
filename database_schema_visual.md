# AI Radio Database Schema Visualization

## Overview
SQLite database schema for AI Radio system managing TTS entries, play history, and artwork caching.

## Tables Structure

### 🎤 TTS_ENTRIES Table
**Purpose**: Stores AI DJ text-to-speech entries with audio file relationships

```
┌─────────────────────────────────────────────────────────────────┐
│                        TTS_ENTRIES                              │
├─────────────────────────────────────────────────────────────────┤
│ 🔑 id (PK)                INTEGER AUTO INCREMENT               │
│ ⏰ timestamp               INTEGER NOT NULL UNIQUE              │
│ 💬 text                   TEXT NOT NULL                         │
│ 🎵 audio_filename         TEXT NOT NULL                         │
│ 📄 text_filename          TEXT NOT NULL                         │
│ 🎵 track_title            TEXT                                  │
│ 🎤 track_artist           TEXT                                  │
│ 🎯 mode                   TEXT DEFAULT 'custom'                │
│ 📊 status                 TEXT DEFAULT 'active'                │
│ 📅 created_at             DATETIME DEFAULT CURRENT_TIMESTAMP   │
│ 📏 file_size              INTEGER                               │
│ ⏱️  audio_duration         REAL                                  │
└─────────────────────────────────────────────────────────────────┘

Constraints:
• mode: 'custom', 'intro', 'outro'
• status: 'active', 'deleted', 'failed'
• timestamp: UNIQUE (prevents duplicates)
```

### 📚 PLAY_HISTORY Table
**Purpose**: Tracks all played content (music + DJ commentary) with TTS relationships

```
┌─────────────────────────────────────────────────────────────────┐
│                       PLAY_HISTORY                              │
├─────────────────────────────────────────────────────────────────┤
│ 🔑 id (PK)                INTEGER AUTO INCREMENT               │
│ 🏷️  type                   TEXT NOT NULL                        │
│ ⏰ timestamp               INTEGER NOT NULL                      │
│ 🎵 title                   TEXT                                  │
│ 🎤 artist                  TEXT                                  │
│ 💿 album                   TEXT                                  │
│ 📁 filename                TEXT                                  │
│ 🖼️  artwork_url            TEXT                                  │
│ 🔗 tts_entry_id           INTEGER (FK)                          │
│ 📋 metadata                TEXT (JSON)                          │
│ 📅 created_at             DATETIME DEFAULT CURRENT_TIMESTAMP   │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    │ FOREIGN KEY
                                    ▼
                        ┌─────────────────────┐
                        │    TTS_ENTRIES      │
                        │        (id)         │
                        └─────────────────────┘

Constraints:
• type: CHECK IN ('song', 'dj')
• tts_entry_id: FOREIGN KEY → tts_entries(id) ON DELETE SET NULL
```

### 🖼️ ARTWORK_CACHE Table
**Purpose**: Caches album artwork and cover images (future enhancement)

```
┌─────────────────────────────────────────────────────────────────┐
│                      ARTWORK_CACHE                              │
├─────────────────────────────────────────────────────────────────┤
│ 🔑 id (PK)                INTEGER AUTO INCREMENT               │
│ 🔗 cache_key              TEXT UNIQUE NOT NULL                  │
│ 🎤 artist                  TEXT                                  │
│ 💿 album                   TEXT                                  │
│ 📁 filename                TEXT                                  │
│ 🌐 artwork_url            TEXT                                  │
│ 📂 local_path             TEXT                                  │
│ 📏 file_size              INTEGER                               │
│ 📅 cached_at              DATETIME DEFAULT CURRENT_TIMESTAMP   │
│ 👁️  last_accessed          DATETIME DEFAULT CURRENT_TIMESTAMP   │
│ 📊 status                 TEXT DEFAULT 'active'                │
└─────────────────────────────────────────────────────────────────┘

Constraints:
• cache_key: UNIQUE (hash of artist+album or filename)
• status: CHECK IN ('active', 'failed', 'expired')
```

## 📊 Indexes for Performance

```
🔍 TTS_ENTRIES Indexes:
├── idx_tts_timestamp (timestamp) - Fast chronological lookups
├── idx_tts_status (status) - Filter by active/deleted
└── Primary Key (id) - Auto-indexed

🔍 PLAY_HISTORY Indexes:
├── idx_history_timestamp (timestamp) - Recent history queries
├── idx_history_type (type) - Separate song/dj queries
├── idx_history_tts (tts_entry_id) - Foreign key lookups
└── Primary Key (id) - Auto-indexed

🔍 ARTWORK_CACHE Indexes:
├── idx_artwork_cache_key (cache_key) - Fast cache lookups
├── idx_artwork_accessed (last_accessed) - Cache cleanup
└── Primary Key (id) - Auto-indexed
```

## 🔗 Relationships

```
TTS_ENTRIES (1) ←──────── (0..1) PLAY_HISTORY
      │                           │
      │ One TTS entry can be      │ Each history entry
      │ linked to zero or one     │ can optionally link
      │ history entries           │ to one TTS entry
      │                           │
      └─── ON DELETE SET NULL ────┘
           (If TTS deleted, history remains but link becomes NULL)
```

## 🎯 Key Features

### Data Integrity
- **Foreign Key Constraints**: Ensures TTS-History relationships remain valid
- **Check Constraints**: Validates enum values (type, status, mode)
- **Unique Constraints**: Prevents duplicate TTS timestamps and cache keys

### Performance Optimization  
- **Strategic Indexing**: Fast queries on timestamp, type, and status fields
- **Compound Indexes**: Support complex filtering scenarios

### Flexibility
- **JSON Metadata Field**: Stores additional track information without schema changes
- **Nullable References**: History can exist without TTS (regular music tracks)
- **Status Tracking**: Soft delete support with status flags

## 📈 Usage Patterns

```
┌─ TTS Creation ──────────────────────────────────────┐
│ 1. AI generates commentary text                     │
│ 2. TTS engine creates audio file                   │  
│ 3. Insert into tts_entries with timestamp          │
│ 4. Link to play_history when track plays           │
└─────────────────────────────────────────────────────┘

┌─ History Tracking ─────────────────────────────────┐
│ 1. Track starts playing                           │
│ 2. Insert into play_history                       │
│ 3. Link tts_entry_id if DJ commentary exists      │
│ 4. Query with JOINs for complete information      │
└─────────────────────────────────────────────────────┘

┌─ Data Cleanup ─────────────────────────────────────┐
│ 1. Mark old TTS entries as 'deleted'              │
│ 2. Clean up expired artwork cache entries         │
│ 3. Foreign keys maintain referential integrity    │
└─────────────────────────────────────────────────────┘
```

---
*Generated for AI Radio System - Database schema visualization*