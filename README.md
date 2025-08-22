# AI Radio

An intelligent radio streaming platform that automatically generates DJ commentary and manages music playback using Liquidsoap and AI-powered text-to-speech synthesis.

## Features

- **AI DJ Commentary**: Automatically generates contextual commentary about upcoming tracks using AI models
- **Multiple TTS Engines**: Supports XTTS, ElevenLabs, and Piper text-to-speech synthesis
- **Web Interface**: Real-time web UI for monitoring current track, history, and upcoming queue
- **Automated Streaming**: Liquidsoap-based radio automation with intelligent queuing
- **Cover Art**: Automatic album cover extraction and display
- **Track History**: Persistent logging and display of played tracks
- **API Integration**: RESTful API for external integrations and control

## Architecture

- **Backend**: Python Flask application (`ui/app.py`) providing REST API and web interface
- **Streaming**: Liquidsoap (`radio.liq`) handles audio streaming and queue management
- **TTS Generation**: Multiple shell scripts for different TTS providers
- **AI Generation**: Ollama integration for intelligent DJ commentary
- **Storage**: JSON-based persistence for history and metadata

## Directory Structure

```
/opt/ai-radio/
├── ui/                     # Web interface
│   ├── app.py             # Main Flask application
│   └── index.html         # Frontend interface
├── cache/covers/          # Album cover cache
├── tts/                   # Generated TTS audio files
├── logs/                  # Application logs
├── static/                # Static web assets
├── voices/                # TTS voice models
├── utils/                 # Utility scripts
├── radio.liq              # Liquidsoap configuration
├── auto_dj.conf           # DJ configuration
├── dj_settings.json       # DJ settings
├── library_clean.m3u      # Music library playlist
├── play_history.json      # Track history
├── now.json               # Current track metadata
├── next.json              # Upcoming tracks
└── *.sh                   # Various utility scripts
```

## Key Components

### Flask Application (`ui/app.py`)
- Serves web interface and API endpoints
- Manages track history and metadata
- Integrates with TTS engines
- Provides real-time track information

### Liquidsoap (`radio.liq`)
- Handles audio streaming and broadcasting
- Manages music queue and transitions
- Integrates TTS audio into stream
- Provides telnet interface for control

### TTS Scripts
- `gen_ai_dj_line.sh`: Generates AI commentary using Ollama
- `dj_enqueue_xtts.sh`: XTTS text-to-speech synthesis
- `dj_enqueue_elevenlabs.sh`: ElevenLabs TTS integration
- `dj_enqueue_xtts_ai.sh`: AI-enhanced XTTS generation

## API Endpoints

- `GET /api/now` - Current playing track
- `GET /api/next` - Upcoming tracks in queue
- `GET /api/history` - Recently played tracks
- `GET /api/cover?file=<path>` - Album artwork
- `POST /api/dj-next` - Generate DJ intro for next track
- `POST /api/skip` - Skip current track
- `POST /api/tts_queue` - Add TTS to queue

## Configuration

### Environment Variables
- `ELEVENLABS_API_KEY`: API key for ElevenLabs TTS
- `ELEVENLABS_VOICE_ID`: Voice ID for ElevenLabs
- `XTTS_SPEAKER`: Speaker name for XTTS
- `USE_XTTS`: Enable/disable XTTS (default: 1)
- `DJ_INTRO_MODE`: DJ commentary mode flag

### Music Library
Edit `library_clean.m3u` to point to your music files. Supports standard audio formats (MP3, FLAC, M4A, WAV).

### TTS Configuration
- XTTS models stored in `xtts-venv/`
- Voice samples in `voices/` directory
- DJ settings in `dj_settings.json`

## Installation & Setup

1. Ensure Liquidsoap 2.3.x is installed
2. Install Python dependencies for Flask application
3. Configure music library path in `library_clean.m3u`
4. Set up TTS engine of choice (XTTS, ElevenLabs, or Piper)
5. Configure streaming settings in `radio.liq`
6. Start Liquidsoap: `liquidsoap radio.liq`
7. Start web interface: `python ui/app.py`

## Dependencies

- **Liquidsoap 2.3.x**: Audio streaming engine
- **Python 3.x**: Flask web application
- **Flask**: Web framework
- **Mutagen**: Audio metadata extraction
- **Requests**: HTTP client for external APIs
- **Ollama**: AI model for commentary generation
- **XTTS/ElevenLabs/Piper**: Text-to-speech engines

## Web Interface

Access the web interface at `http://localhost:5055` to view:
- Current playing track with album art
- Recently played history
- AI DJ commentary timeline
- Upcoming tracks queue
- Playback controls

## AI DJ Features

The system automatically generates contextual commentary about upcoming tracks, including:
- Track introductions
- Artist information
- Contextual music commentary
- Smooth transitions between songs

Commentary is generated using AI models and synthesized to speech using configurable TTS engines, then seamlessly mixed into the audio stream by Liquidsoap.