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
- 🌐 **React Frontend**: Modern web UI with real-time WebSocket updates and smooth animations
- 📻 **Automated Streaming**: Liquidsoap-based radio automation with intelligent queuing
- 🎨 **Cover Art**: Automatic album cover extraction and display with database enhancement
- 📝 **SQLite Database**: Robust data storage for track history, TTS entries, and metadata
- 🔄 **Real-time Next Tracks**: Live upcoming queue display with database-enhanced artwork
- 🎯 **TTS-Audio Synchronization**: Perfect matching between displayed text and actual audio
- 🔌 **Harbor Integration**: Modern streaming protocol with WebSocket real-time updates
- ⚡ **Telnet Storm Prevention**: Optimized metadata caching prevents connection flooding
- 🎵 **Track Progress**: Accurate timing with persistent progress across page refreshes

## Architecture 🏗️

- 🐍 **Backend**: Python Flask application (`ui/app.py`) with SQLite database and WebSocket support
- ⚛️ **Frontend**: React TypeScript application with Framer Motion animations and real-time updates
- 🎧 **Streaming**: Liquidsoap (`radio.liq`) with Harbor protocol and metadata caching daemon
- 🗣️ **TTS Generation**: Database-integrated scripts with automatic text-audio linking
- 🧠 **AI Generation**: Multi-tier fallback system with Ollama and OpenAI integration
- 💾 **Storage**: SQLite database with ACID transactions and referential integrity
- 🔄 **Metadata System**: Real-time caching daemon prevents telnet storms while ensuring fresh data

## Directory Structure

```
/opt/ai-radio/
├── ui/                     # Flask web application
│   ├── app.py             # Main Flask application with WebSocket support
│   ├── index.html         # Compiled React frontend
│   └── static/            # Built React assets (CSS, JS)
├── radio-frontend/         # React TypeScript source
│   ├── src/App.tsx        # Main React component with animations
│   ├── src/App.css        # Modern CSS with transitions
│   └── package.json       # React dependencies
├── cache/                 # Metadata caching system
│   ├── now_metadata.json  # Current track cache
│   └── next_metadata.json # Upcoming tracks cache
├── tts/                   # Generated TTS audio files
├── logs/                  # Application logs  
├── voices/                # TTS voice samples
├── database.py            # SQLite database management
├── metadata_daemon.py     # Real-time metadata caching daemon
├── ai_radio.db           # SQLite database file
├── radio.liq             # Liquidsoap configuration with Harbor support
├── dj_settings.json      # AI DJ configuration and prompts
├── library_clean.m3u     # Music library playlist
└── *.sh                  # TTS generation scripts with database integration
```

## Key Components

### Flask Application (`ui/app.py`)
- SQLite database integration with ACID transactions
- WebSocket support for real-time frontend updates
- REST API with optimized metadata caching
- TTS-audio synchronization system
- Advanced scrobbling with DJ content detection

### React Frontend (`radio-frontend/`)
- TypeScript-based single-page application
- Framer Motion animations and smooth transitions
- Real-time WebSocket integration for live updates
- Persistent track progress across page refreshes
- Responsive design with modern CSS

### Metadata Caching Daemon (`metadata_daemon.py`)
- Real-time Liquidsoap metadata parsing
- Intelligent track change detection with timestamps
- Database-enhanced next track information
- Telnet storm prevention with smart caching
- Automatic current track exclusion from queue

### Database System (`database.py`, `ai_radio.db`)
- SQLite with referential integrity and foreign keys
- TTS entries table with text-audio linking
- Play history with automatic deduplication
- Track lookup system for artwork enhancement
- Thread-safe operations with connection pooling

### TTS Integration Scripts
- `dj_enqueue_xtts.sh`: Database-integrated XTTS generation
- `gen_ai_dj_line_enhanced.sh`: Multi-tier AI fallback system
- Automatic database entry creation for all TTS files
- Perfect text-audio synchronization via timestamps

## API Endpoints 🚀

- 📡 `GET /api/now` - Current playing track with accurate start time
- ⏭️ `GET /api/next` - Database-enhanced upcoming tracks with artwork
- 📜 `GET /api/history` - Recently played tracks with TTS text matching
- 🖼️ `GET /api/cover?file=<path>` - Album artwork with caching
- 🎙️ `GET /api/event` - Event ingestion for DJ/song tracking
- 🔊 `POST /api/tts_queue` - Add TTS to Liquidsoap queue
- ⏩ `POST /api/skip` - Skip current track
- 💊 `GET /api/health` - Service health check with telnet status

## Advanced Systems 🛡️

### Metadata Caching Architecture
**Smart Caching Strategy:**
- Dedicated metadata daemon queries Liquidsoap every 3 seconds
- Frontend uses cached data via REST API (no direct telnet calls)
- Intelligent track change detection with timestamps
- Database-enhanced upcoming tracks with artwork lookup

**Architecture Flow:**
```
metadata_daemon.py → Liquidsoap telnet → Cache files → Flask API → React Frontend
TTS Scripts → SQLite Database → Flask API → WebSocket updates
```

**Benefits:**
- Zero telnet storms with sub-second response times
- Real-time WebSocket updates for instant UI changes
- Persistent track timing across page refreshes
- Database-enhanced metadata for richer experiences

### Database Integration
**SQLite Features:**
- ACID transactions ensure data consistency
- Foreign key relationships link TTS to history entries
- Automatic deduplication prevents duplicate entries
- Thread-safe operations for concurrent access

**TTS Synchronization:**
- Every TTS file automatically creates database entry
- Perfect text-audio matching via timestamp correlation
- Historical lookup enhances upcoming tracks with artwork
- Referential integrity maintains data consistency

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