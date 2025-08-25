# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

AI Radio is a **SQLite database-driven** streaming platform with AI-powered DJ commentary. The system consists of:

**Core Components:**
- **Liquidsoap** (`radio.liq`): Audio streaming engine running in Docker container, handles music playback and TTS integration
- **Flask Web UI** (`ui/app.py`): Modern modular web interface with database-backed services
- **SQLite Database** (`ai_radio.db`): Single source of truth for all track history, TTS entries, and artwork cache
- **Metadata Daemon** (`metadata_daemon.py`): Caches real-time Liquidsoap metadata for UI responsiveness
- **TTS System**: Multi-engine text-to-speech with database tracking and linking
- **AI DJ System**: Multi-tier AI commentary generation with persistent history

**Service Architecture:**
```
ai-radio.service         → Liquidsoap in Docker (port 8000/stream.mp3)
ai-dj-ui.service        → Flask web UI (port 5055) → SQLite Database
ai-metadata-daemon.service → Real-time metadata caching
```

## Key System Commands

**Service Management:**
```bash
# Check service status
systemctl status ai-radio.service
systemctl status ai-dj-ui.service
systemctl status ai-metadata-daemon.service

# View logs
journalctl -u ai-radio.service -f
journalctl -u ai-dj-ui.service -f
docker logs ai-radio

# Restart services
systemctl restart ai-radio.service
systemctl restart ai-dj-ui.service
```

**Development Commands:**
```bash
# Test TTS generation
./dj_enqueue_xtts.sh "Test message"
./gen_ai_dj_line_enhanced.sh "Song Title" "Artist Name"

# Skip current track via Harbor HTTP
curl -X PUT http://127.0.0.1:8001/music -H "Content-Type: audio/mpeg" --data-binary "@silence.mp3"

# Test stream accessibility
curl -s http://localhost:8000/stream.mp3 -o /dev/null -w "HTTP: %{http_code}\n"

# Monitor TTS queue (DEPRECATED - Harbor HTTP handles reliability)
# ./monitor_tts_queue.sh

# Flask development (from ui/ directory)
cd ui && .venv/bin/python app.py

# Database operations
sqlite3 ai_radio.db ".tables"  # List all tables
sqlite3 ai_radio.db "SELECT COUNT(*) FROM play_history;"  # Check history count
python3 init_database.py  # Initialize/migrate database schema
```

**Configuration Files:**
```bash
# Edit DJ settings and AI prompts
vi dj_settings.json

# Configure music library
vi library_clean.m3u

# Modify Liquidsoap config
vi radio.liq

# TTS voice configuration
vi auto_dj.conf
```

## Critical System Architecture Details

**Database Schema (SQLite):**
- **`play_history`**: All track play events and DJ commentary with full metadata
- **`tts_entries`**: Generated TTS files linked to tracks via timestamps  
- **`artwork_cache`**: Album art cache to reduce filesystem lookups
- **Single source of truth**: Eliminates data inconsistencies between components

**Liquidsoap Integration:**
- Runs in Docker with volume mounts to `/opt/ai-radio` and `/mnt/music`
- Uses **Harbor HTTP inputs** (ports 8001/8002) for reliable audio stream control
- Sends track events to Flask `/api/event` endpoint via HTTP calls
- Automatically generates DJ intros via `auto_generate_dj_intro()` function
- Real-time metadata cached in `/opt/ai-radio/cache/now_metadata.json`

**TTS Pipeline:**
1. AI generates commentary via `gen_ai_dj_line_enhanced.sh`
2. Text-to-speech via `dj_enqueue_xtts.sh` or ElevenLabs scripts
3. **TTS entry stored in database** with track linking
4. Audio enqueued to Liquidsoap via **Harbor HTTP** (port 8002)
5. Liquidsoap mixes TTS with music stream

**Multi-Tier AI Fallback System:**
- Tier 1: OpenAI (gpt-4o-mini, gpt-3.5-turbo)
- Tier 2: Ollama local models (llama3.2:3b, llama3.2:1b, phi3:mini)
- Tier 3: Ollama alternative models (mistral:7b, gemma:2b)
- Tier 4: Template-based fallback for reliability

**Data Flow:**
```
Liquidsoap → Flask /api/event → SQLite Database → History Service → Web UI
         → metadata_daemon.py → cache files (real-time metadata)
```

**File Structure (Key Paths):**
```
/opt/ai-radio/
├── ai_radio.db            # SQLite database (primary data store)
├── radio.liq              # Main Liquidsoap configuration
├── ui/app.py              # Flask web application (modular architecture)
├── ui/services/           # Database-backed service layer
│   ├── history.py         # History service (SQLite-backed)
│   ├── metadata.py        # Metadata service
│   └── tts.py            # TTS service
├── database.py            # Database manager and operations
├── init_database.py       # Database initialization script
├── metadata_daemon.py     # Real-time metadata caching service
├── dj_settings.json       # AI DJ configuration and prompts
├── library_clean.m3u      # Music library playlist
├── gen_ai_dj_line_enhanced.sh # AI commentary generation
├── dj_enqueue_xtts.sh     # XTTS text-to-speech
├── cache/                 # Real-time metadata cache directory
├── tts/                   # Generated TTS audio files
├── logs/                  # System logs
└── voices/                # TTS voice samples
```

**Environment Variables (for services):**
- `TTS_ENGINE`: xtts|elevenlabs|piper
- `XTTS_SPEAKER`: Voice name for XTTS
- `OLLAMA_MODELS`: Path to Ollama models
- `ELEVENLABS_API_KEY`: ElevenLabs API key
- `DJ_INTRO_MODE`: Enable/disable DJ commentary

## Database Management

**Database Initialization:**
- Run `python3 init_database.py` to create schema and migrate existing JSON data
- Database located at `/opt/ai-radio/ai_radio.db` 
- Automatic backups created during migration (e.g., `play_history.json.backup.YYYYMMDD_HHMMSS`)

**Database Tables:**
- **`play_history`**: Primary table for all track/DJ events with full metadata and artwork URLs
- **`tts_entries`**: TTS generation history with file paths and track associations
- **`artwork_cache`**: Album art caching to reduce filesystem lookups

**Data Consistency:**
- All services now use SQLite as single source of truth
- Real-time metadata still cached in `/opt/ai-radio/cache/` for performance
- No more JSON file inconsistencies between history, TTS, and metadata services

## Migration from Legacy System

**If experiencing data inconsistencies:**
1. Stop services: `systemctl stop ai-dj-ui.service`
2. Run database migration: `python3 init_database.py`
3. Restart services: `systemctl start ai-dj-ui.service`
4. Verify with: `sqlite3 ai_radio.db "SELECT COUNT(*) FROM play_history;"`

**Legacy JSON files (now obsolete):**
- `play_history.json` → migrated to `play_history` table
- Individual TTS tracking → migrated to `tts_entries` table
- Manual file management → automated database operations