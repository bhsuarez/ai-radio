#!/usr/bin/env python3
import os, json, socket, time, hashlib, io, re, subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
try:
    import requests
except Exception:
    requests = None
from urllib.parse import quote

# ── Config ──────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# File paths
NOW_JSON = "/opt/ai-radio/now.json"
NOW_TXT = "/opt/ai-radio/nowplaying.txt"
HISTORY_FILE = "/opt/ai-radio/play_history.json"
TTS_DIR = "/opt/ai-radio/tts"
LOG_DIR = "/opt/ai-radio/logs"

# Icecast config
ICECAST_STATUS = "http://icecast.zorro.local:8000/status-json.xsl"
MOUNT = "/stream.mp3"

# TTS config
VOICE = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"

# Cache and limits
COVER_CACHE = Path("/opt/ai-radio/cache/covers")
COVER_CACHE.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 100
DEDUP_WINDOW_MS = 60_000

# Create directories
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(TTS_DIR, exist_ok=True)

# ANSI regex for cleaning terminal output
ANSI = re.compile(r'\x1B\[[0-9;?]*[ -/]*[@-~]')

# Optional dependencies
_MUTAGEN_OK = True
try:
    from mutagen import File as MutaFile
except Exception:
    _MUTAGEN_OK = False

try:
    from PIL import Image
except Exception:
    Image = None

app = Flask(__name__)

# ── In-memory state ─────────────────────────────────────────────────────────
HISTORY = []          # newest first
UPCOMING = []         # future tracks from liquidsoap

