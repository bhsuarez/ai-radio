# AI Radio System Architecture

This document provides a comprehensive overview of the AI Radio system architecture, data flows, and component interactions.

## Entity Relationship Diagram (ERD) 🗂️

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Music Files   │    │   Playlist      │    │ Request Queue   │
│                 │    │                 │    │                 │
│ • filename      │────│ • file_path     │────│ • request_id    │
│ • title         │    │ • enabled       │    │ • uri           │
│ • artist        │    │ • order         │    │ • status        │
│ • album         │    │                 │    │ • metadata      │
│ • genre         │    │                 │    │ • created_at    │
│ • duration      │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
         ┌─────────────────┐     │     ┌─────────────────┐
         │ Current Track   │─────┼─────│   TTS Queue     │
         │                 │     │     │                 │
         │ • track_data    │     │     │ • tts_id        │
         │ • start_time    │     │     │ • audio_file    │
         │ • elapsed       │     │     │ • text_content  │
         │ • remaining     │     │     │ • status        │
         └─────────────────┘     │     └─────────────────┘
                                 │
         ┌─────────────────┐     │     ┌─────────────────┐
         │ Metadata Cache  │─────┼─────│  Play History   │
         │                 │     │     │                 │
         │ • now.json      │     │     │ • track_id      │
         │ • next.json     │     │     │ • played_at     │
         │ • cached_at     │     │     │ • title         │
         │ • expires_at    │     │     │ • artist        │
         └─────────────────┘     │     │ • duration      │
                                 │     └─────────────────┘
         ┌─────────────────┐     │
         │ Stream Output   │─────┘
         │                 │
         │ • icecast_url   │
         │ • current_meta  │
         │ • listener_count│
         │ • bitrate       │
         └─────────────────┘
```

## System Flow Chart 🔄

```
                    ┌─────────────────┐
                    │ library_clean   │
                    │ .m3u playlist   │
                    └─────────┬───────┘
                              │ reads
                              ▼
    ┌─────────────────────────────────────────────────────────┐
    │                 LIQUIDSOAP CORE                         │
    │                                                         │
    │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
    │  │   Playlist  │───▶│ Music Queue │───▶│   Mixer     │ │
    │  │   Source    │    │  (random)   │    │             │ │
    │  └─────────────┘    └─────────────┘    └─────┬───────┘ │
    │                              │                │         │
    │  ┌─────────────┐             │                │         │
    │  │ TTS Queue   │─────────────┘                │         │
    │  │ (priority)  │                               │         │
    │  └─────────────┘                               ▼         │
    │                                      ┌─────────────┐     │
    │                                      │ Icecast     │     │
    │                                      │ Output      │     │
    │                                      └─────────────┘     │
    └─────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────────────┐
        │              METADATA FLOW                          │
        │                                                     │
        │  Track Selected ───▶ Auto DJ Trigger ───▶ AI Gen   │
        │       │                    │                 │      │
        │       ▼                    ▼                 ▼      │
        │  Metadata ──────▶ Flask API Call ────▶ TTS Gen     │
        │   Update              │                     │       │
        │       │              ▼                     ▼       │
        │       ▼         ┌─────────┐          ┌─────────┐    │
        │  ┌─────────┐    │ Flask   │          │ TTS     │    │
        │  │ Telnet  │◀───│ API     │          │ Audio   │    │
        │  │ Server  │    │         │          │ File    │    │
        │  └─────────┘    └─────────┘          └─────────┘    │
        │       │              │                     │       │
        │       ▼              ▼                     ▼       │
        │  Metadata ──────▶ Cache Files ──────▶ Enqueue     │
        │  History              │                 to TTS      │
        │                       ▼                Queue       │
        │               ┌─────────────┐                      │
        │               │ Web UI      │                      │
        │               │ Frontend    │                      │
        │               └─────────────┐                      │
        └─────────────────────────────────────────────────────┘
