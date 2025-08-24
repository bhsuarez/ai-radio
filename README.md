# AI Radio

```
         .-""""""""-.
       .'            '.
      /                \
     ;      .-""-.      ;
    /      /      \      \
   ;      ;        ;      ;
   |      |   🎵   |      |
   ;      ;        ;      ;
    \      \      /      /
     ;      '-__-'      ;
      \                /
       '.            .'
         '-.........-'
```

An intelligent radio streaming platform that automatically generates DJ commentary and manages music playback using Liquidsoap and AI-powered text-to-speech synthesis.

## Features ✨

- 🤖 **AI DJ Commentary**: Automatically generates contextual commentary about upcoming tracks using AI models
- 🎤 **Multiple TTS Engines**: Supports XTTS, ElevenLabs, and Piper text-to-speech synthesis
- 🌐 **Web Interface**: Real-time web UI for monitoring current track, history, and upcoming queue
- 📻 **Automated Streaming**: Liquidsoap-based radio automation with intelligent queuing
- 🎨 **Cover Art**: Automatic album cover extraction and display
- 📝 **Track History**: Persistent logging and display of played tracks
- 🔌 **API Integration**: RESTful API for external integrations and control
- ⚡ **Telnet Storm Prevention**: Optimized polling system prevents connection flooding while maintaining real-time updates

## Architecture 🏗️

- 🐍 **Backend**: Python Flask application (`ui/app.py`) providing REST API and web interface
- 🎧 **Streaming**: Liquidsoap (`radio.liq`) handles audio streaming and queue management
- 🗣️ **TTS Generation**: Multiple shell scripts for different TTS providers
- 🧠 **AI Generation**: Ollama integration for intelligent DJ commentary
- 💾 **Storage**: JSON-based persistence for history and metadata

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

## API Endpoints 🚀

- 📡 `GET /api/now` - Current playing track
- ⏭️ `GET /api/next` - Upcoming tracks in queue  
- ⚡ `GET /api/track-check` - **Optimized polling**: Current + next track info in single call
- 📜 `GET /api/history` - Recently played tracks
- 🖼️ `GET /api/cover?file=<path>` - Album artwork
- 🎵 `POST /api/enqueue` - Enqueue TTS files via Flask API (replaces direct telnet)
- 🎙️ `POST /api/dj-next` - Generate DJ intro for next track
- ⏩ `POST /api/skip` - Skip current track
- 🔊 `POST /api/tts_queue` - Add TTS to queue

## Telnet Storm Prevention 🛡️

This system implements an optimized polling architecture to prevent telnet connection flooding:

**Smart Polling Strategy:**
- Frontend polls `/api/track-check` every 15 seconds
- Single telnet call retrieves both current and next track metadata  
- Only full refresh when `track_id` changes
- Metadata daemon uses Flask API instead of direct telnet

**Architecture Flow:**
```
Frontend → /api/track-check (15s intervals) → Single telnet call → Liquidsoap
Metadata Daemon → Flask API → (no telnet)
TTS Scripts → Flask API → (no telnet)
```

**Benefits:**
- Zero telnet storms while maintaining real-time updates
- Minimal resource usage with smart change detection
- Fresh metadata without cache staleness issues

## Configuration ⚙️

### Environment Variables 🌍
- `ELEVENLABS_API_KEY`: API key for ElevenLabs TTS
- `ELEVENLABS_VOICE_ID`: Voice ID for ElevenLabs
- `XTTS_SPEAKER`: Speaker name for XTTS
- `USE_XTTS`: Enable/disable XTTS (default: 1)
- `DJ_INTRO_MODE`: DJ commentary mode flag

### Music Library 🎵
Edit `library_clean.m3u` to point to your music files. Supports standard audio formats (MP3, FLAC, M4A, WAV).

### TTS Configuration 🎤
- XTTS models stored in `xtts-venv/`
- Voice samples in `voices/` directory
- DJ settings in `dj_settings.json`

## Installation & Setup 🚀

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

### Current Features

Access the web interface at `http://localhost:5055` to view:
- **Now Playing**: Current track with album art from online lookups
- **Coming Up**: Next 5 tracks in queue with position numbers
- **Recently Played**: History with both songs and DJ commentary
- **Real-time Updates**: WebSocket integration for live track changes
- **Responsive Design**: Mobile-friendly interface with error handling
- **Album Art**: Automatic online lookup via iTunes/MusicBrainz APIs

### React Frontend Benefits

The modern React-based frontend provides:
- **Real-time Interface**: Live updates without page refreshes
- **Better Performance**: Client-side rendering reduces server load  
- **Professional UI**: Modern design with animations and responsive layout
- **Error Resilience**: Graceful handling of API failures with fallbacks

### Future Possibilities 🚀

The React foundation enables advanced features:

#### **Interactive Controls**
- **Real-time DJ Controls**: Live volume, EQ, and audio effects adjustment
- **Request System**: User voting and track request functionality
- **Skip Controls**: Listener-driven track skipping with voting
- **Playlist Management**: Drag-and-drop queue reordering

#### **Social Features**
- **Live Chat**: Real-time listener interaction and community
- **Track Ratings**: User feedback on played tracks
- **Social Sharing**: Share favorite tracks to social media
- **User Profiles**: Personalized listening history and preferences

#### **Advanced Analytics**
- **Live Dashboard**: Real-time listener stats and engagement metrics
- **Music Analytics**: Track popularity, skip rates, and listening patterns  
- **DJ Performance**: Commentary effectiveness and listener retention
- **Geographic Stats**: Listener locations and regional preferences

#### **Mobile & Extended Platform**
- **Mobile App**: React Native version for iOS and Android
- **Desktop App**: Electron-based desktop application
- **Smart Speaker Integration**: Alexa, Google Home compatibility
- **Car Integration**: Android Auto and CarPlay support

#### **Enhanced Audio Features**
- **Audio Visualization**: Real-time spectrum analyzer and waveforms
- **Lyrics Display**: Synchronized lyrics with currently playing track
- **Cross-fade Controls**: User-adjustable transition settings  
- **Audio Effects**: Real-time reverb, echo, and filter controls

#### **Content Management**
- **Multi-station Support**: Different genres, moods, or themes
- **Automated Scheduling**: Time-based programming and content blocks
- **Content Curation**: AI-powered music discovery and playlist generation
- **Podcast Integration**: Mixed content with music and spoken word

#### **Integration Capabilities**
- **Streaming Platforms**: Spotify, Apple Music, YouTube Music integration
- **Last.fm Scrobbling**: Automatic track scrobbling for listeners
- **Discord Bots**: Server integration for community radio
- **Home Automation**: Smart home integration for ambient audio

## AI DJ Features

The system automatically generates contextual commentary about upcoming tracks, including:
- Track introductions
- Artist information
- Contextual music commentary
- Smooth transitions between songs

Commentary is generated using AI models and synthesized to speech using configurable TTS engines, then seamlessly mixed into the audio stream by Liquidsoap.