# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

AI Radio is a Liquidsoap-based streaming platform with AI-powered DJ commentary. The system consists of:

**Core Components:**
- **Liquidsoap** (`radio.liq`): Audio streaming engine running in Docker container, handles music playback and TTS integration
- **Flask Web UI** (`ui/app.py`): Web interface and REST API for monitoring/control
- **Metadata Daemon** (`metadata_daemon.py`): Caches Liquidsoap metadata to prevent telnet storms
- **TTS System**: Multi-engine text-to-speech with XTTS, ElevenLabs, and Piper support
- **AI DJ System**: Multi-tier AI commentary generation with Ollama and OpenAI fallbacks

**Service Architecture:**
```
ai-radio.service         → Liquidsoap in Docker (port 8000/stream.mp3)
ai-dj-ui.service        → Flask web UI (port 5055)
ai-metadata-daemon.service → Metadata caching daemon
ai-dj-daemon.service    → [BROKEN] Missing dj_daemon.py file
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

# Access Liquidsoap telnet interface
telnet localhost 1234

# Test stream accessibility
curl -s http://localhost:8000/stream.mp3 -o /dev/null -w "HTTP: %{http_code}\n"

# Monitor TTS queue
./monitor_tts_queue.sh

# Flask development (from ui/ directory)
cd ui && .venv/bin/python app.py
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

**Liquidsoap Integration:**
- Runs in Docker with volume mounts to `/opt/ai-radio` and `/mnt/music`
- Uses telnet interface on port 1234 for TTS queue management
- Automatically generates DJ intros via `auto_generate_dj_intro()` function
- Metadata stored in `/opt/ai-radio/now.json` and `/opt/ai-radio/next.json`

**TTS Pipeline:**
1. AI generates commentary via `gen_ai_dj_line_enhanced.sh`
2. Text-to-speech via `dj_enqueue_xtts.sh` or ElevenLabs scripts
3. Audio enqueued to Liquidsoap via telnet `tts.push request.create()`
4. Liquidsoap mixes TTS with music stream

**Multi-Tier AI Fallback System:**
- Tier 1: OpenAI (gpt-4o-mini, gpt-3.5-turbo)
- Tier 2: Ollama local models (llama3.2:3b, llama3.2:1b, phi3:mini)
- Tier 3: Ollama alternative models (mistral:7b, gemma:2b)
- Tier 4: Template-based fallback for reliability

**Metadata Flow:**
```
Liquidsoap → metadata_daemon.py → JSON cache files → Flask API → Web UI
```

**File Structure (Key Paths):**
```
/opt/ai-radio/
├── radio.liq              # Main Liquidsoap configuration
├── ui/app.py              # Flask web application
├── metadata_daemon.py     # Metadata caching service
├── dj_settings.json       # AI DJ configuration and prompts
├── library_clean.m3u      # Music library playlist
├── gen_ai_dj_line_enhanced.sh # AI commentary generation
├── dj_enqueue_xtts.sh     # XTTS text-to-speech
├── cache/                 # Metadata cache directory
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

## Known Issues

**Missing DJ Daemon:**
The `ai-dj-daemon.service` is failing because `/opt/ai-radio/dj_daemon.py` doesn't exist. The system currently uses shell scripts for background DJ functionality instead of a Python daemon.

**Multi-Station Setup:**
The system is designed to support multiple Liquidsoap instances for different playback modes (random/sequential/genre-based) as outlined in the original CLAUDE.md planning notes.