```

## Detailed Process Flows 📋

### 1. Song Queue Management

```
library_clean.m3u
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ File Scan   │────▶│ Random Pick │────▶│ Add to      │
│ (Liquidsoap)│     │ Algorithm   │     │ Queue       │
└─────────────┘     └─────────────┘     └─────────────┘
```

**Components:**
- **library_clean.m3u**: Master playlist file containing all available music files
- **Liquidsoap Playlist Source**: Scans and loads files from the playlist
- **Random Algorithm**: Shuffles tracks to prevent repetition
- **Request Queue**: Internal Liquidsoap queue system

### 2. Metadata Extraction & Processing

```
Selected Song
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Extract     │────▶│ Normalize   │────▶│ Trigger     │
│ ID3 Tags    │     │ Metadata    │     │ Auto DJ     │
└─────────────┘     └─────────────┘     └─────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Cache in    │     │ Update      │     │ AI Commentary│
│ now.json    │     │ Icecast     │     │ Generation  │
└─────────────┘     └─────────────┘     └─────────────┘
```

**Process Steps:**
1. **ID3 Tag Extraction**: Liquidsoap reads embedded metadata (title, artist, album, etc.)
2. **Metadata Normalization**: Clean and standardize metadata format
3. **Cache Storage**: Save metadata to `now.json` and `next.json` files
4. **Icecast Update**: Push metadata to streaming server for client display
5. **Auto DJ Trigger**: Initiate AI commentary generation for upcoming track

### 3. AI DJ Commentary Flow

```
Track Ready
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Flask API   │────▶│ Ollama/     │────▶│ TTS         │
│ Call        │     │ OpenAI      │     │ Generation  │
└─────────────┘     └─────────────┘     └─────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Track Info  │     │ AI Text     │     │ Audio File  │
│ (Artist/    │     │ Response    │     │ (.mp3)      │
│ Title)      │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
                           │                    │
                           ▼                    ▼
                    ┌─────────────┐     ┌─────────────┐
                    │ Text        │     │ Enqueue to  │
                    │ Processing  │     │ TTS Queue   │
                    │ & Cleanup   │     │             │
                    └─────────────┘     └─────────────┘
```

**AI Generation Pipeline:**
1. **Trigger Event**: `auto_generate_dj_intro()` function called when track is ready
2. **Flask API Call**: HTTP request to `/api/dj-now` endpoint
3. **AI Processing**: Multi-tier fallback system:
   - Tier 1: OpenAI (gpt-4o-mini, gpt-3.5-turbo)
   - Tier 2: Ollama local models (llama3.2:3b, llama3.2:1b, phi3:mini)  
   - Tier 3: Ollama alternatives (mistral:7b, gemma:2b)
   - Tier 4: Template-based fallback
4. **Text-to-Speech**: Convert AI text to audio using XTTS/ElevenLabs/Piper
5. **Audio Enqueuing**: Add generated audio to TTS priority queue

### 4. Frontend Data Flow

```
Browser Request
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ /api/track- │────▶│ Telnet Call │────▶│ Live        │
│ check       │     │ to          │     │ Metadata    │
│ (15s poll)  │     │ Liquidsoap  │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Track ID    │     │ Parse       │     │ Return      │
│ Changed?    │     │ Sections    │     │ JSON        │
└─────────────┘     └─────────────┘     └─────────────┘
       │                    │                    │
    No │ Yes                ▼                    ▼
       │   ▼         ┌─────────────┐     ┌─────────────┐
       │ ┌─────────────┐ │ Current &   │     │ Frontend    │
       │ │ Full Update │ │ Next Track  │     │ Display     │
       │ │ UI          │ │ Data        │     │ Update      │
       │ └─────────────┘ └─────────────┘     └─────────────┘
       │
       ▼