# ── Core Helper Functions ───────────────────────────────────────────────────
def telnet_cmd(cmd: str, timeout=5) -> str:
    """Send command to Liquidsoap telnet interface"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((TELNET_HOST, TELNET_PORT))
        s.sendall((cmd + "\n").encode())
        
        chunks = []
        while True:
            try:
                b = s.recv(65535)
                if not b:
                    break
                chunks.append(b)
            except socket.timeout:
                break
        s.close()
        
        return (b"".join(chunks).decode(errors="ignore") or "").strip()
    except Exception as e:
        print(f"Telnet error: {e}")
        return ""

def parse_liquidsoap_metadata(raw_output: str) -> dict:
    """Parse Liquidsoap telnet metadata output"""
    try:
        lines = raw_output.split('\n')
        current_track = {}
        in_current = False
        
        for line in lines:
            line = line.strip()
            if line == "--- 1 ---":  # Current track section
                in_current = True
                continue
            elif line.startswith("--- ") and line != "--- 1 ---":
                in_current = False
                continue
            elif in_current and "=" in line:
                key, value = line.split("=", 1)
                current_track[key.strip()] = value.strip().strip('"')
        
        return current_track
    except Exception as e:
        print(f"Error parsing liquidsoap metadata: {e}")
        return {}

def get_icecast_status() -> dict:
    """Get current track from Icecast status"""
    try:
        if not requests:
            return {}
            
        r = requests.get(ICECAST_STATUS, timeout=3)
        data = r.json()["icestats"]["source"]
        
        if isinstance(data, list):
            data = next((s for s in data if s.get("listenurl", "").endswith(MOUNT)), None)
        
        if not data:
            return {}
            
        # Parse artist - title from Icecast
        raw_title = data.get("title") or data.get("song", "")
        if " - " in raw_title:
            artist, title = raw_title.split(" - ", 1)
            return {
                "title": title.strip(),
                "artist": artist.strip(),
                "listeners": data.get("listeners", 0),
                "stream_start": data.get("stream_start_iso"),
                "source": "icecast"
            }
        else:
            return {
                "title": raw_title,
                "artist": "",
                "listeners": data.get("listeners", 0),
                "stream_start": data.get("stream_start_iso"),
                "source": "icecast"
            }
    except Exception as e:
        print(f"Icecast status error: {e}")
        return {}

def read_now_playing() -> dict:
    """Get current track metadata from best available source"""
    data = {}

    # 1. Try JSON file first (most reliable)
    if os.path.exists(NOW_JSON):
        try:
            with open(NOW_JSON, "r") as f:
                j = json.load(f)
            if isinstance(j, dict):
                data.update(j)
                data["source"] = "json_file"
        except Exception as e:
            print(f"Error reading {NOW_JSON}: {e}")

    # 2. Try text file if no JSON or incomplete data
    if os.path.exists(NOW_TXT) and not (data.get("title") and data.get("artist")):
        try:
            with open(NOW_TXT, "r") as f:
                raw = f.read().strip()
            
            if "=" in raw:
                # key=value format
                for line in raw.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data.setdefault(k.strip(), v.strip().strip('"'))
            elif " - " in raw and "\n" not in raw:
                # "Artist - Title" format
                artist, title = raw.split(" - ", 1)
                data.setdefault("artist", artist.strip())
                data.setdefault("title", title.strip())
                
            data["source"] = "text_file"
        except Exception as e:
            print(f"Error reading {NOW_TXT}: {e}")

    # 3. Try Liquidsoap telnet if still incomplete
    if not (data.get("title") and data.get("artist")):
        try:
            raw = telnet_cmd("output.icecast.metadata")
            if raw:
                telnet_data = parse_liquidsoap_metadata(raw)
                if telnet_data:
                    data.update({
                        "title": telnet_data.get("title") or data.get("title") or "Unknown",
                        "artist": telnet_data.get("artist") or data.get("artist") or "Unknown",
                        "album": telnet_data.get("album") or data.get("album", ""),
                        "source": "liquidsoap_telnet"
                    })
        except Exception as e:
            print(f"Liquidsoap telnet error: {e}")

    # 4. Fallback to Icecast status
    if not (data.get("title") and data.get("artist")):
        icecast_data = get_icecast_status()
        if icecast_data:
            data.update(icecast_data)

    # Set defaults
    data.setdefault("title", "Unknown")
    data.setdefault("artist", "Unknown")
    data.setdefault("album", "")
    data.setdefault("source", "fallback")
    
    return data

def load_history():
    """Load play history from file"""
    global HISTORY
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                HISTORY[:] = json.load(f)
        else:
            HISTORY[:] = []
    except Exception as e:
        print(f"Error loading history: {e}")
        HISTORY[:] = []

def save_history():
    """Save play history to file"""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(HISTORY[:MAX_HISTORY], f, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")

def push_event(ev: dict):
    """Add event to history with deduplication"""
    now_ms = int(time.time() * 1000)
    
    # Normalize timestamp
    if isinstance(ev.get("time"), (int, float)):
        t = int(ev["time"])
        if t < 10_000_000_000:  # seconds → ms
            ev["time"] = t * 1000
    else:
        ev["time"] = now_ms

    # Normalize song fields
    if ev.get("type") == "song":
        title = (ev.get("title") or "").strip()
        artist = (ev.get("artist") or "").strip()
        filename = (ev.get("filename") or "")
        
        # Extract from filename if missing
        if not title and filename:
            m = re.search(r'([^/\\]+?)\s*-\s*([^/\\]+?)\.(mp3|flac|m4a|wav)$', filename, re.I)
            if m:
                artist = artist or m.group(1)
                title = title or m.group(2)
                
        ev["artist"] = artist or "Unknown Artist"
        ev["title"] = title or "Unknown"

    # Simple deduplication
    if HISTORY:
        last = HISTORY[0]
        if ev.get("type") == "song" and last.get("type") == "song":
            same_track = (
                (ev.get("title") or "") == (last.get("title") or "") and
                (ev.get("artist") or "") == (last.get("artist") or "")
            )
            if same_track and (now_ms - int(last.get("time", now_ms))) < DEDUP_WINDOW_MS:
                return  # Skip duplicate
                
        if ev.get("type") == "dj" and last.get("type") == "dj":
            if ((ev.get("text") or "") == (last.get("text") or "") and
                (now_ms - int(last.get("time", now_ms))) < 5000):
                return  # Skip duplicate DJ

    HISTORY.insert(0, ev)
    del HISTORY[MAX_HISTORY:]
    save_history()

def synthesize_with_elevenlabs(text, output_path):
    """Synthesize speech using ElevenLabs API"""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    model = os.getenv("ELEVENLABS_MODEL", "eleven_monolingual_v1")
    
    if not api_key:
        return False
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    data = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.5,
            "style": 0.0,
            "use_speaker_boost": True
        }
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=30)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        return True
    except Exception as e:
        print(f"ElevenLabs error: {e}")
        return False

# ── Album Art Functions ─────────────────────────────────────────────────────
def _first_tag(v):
    """Extract first tag value from mutagen"""
    try:
        if v is None: return None
        if isinstance(v, list): return str(v[0]) if v else None
        if hasattr(v, "text"): return str(v.text[0]) if getattr(v, "text", []) else None
        return str(v)
    except Exception:
        return None

def _fetch_online_cover(artist, album, title, size=600, timeout=6):
    """Fetch album art from online sources"""
    if not requests:
        return None
        
    headers = {"User-Agent": "AI-Radio/1.0"}

    # Try iTunes API
    try:
        term = " ".join([x for x in [artist, album or title] if x]).strip()
        if term:
            r = requests.get(
                "https://itunes.apple.com/search",
                params={"term": term, "entity": "album", "limit": 1},
                headers=headers, timeout=timeout
            )
            if r.ok:
                js = r.json()
                if js.get("resultCount"):
                    url = js["results"][0].get("artworkUrl100")
                    if url:
                        url = re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url)
                        img = requests.get(url, headers=headers, timeout=timeout)
                        if img.ok and img.content:
                            return img.content, "image/jpeg"
    except Exception as e:
        print(f"iTunes art fetch error: {e}")

    return None

# ── Routes ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename)

@app.route("/tts/<path:filename>")
def serve_tts(filename):
    """Serve TTS audio files"""
    return send_from_directory(TTS_DIR, filename)

@app.route("/api/now")
def api_now():
    """Get current playing track"""
    try:
        now_data = read_now_playing()
        
        # Add timing info if available from Icecast
        icecast_data = get_icecast_status()
        if icecast_data.get("listeners") is not None:
            now_data["listeners"] = icecast_data["listeners"]
        if icecast_data.get("stream_start"):
            now_data["stream_start"] = icecast_data["stream_start"]
            
        return jsonify(now_data)
    except Exception as e:
        print(f"/api/now error: {e}")
        return jsonify({
            "title": "Unknown",
            "artist": "Unknown",
            "error": str(e)
        }), 200

@app.route("/api/next")
def api_next():
    """Get upcoming tracks"""
    try:
        # Try to get next tracks from liquidsoap
        raw = telnet_cmd("request.queue")
        upcoming_tracks = []
        
        if raw:
            # Parse liquidsoap queue output
            lines = raw.split('\n')
            for line in lines:
                if '.mp3' in line or '.flac' in line or '.m4a' in line:
                    # Extract filename and try to parse artist/title
                    filename = line.strip()
                    if filename:
                        # Try to extract artist/title from filename
                        basename = os.path.basename(filename)
                        name_no_ext = os.path.splitext(basename)[0]
                        
                        if ' - ' in name_no_ext:
                            artist, title = name_no_ext.split(' - ', 1)
                            upcoming_tracks.append({
                                "title": title.strip(),
                                "artist": artist.strip(),
                                "filename": filename,
                                "type": "song"
                            })
                        else:
                            upcoming_tracks.append({
                                "title": name_no_ext,
                                "artist": "Unknown",
                                "filename": filename,
                                "type": "song"
                            })
        
        return jsonify(upcoming_tracks[:10])  # Limit to 10 upcoming tracks
    except Exception as e:
        print(f"/api/next error: {e}")
        return jsonify([])

@app.route("/api/history")
def api_history():
    """Get play history"""
    try:
        # Return recent history, newest first
        history_items = []
        for item in HISTORY[:60]:  # Last 60 items
            normalized = {
                "type": item.get("type", "song"),
                "time": item.get("time", int(time.time() * 1000)),
                "title": item.get("title", "Unknown"),
                "artist": item.get("artist", "Unknown"),
                "album": item.get("album", ""),
                "filename": item.get("filename", ""),
            }
            
            if item.get("type") == "dj":
                normalized.update({
                    "text": item.get("text", ""),
                    "audio_url": item.get("audio_url")
                })
            else:
                # Add artwork URL for songs
                if item.get("filename"):
                    normalized["artwork_url"] = f"/api/cover?file={quote(item['filename'])}"
                else:
                    normalized["artwork_url"] = "/static/station-cover.jpg"
                    
            history_items.append(normalized)
            
        return jsonify(history_items)
    except Exception as e:
        print(f"/api/history error: {e}")
        return jsonify([])

@app.route("/api/tts_queue", methods=["GET"])
def api_tts_queue_get():
    """Get recent DJ events for UI compatibility"""
    try:
        dj_events = [e for e in HISTORY if e.get("type") == "dj"][:5]
        return jsonify(dj_events)
    except Exception as e:
        return jsonify([])

@app.route("/api/tts_queue", methods=["POST"])
def api_tts_queue_post():
    """Queue TTS message"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        text = (data.get("text") or "").strip()
        
        if not text:
            return jsonify({"ok": False, "error": "No text provided"}), 400
            
        timestamp = int(time.time())
        txt_path = os.path.join(TTS_DIR, f"dj_{timestamp}.txt")
        
        with open(txt_path, "w") as f:
            f.write(text + "\n")
            
        # Add to timeline
        push_event({
            "type": "dj",
            "text": text,
            "audio_url": None,
            "time": int(time.time() * 1000),
        })
        
        return jsonify({"ok": True, "queued": os.path.basename(txt_path)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/skip", methods=["POST"])
def api_skip():
    """Skip current track"""
    try:
        result = telnet_cmd("icecast.output.skip")
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/log_event", methods=["POST"])
def api_log_event():
    """Log an event to history"""
    try:
        data = request.get_json(force=True) or {}
        push_event(data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/dj-now", methods=["POST"])
def api_dj_now():
    """Generate and queue DJ intro for current track"""
    try:
        timestamp = int(time.time())
        
        # Get current track info
        track_data = read_now_playing()
        title = track_data.get("title", "Unknown")
        artist = track_data.get("artist", "Unknown")
        
        print(f"Generating DJ line for: {title} by {artist}")
        
        # Generate DJ line using script
        try:
            result = subprocess.run(
                ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist],
                capture_output=True, text=True, timeout=35
            )
            
            if result.returncode == 0 and result.stdout.strip():
                dj_line = ANSI.sub('', result.stdout.strip())
            else:
                dj_line = f"That was '{title}' by {artist}."
                print(f"DJ script failed, using fallback: {dj_line}")
        except Exception as e:
            dj_line = f"That was '{title}' by {artist}."
            print(f"DJ script error: {e}, using fallback")

        # Synthesize TTS
        audio_url = None
        mp3_path = os.path.join(TTS_DIR, f"intro_{timestamp}.mp3")
        
        # Try ElevenLabs first
        if synthesize_with_elevenlabs(dj_line, mp3_path):
            audio_url = f"/tts/intro_{timestamp}.mp3"
            print("ElevenLabs synthesis successful")
        else:
            # Fallback to Piper
            try:
                wav_path = os.path.join(TTS_DIR, f"intro_{timestamp}.wav")
                piper_result = subprocess.run(
                    ["piper", "--model", VOICE, "--output_file", wav_path],
                    input=dj_line.encode("utf-8"),
                    capture_output=True,
                    timeout=30
                )
                
                if piper_result.returncode == 0:
                    # Convert to MP3 if possible
                    try:
                        subprocess.run([
                            "ffmpeg", "-nostdin", "-y", "-i", wav_path,
                            "-codec:a", "libmp3lame", "-q:a", "3", mp3_path
                        ], capture_output=True, timeout=15, check=True)
                        audio_url = f"/tts/intro_{timestamp}.mp3"
                    except:
                        audio_url = f"/tts/intro_{timestamp}.wav"
                    print("Piper synthesis successful")
            except Exception as e:
                print(f"Piper synthesis failed: {e}")

        # Push to Liquidsoap if we have audio
        if audio_url:
            try:
                audio_file = audio_url.replace('/tts/', '')
                full_path = os.path.join(TTS_DIR, audio_file)
                
                subprocess.run([
                    "nc", "127.0.0.1", "1234"
                ], input=f"tts.push {full_path}\nquit\n".encode(),
                capture_output=True, timeout=5)
                print(f"Pushed to Liquidsoap: {full_path}")
            except Exception as e:
                print(f"Liquidsoap push failed: {e}")

        # Add to timeline
        push_event({
            "type": "dj",
            "text": dj_line,
            "audio_url": audio_url,
            "time": int(time.time() * 1000),
        })

        return jsonify({
            "ok": True,
            "queued_text": dj_line,
            "audio_url": audio_url
        })
        
    except Exception as e:
        print(f"DJ generation error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/cover")
def api_cover():
    """Get album artwork for a file"""
    fpath = request.args.get("file", "")
    default_cover = os.path.join(BASE_DIR, "static", "station-cover.jpg")
    
    def send_default():
        if os.path.exists(default_cover):
            return send_file(default_cover, mimetype="image/jpeg", conditional=True)
        return abort(404)

    if not fpath or not os.path.isabs(fpath) or not os.path.exists(fpath):
        return send_default()

    # Check cache
    key = hashlib.sha1(fpath.encode("utf-8")).hexdigest()
    cache_jpg = COVER_CACHE / f"{key}.jpg"
    cache_png = COVER_CACHE / f"{key}.png"
    
    if cache_jpg.exists():
        return send_file(cache_jpg, mimetype="image/jpeg", conditional=True)
    if cache_png.exists():
        return send_file(cache_png, mimetype="image/png", conditional=True)

    data = None
    mime = None

    # 1. Try embedded art
    if _MUTAGEN_OK:
        try:
            audio = MutaFile(fpath)
            if audio and getattr(audio, "tags", None):
                # MP3 APIC
                try:
                    from mutagen.id3 import APIC
                    for _, v in audio.tags.items():
                        if isinstance(v, APIC):
                            data, mime = v.data, (v.mime or "image/jpeg")
                            break
                except:
                    pass
                
                # FLAC picture
                if data is None:
                    try:
                        from mutagen.flac import FLAC
                        if isinstance(audio, FLAC) and audio.pictures:
                            pic = audio.pictures[0]
                            data, mime = pic.data, (pic.mime or "image/jpeg")
                    except:
                        pass
        except Exception as e:
            print(f"Embedded art extraction error: {e}")

    # 2. Try folder images
    if data is None:
        folder = os.path.dirname(fpath)
        for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png"):
            img_path = os.path.join(folder, name)
            if os.path.exists(img_path):
                try:
                    with open(img_path, "rb") as f:
                        data = f.read()
                    mime = "image/png" if name.endswith(".png") else "image/jpeg"
                    break
                except:
                    continue

    # 3. Try online lookup
    if data is None and _MUTAGEN_OK:
        try:
            audio = MutaFile(fpath)
            artist = album = title = None
            
            if audio and getattr(audio, "tags", None):
                title = _first_tag(audio.tags.get("title")) or _first_tag(audio.tags.get("TIT2"))
                artist = _first_tag(audio.tags.get("artist")) or _first_tag(audio.tags.get("TPE1"))
                album = _first_tag(audio.tags.get("album")) or _first_tag(audio.tags.get("TALB"))
            
            if not artist or not title:
                # Try to extract from path
                parts = os.path.normpath(fpath).split(os.sep)
                if len(parts) >= 3:
                    album = album or parts[-2]
                    artist = artist or parts[-3]
                    title = title or os.path.splitext(os.path.basename(fpath))[0]

            fetched = _fetch_online_cover(artist, album, title)
            if fetched:
                data, mime = fetched
        except Exception as e:
            print(f"Online art fetch error: {e}")

    # Cache and return
    if data is None:
        return send_default()

    try:
        ext = ".jpg" if "jpeg" in (mime or "").lower() else ".png"
        cache_file = COVER_CACHE / f"{key}{ext}"
        
        with open(cache_file, "wb") as f:
            f.write(data)
            
        return send_file(cache_file, 
                        mimetype="image/jpeg" if ext == ".jpg" else "image/png",
                        conditional=True)
    except Exception as e:
        print(f"Cache write error: {e}")
        return send_default()

# ── Startup ─────────────────────────────────────────────────────────────────
load_history()

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=True)