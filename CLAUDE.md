# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## System Architecture

AI Radio is an intelligent streaming platform that combines Liquidsoap audio automation with AI-powered DJ commentary and multiple TTS engines. The system runs in Docker containers with systemd management.

### Core Components

- **Liquidsoap Engine** (`radio.liq`): Handles audio streaming, queue management, and telnet control interface on port 1234
- **Flask Web Application** (`ui/app.py`): Provides REST API and real-time web interface on port 5055
- **AI Commentary System**: Uses Ollama models (default: llama3.2:3b) for contextual DJ commentary
- **TTS Pipeline**: Supports XTTS, ElevenLabs, and Piper engines for speech synthesis
- **Metadata Management**: JSON-based persistence for track history, current/next track info

### Directory Structure

```
/opt/ai-radio/                  # Main application directory
├── ui/                         # Flask web interface
│   ├── app.py                 # Main application
│   └── index.html             # Frontend UI
├── radio.liq                  # Liquidsoap configuration
├── auto_dj.conf              # DJ automation settings
├── dj_settings.json          # DJ templates and TTS settings
├── library_clean.m3u         # Music library playlist
├── tts/                      # Generated TTS files
├── tts_queue/               # TTS processing queue
├── cache/covers/            # Album artwork cache
├── logs/                    # Application logs
├── voices/                  # TTS voice models
├── xtts-venv/              # Python virtual environment for XTTS
└── *.sh                     # Various utility scripts
```

## Key Shell Scripts

- **gen_ai_dj_line.sh**: Generates AI-powered DJ commentary using Ollama
- **dj_enqueue_xtts_ai.sh**: Combined AI generation + XTTS synthesis
- **dj_enqueue_xtts.sh**: XTTS text-to-speech generation
- **dj_enqueue_elevenlabs.sh**: ElevenLabs TTS integration
- **monitor_tts_queue.sh**: Monitors and processes TTS generation queue
- **fix_sine_fallback.sh**: Fixes audio fallback issues

## Service Management

The system runs as two separate systemd services:

```bash
# AI Radio service (Liquidsoap in Docker)
systemctl status ai-radio
systemctl restart ai-radio
systemctl stop ai-radio
systemctl start ai-radio

# AI DJ UI service (Flask web interface)
systemctl status ai-dj-ui
systemctl restart ai-dj-ui
systemctl stop ai-dj-ui
systemctl start ai-dj-ui

# Check both processes
ps aux | grep liquidsoap
ps aux | grep flask

# View service logs
journalctl -u ai-radio -f
journalctl -u ai-dj-ui -f
```

## Development Commands

### Starting the System

1. **Start main radio service**: `systemctl start ai-radio`
2. **Start Flask web interface**: `systemctl start ai-dj-ui`

Note: Both services need to be restarted for configuration changes to take effect.

### Configuration

- **Music Library**: Edit `library_clean.m3u` to point to music files
- **DJ Settings**: Modify `dj_settings.json` for TTS voices and templates
- **Auto DJ**: Configure `auto_dj.conf` for generation intervals and models

### Environment Variables

- `OLLAMA_MODELS="/mnt/music/ai-dj/ollama"`: Ollama model directory
- `XTTS_SPEAKER="Damien Black"`: Default XTTS voice
- `DJ_INTRO_MODE`: Enable/disable intro generation mode
- `USE_XTTS=1`: Enable XTTS engine
- `ELEVENLABS_API_KEY`: API key for ElevenLabs TTS

### TTS Generation

```bash
# Generate AI intro for track
./dj_enqueue_xtts_ai.sh "Artist Name" "Track Title" en "Voice Name" intro

# Monitor TTS queue processing
./monitor_tts_queue.sh

# Test XTTS directly
./xtts_with_lock.sh "Test message" en "Damien Black"
```

### API Endpoints

- `GET /api/now` - Current playing track
- `GET /api/next` - Upcoming tracks
- `GET /api/history` - Play history
- `POST /api/dj-next` - Generate DJ intro
- `POST /api/skip` - Skip current track
- `POST /api/tts_queue` - Add TTS to queue

### Debugging

- **Liquidsoap telnet**: `telnet 127.0.0.1 1234` for direct control (remember to use `\nquit` after commands)
- **TTS Debug**: Check `/opt/ai-radio/logs/` for generation logs
- **Docker logs**: `docker logs ai-radio`
- **Service logs**: `journalctl -u ai-radio -f` or `journalctl -u ai-dj-ui -f`

### File Locations

- Current track: `/opt/ai-radio/now.json`
- Next tracks: `/opt/ai-radio/next.json`
- Play history: `/opt/ai-radio/play_history.json`
- Generated TTS: `/opt/ai-radio/tts/`
- Album covers: `/opt/ai-radio/cache/covers/`

### Dependencies

- Docker (for Liquidsoap container)
- Python 3.x with Flask, Mutagen, Requests
- Ollama for AI generation
- XTTS virtual environment in `xtts-venv/`
- Optional: ElevenLabs API access

## Common Tasks

1. **Add new TTS voice**: Place voice files in `/opt/ai-radio/voices/` and update `dj_settings.json`
2. **Change AI model**: Modify `MODEL` variable in `auto_dj.conf` or scripts
3. **Update music library**: Edit `library_clean.m3u` and restart service
4. **Monitor generation**: Use `./monitor_tts_queue.sh` to watch TTS processing
5. **Debug audio issues**: Check `./fix_sine_fallback.sh` for common problems
- There are two systemd processes ai-radio (liquidsoap) and ai-dj-ui (flask). You can restart these for changes to take effect.
- Always commit changes to git.  update README only if there is functionality changes