┌─────────────┐
│ No Action   │
│ (Efficient) │
└─────────────┘
```

**Smart Polling Strategy:**
1. **Periodic Check**: Frontend polls `/api/track-check` every 15 seconds
2. **Change Detection**: Compare current `track_id` with previous value  
3. **Minimal Update**: Only refresh UI when track actually changes
4. **Single Telnet Call**: Get both current and next track data in one request
5. **Graceful Fallback**: Show cached data if telnet unavailable

## Component Architecture 🏗️

### Core Services

**ai-radio.service** (Liquidsoap in Docker)
- Manages audio streaming and queue processing
- Handles music playback and TTS integration  
- Provides telnet interface for metadata access
- Outputs stream to Icecast server

**ai-dj-ui.service** (Flask Web Application)
- REST API server for frontend communication
- Metadata caching and processing
- AI commentary generation coordination
- Web interface hosting

**ai-metadata-daemon.service** (Metadata Caching)
- Periodic metadata updates to cache files
- Prevents telnet storms through API indirection
- Maintains `now.json` and `next.json` files

**ai-radio-watchdog.service** (Health Monitoring)
- Monitors telnet connectivity health
- Automatic gentle recovery procedures
- Prevents stream interruption during telnet issues

### Data Storage

**JSON Cache Files**
- `/opt/ai-radio/now.json` - Current track metadata
- `/opt/ai-radio/cache/next_metadata.json` - Upcoming tracks  
- `/opt/ai-radio/play_history.json` - Historical play data

**Audio Files**
- `/opt/ai-radio/tts/` - Generated TTS commentary audio
- `/mnt/music/` - Master music library storage
- `/opt/ai-radio/voices/` - TTS voice model samples

**Configuration**
- `/opt/ai-radio/library_clean.m3u` - Music playlist
- `/opt/ai-radio/dj_settings.json` - AI prompts and settings
- `/opt/ai-radio/radio.liq` - Liquidsoap configuration

### API Endpoints

**Real-time Data**
- `GET /api/track-check` - Optimized polling with current + next track info
- `GET /api/now` - Current playing track metadata
- `GET /api/next` - Upcoming tracks in queue
- `GET /api/health` - System health and telnet connectivity status

**Control & Management**  
- `POST /api/enqueue` - Add TTS audio to queue (telnet-free)
- `POST /api/dj-next` - Trigger AI commentary generation
- `POST /api/skip` - Skip current track
- `GET /api/history` - Play history retrieval

## Telnet Storm Prevention Architecture 🛡️

### Problem
Multiple components making frequent telnet connections to Liquidsoap caused connection flooding, resulting in:
- Audio stream buzzing/distortion
- System instability every 2-3 songs
- Metadata sync failures

### Solution Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Metadata Daemon │────│ Flask API       │────│ Single Telnet   │
│ (No Telnet)     │    │ (Controlled)    │    │ Connection      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │ HTTP Calls            │ Minimal Calls         │ Live Data
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ TTS Scripts     │────│ Cache System    │────│ Liquidsoap      │
│ (No Telnet)     │    │ (JSON Files)    │    │ (Streaming)     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │ API Calls             │ File I/O              │ Audio Stream
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Frontend UI     │────│ Browser Cache   │────│ Icecast Server  │
│ (Smart Polling) │    │ (15s intervals) │    │ (Public Stream) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

**Key Improvements:**
1. **Centralized Telnet**: Only Flask API makes telnet calls
2. **API Indirection**: All components use HTTP APIs instead of direct telnet
3. **Smart Polling**: Frontend polls only when tracks change
4. **Gentle Recovery**: Watchdog tries soft fixes before restarting
5. **Stream Continuity**: Never interrupt audio during metadata issues

## Performance Characteristics ⚡

### Telnet Usage (Before vs After)

**Before (Storm Conditions):**
- Metadata daemon: 3+ calls every 5 seconds = 720+ calls/hour
- TTS scripts: 2-4 calls per track = 120+ calls/hour  
- Frontend requests: Variable based on user activity
- **Total**: 800+ telnet calls/hour → Connection flooding

**After (Optimized):**
- Flask API: 1 call every 15 seconds = 240 calls/hour
- All other components: 0 telnet calls (HTTP only)
- **Total**: 240 telnet calls/hour → Zero storms

### Resource Efficiency
- **67% reduction** in telnet connection overhead
- **Zero stream interruptions** during metadata sync
- **Real-time accuracy** with minimal resource usage
- **Automatic recovery** from connectivity issues

This architecture ensures reliable 24/7 streaming with AI-powered commentary while maintaining system stability and preventing connection storms.