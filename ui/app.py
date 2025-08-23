#!/usr/bin/env python3
import os, json, socket, time, html, hashlib, io, re, requests, subprocess, threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort, Response
from urllib.parse import quote
from urllib.parse import unquote, unquote_plus
from contextlib import closing
from collections import deque
import shlex

HISTORY_PATH = "/opt/ai-radio/play_history.json"

ICECAST_STATUS = "http://icecast.zorro.network:8000/status-json.xsl"
MOUNT = "/stream.mp3"

MUSIC_ROOTS = ["/mnt/music", "/mnt/music/media", "/mnt/music/Music"]
def _is_allowed_path(p: str) -> bool:
    return any(os.path.commonpath([p, root]) == root for root in MUSIC_ROOTS)

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

LS_HOST = os.environ.get("QUEUE_HOST", "127.0.0.1")
LS_PORT = int(os.environ.get("QUEUE_PORT", "1234"))

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))

NOW_JSON   = "/opt/ai-radio/now.json"
NOW_TXT    = "/opt/ai-radio/nowplaying.txt"

TTS_DIR    = "/opt/ai-radio/tts_queue"
TTS_FALLBACK_DIR = "/opt/ai-radio/tts"
GEN_SCRIPT = "/opt/ai-radio/gen_dj_clip.sh"

def _tts_root():
    return TTS_DIR if os.path.isdir(TTS_DIR) else TTS_FALLBACK_DIR

LOG_DIR    = "/opt/ai-radio/logs"
DJ_LOG     = os.path.join(LOG_DIR, "dj-now.log")

ANSI = re.compile(r'\x1B\[[0-9;?]*[ -/]*[@-~]')
TTS_DIR = "/opt/ai-radio/tts"
VOICE   = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"

COVER_CACHE = Path("/opt/ai-radio/cache/covers"); COVER_CACHE.mkdir(parents=True, exist_ok=True)

MAX_HISTORY = 100
DEDUP_WINDOW_MS = 60_000  # suppress exact duplicate events within 60s

os.makedirs(LOG_DIR, exist_ok=True)

# Optional dependencies (album art)
_MUTAGEN_OK = True
try:
    from mutagen import File as MutaFile
except Exception:
    _MUTAGEN_OK = False

try:
    from PIL import Image
except Exception:
    Image = None  # cover fallback will be used

app = Flask(__name__)

# ── In-memory state ─────────────────────────────────────────────
HISTORY: list = []   # keep this line
HIST_FILE = "/opt/ai-radio/history.json"  # adjust to your path
HISTORY_FILE = "/opt/ai-radio/play_history.json"
HISTORY_LOCK = threading.Lock()
UPCOMING = []
MAX_HISTORY = 300
NEXT_CACHE = Path("/opt/ai-radio/next.json")

# Track history is now handled by metadata_daemon.py

# Cover art cache - maps (artist, album) -> cover_url or None
_cover_cache = {}
_cover_cache_lock = threading.Lock()

HISTORY = deque(maxlen=400)
_last_now_key = None
_last_history_key = None 
_last_now_payload = None

# DJ generation throttling
_dj_generation_lock = threading.Lock()
_last_dj_generation = 0
_last_dj_track_key = ""
DJ_GENERATION_COOLDOWN = 60  # seconds between generations - increased to prevent connection storm 

# ── Helpers ─────────────────────────────────────────────────────

def _history_key(ev: dict) -> str:
    """Stable key: prefer filename; else title|artist|album."""
    fn = (ev.get("filename") or "").strip()
    if fn:
        return f"f|{fn}"
    t = (ev.get("title") or "").strip().lower()
    a = (ev.get("artist") or "").strip().lower()
    b = (ev.get("album") or "").strip().lower()
    return f"t|{t}|{a}|{b}"

def _save_history_to_disk():
    tmp = HISTORY[-MAX_HISTORY:]
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump(tmp, f)
    except Exception:
        pass

def parse_kv_text(text: str) -> dict:
    """Parse lines like key=value"""
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out

def load_history():
    """Load prior history (if any) into the in‑memory list."""
    global HISTORY
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                with HISTORY_LOCK:
                    HISTORY.clear()
                    HISTORY.extend(data)
            else:
                with HISTORY_LOCK:
                    HISTORY.clear()
    except FileNotFoundError:
        with HISTORY_LOCK:
            HISTORY.clear()
    except Exception:
        # On any parse error, start fresh rather than crashing
        with HISTORY_LOCK:
            HISTORY.clear()

def _load_history_from_disk():
    global HISTORY
    if os.path.isfile(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    with HISTORY_LOCK:
                        HISTORY.clear()
                        HISTORY.extend(data[-MAX_HISTORY:])
        except Exception:
            # corrupt or empty; start clean
            with HISTORY_LOCK:
                HISTORY.clear()

def _append_history(ev: dict):
    """Append a single play event to history.jsonl (safe for multi-workers)."""
    ev = {
        "type": "song",
        "time": int(ev.get("time") or time.time()*1000),
        "title": ev.get("title") or "",
        "artist": ev.get("artist") or "",
        "album": ev.get("album") or "",
        "filename": ev.get("filename") or "",
        "artwork_url": ev.get("artwork_url") or "",
    }
    with open(HISTORY_PATH, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)

def save_history():
    """Persist the in‑memory history to disk."""
    with HISTORY_LOCK:
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(HISTORY[-500:], f, ensure_ascii=False)
        except Exception:
            pass  # don't crash UI on disk errors

def read_now() -> dict:
    """Best-effort live metadata: JSON → key=value txt → 'Artist - Title' → telnet."""
    data = {}

    # Preferred: JSON file
    if os.path.exists(NOW_JSON):
        try:
            with open(NOW_JSON, "r") as f:
                j = json.load(f)
            if isinstance(j, dict):
                data.update(j)
        except Exception:
            pass

    # Fallback: NOW_TXT (key=value OR "Artist - Title")
    if os.path.exists(NOW_TXT) and not (data.get("title") and data.get("artist")):
        try:
            with open(NOW_TXT, "r") as f:
                raw = f.read().strip()
            # key=value lines?
            if "=" in raw:
                kv = parse_kv_text(raw)
                for k in ("title","artist","album","artwork_url","started_at","duration","filename"):
                    if k not in data and k in kv:
                        data[k] = kv[k]
            # single line "Artist - Title"
            if not (data.get("title") and data.get("artist")) and " - " in raw and "\n" not in raw:
                artist, title = raw.split(" - ", 1)
                data.setdefault("artist", artist.strip())
                data.setdefault("title", title.strip())
        except Exception:
            pass

    # Fallback: Liquidsoap telnet - FIXED VERSION
    if not data.get("title"):
        try:
            # Use the standardized telnet command
            lines = _ls_lines("output.icecast.metadata")
            raw = '\n'.join(lines)
            print(f"DEBUG: Raw telnet response: {raw}")
            
            # Parse the response - look for "--- 1 ---" section (current track)
            lines = raw.split('\n')
            current_track = {}
            in_current = False
            
            for line in lines:
                line = line.strip()
                if line == "--- 1 ---":
                    in_current = True
                    continue
                elif line.startswith("--- ") and line != "--- 1 ---":
                    in_current = False
                    continue
                elif in_current and "=" in line:
                    key, value = line.split("=", 1)
                    current_track[key.strip()] = value.strip().strip('"')
            
            print(f"DEBUG: Parsed current track: {current_track}")
            
            if current_track:
                data["title"] = current_track.get("title") or data.get("title") or "Unknown title"
                data["artist"] = current_track.get("artist") or data.get("artist") or "Unknown artist"
                data["album"] = current_track.get("album") or data.get("album") or ""
                data["date"] = current_track.get("date") or ""
                data["filename"] = ""  # Not available from telnet
                data["artwork_url"] = data.get("artwork_url") or ""
                print(f"DEBUG: Final telnet metadata: {data}")
            else:
                print("DEBUG: No current track found in telnet response")
                
        except Exception as e:
            print(f"DEBUG: Telnet metadata failed: {e}")

    data.setdefault("title", "Unknown title")
    data.setdefault("artist", "Unknown artist")
    return data

def _read_history():
    try:
        with open(HISTORY_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []

def _write_history(rows):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f)
    os.replace(tmp, HISTORY_PATH)

def push_event(ev: dict):
    """Insert newest-first with light de-duplication and persist to disk."""
    now_ms = int(time.time() * 1000)

    # normalize timestamp to ms
    if isinstance(ev.get("time"), (int, float)):
        t = int(ev["time"])
        if t < 10_000_000_000:  # seconds → ms
            ev["time"] = t * 1000

    # Normalize basic song fields
    if ev.get("type") == "song":
        title = (ev.get("title") or "").strip()
        artist = (ev.get("artist") or "").strip()
        fn = (ev.get("filename") or "")
        if not title and fn:
            m = re.search(r'([^/\\]+?)\s*-\s*([^/\\]+?)\.(mp3|flac|m4a|wav)$', fn, re.I)
            if m:
                artist = artist or m.group(1)
                title  = title  or m.group(2)
        ev["artist"] = artist or "Unknown Artist"
        ev["title"]  = title  or "Unknown"

    # De-dupe against recent entries within window
    for recent in list(HISTORY)[:10]:  # Check last 10 entries
        if ev.get("type") == "song" and recent.get("type") == "song":
            same = (
                (ev.get("title") or "") == (recent.get("title") or "") and
                (ev.get("artist") or "") == (recent.get("artist") or "") and
                (ev.get("filename") or "") == (recent.get("filename") or "")
            )
            if same and (now_ms - int(recent.get("time", now_ms))) < DEDUP_WINDOW_MS:
                return
        if ev.get("type") == "dj" and recent.get("type") == "dj":
            if (ev.get("text") or "") == (recent.get("text") or "") and \
               (now_ms - int(recent.get("time", now_ms))) < 5000:
                return

    HISTORY.insert(0, ev)
    del HISTORY[MAX_HISTORY:]
    save_history()

def _first_tag(v):
    try:
        if v is None: return None
        if isinstance(v, list): return str(v[0]) if v else None
        if hasattr(v, "text"): return str(v.text[0]) if getattr(v, "text", []) else None
        return str(v)
    except Exception:
        return None

def _track_key(t: dict) -> str:
    """Stable key for change detection."""
    fn = (t.get("filename") or "").strip().lower()
    ti = (t.get("title") or "").strip().lower()
    ar = (t.get("artist") or "").strip().lower()
    al = (t.get("album") or "").strip().lower()
    return f"f:{fn}" if fn else f"t:{ti}|{ar}|{al}"

def _with_artwork(ev: dict) -> dict:
    """Ensure artwork_url if we know the file path."""
    out = dict(ev)
    fn = out.get("filename")
    if fn and not out.get("artwork_url"):
        out["artwork_url"] = f"/api/cover?file={urllib.parse.quote(fn)}"
    return out

def _maybe_log_previous(previous_now: dict):
    """Push the *previous* song into HISTORY when a new one starts."""
    if not previous_now:
        return
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),  # when we detected the handoff
        "title": previous_now.get("title") or "Unknown title",
        "artist": previous_now.get("artist") or "Unknown artist",
        "album": previous_now.get("album"),
        "filename": previous_now.get("filename"),
        "artwork_url": previous_now.get("artwork_url"),
    }
    HISTORY.appendleft(_with_artwork(ev))

def _fetch_online_cover(artist, album, title, size=600, timeout=6):
    if not requests:
        return None
    headers = {"User-Agent": "AI-Radio/1.0 (cover fetch)"}

    # 1) MusicBrainz → Cover Art Archive (album art)
    try:
        if artist and album:
            q = f'artist:"{artist}" AND release:"{album}"'
            r = requests.get(
                "https://musicbrainz.org/ws/2/release/",
                params={"query": q, "fmt": "json", "limit": 1},
                headers=headers, timeout=timeout
            )
            if r.ok:
                data = r.json()
                rel = (data.get("releases") or [{}])[0]
                mbid = rel.get("id")
                if mbid:
                    ca = requests.get(
                        f"https://coverartarchive.org/release/{mbid}/front",
                        headers=headers, timeout=timeout
                    )
                    if ca.ok and ca.content:
                        return ca.content, ca.headers.get("Content-Type", "image/jpeg")
    except Exception:
        pass

    # 2) iTunes fallback (good hit rate)
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
                        # upgrade 100x100 → 600x600
                        import re as _re
                        url = _re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url)
                        img = requests.get(url, headers=headers, timeout=timeout)
                        if img.ok and img.content:
                            return img.content, "image/jpeg"
    except Exception:
        pass

    return None

def _validate_track_obj(t):
    """Keep only the fields the frontend and enqueue scripts expect."""
    return {
        "title": t.get("title") or "",
        "artist": t.get("artist") or "",
        "album": t.get("album") or "",
        "filename": t.get("filename") or "",
        "artwork_url": t.get("artwork_url") or ""
    }

def synthesize_with_elevenlabs(text, output_path):
    """
    Synthesize speech using ElevenLabs API
    Returns True if successful, False otherwise
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    model = os.getenv("ELEVENLABS_MODEL", "eleven_monolingual_v1")
    
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY environment variable is required")
    
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
        
    except requests.exceptions.RequestException as e:
        print(f"ElevenLabs API error: {e}")
        return False

def _ls(cmd: str, timeout: float = 2.5):
    return _ls_lines(cmd, timeout)

_key_val = re.compile(r'^\s*([A-Za-z0-9_./-]+)="?(.*?)"?\s*$')

def _parse_metadata_block(text: str) -> dict:
    """
    Turn Liquidsoap key="val" lines into a dict.
    """
    out = {}
    for line in text.splitlines():
        m = _key_val.match(line)
        if not m: 
            continue
        k, v = m.group(1), m.group(2)
        # Unescape simple cases: Liquidsoap prints quotes as-is
        out[k] = v
    return out

def _ls_request_all() -> list[int]:
    """
    Return list of RIDs (ints) from `request.all`.
    Example raw: '78 79'
    """
    raw = _ls("request.all")
    # last line can contain rids or be empty; split on whitespace and keep ints
    rids = []
    for tok in raw.strip().split():
        try:
            rids.append(int(tok))
        except ValueError:
            pass
    return rids

def _ls_request_metadata(rid: int) -> dict:
    raw = _ls(f"request.metadata {rid}")
    return _parse_metadata_block(raw)

def _metadata_for_rid(rid: str | int):
    rid = str(rid).strip()
    meta_lines = _ls_lines(f"request.metadata {rid}")
    d = _parse_kv_lines(meta_lines)
    fname = d.get("filename") or d.get("initial_uri", "")
    # Liquidsoap often returns "file:///..." as initial_uri
    if fname.startswith("file://"):
        fname = fname[7:]
    out = {
        "title": d.get("title", "") or "",
        "artist": d.get("artist", "") or "",
        "album": d.get("album", "") or "",
        "filename": fname or "",
    }
    if out["filename"]:
        out["artwork_url"] = f"/api/cover?file={urllib.parse.quote(out['filename'])}"
    return out

def _ls_cmd(cmd: str, timeout: float = 2.5):
    return _ls_lines(cmd, timeout)

_kv_re = re.compile(r'([a-zA-Z0-9_]+)="([^"]*)"')


def _get_now_playing() -> dict | None:
    """
    Get current track metadata from daemon cache ONLY - no direct telnet to avoid storms.
    """
    import time as time_mod
    
    # ONLY read from daemon cache - no telnet fallback
    cache_file = "/opt/ai-radio/cache/now_metadata.json"
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
            
            # Accept cache up to 30 seconds old (daemon updates every 5s)
            cache_age = time_mod.time() - cached_data.get("cached_at", 0)
            if cache_age < 30:
                print(f"DEBUG: Using cached metadata (age: {cache_age:.1f}s)")
                return cached_data
            else:
                print(f"DEBUG: Cache is stale (age: {cache_age:.1f}s) - daemon may be down")
    except Exception as e:
        print(f"DEBUG: Error reading cache file: {e}")
    
    # NO TELNET FALLBACK - return placeholder to avoid connection storms
    print("DEBUG: Returning placeholder data - daemon cache unavailable")
    return {
        "title": "Stream Loading...",
        "artist": "AI Radio",
        "album": "",
        "filename": "",
        "time": int(time_mod.time() * 1000),
        "duration_ms": None,
        "elapsed_ms": None,
    }

def _parse_kv_block(text: str) -> dict:
    """Parse lines like key="val" into dict; ignores '--- n ---' separators."""
    out = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('---') or ln == 'END':
            continue
        if '=' not in ln:
            continue
        k, v = ln.split('=', 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        out[k.strip()] = v
    return out

_key_val_re = re.compile(r'([^=]+)="(.*)"$')

# Simple cache for now playing to avoid repeated slow Liquidsoap calls
_now_playing_cache = {"data": None, "timestamp": 0}
_CACHE_DURATION = 8  # seconds - increased to reduce liquidsoap calls


def _ls_kv(cmd: str, timeout=2.5) -> dict:
    """
    Run a liquidsoap command that returns key="value" lines (e.g. request.metadata N)
    and parse into a dict. Multiple lines are supported.
    """
    d = {}
    for line in _ls_cmd(cmd, timeout=timeout):
        m = _kv_re.match(line.strip())
        if m:
            k, v = m.group(1), m.group(2)
            d[k] = v
    return d

def _clean_file(path_or_uri: str) -> str:
    """Normalize LS filename/initial_uri into a filesystem path."""
    if not path_or_uri:
        return ""
    # LS sometimes gives file:///… URIs; strip scheme
    if path_or_uri.startswith("file://"):
        return path_or_uri[7:]
    return path_or_uri

def _art_url_from_file(fname: str) -> str:
    if not fname:
        return None
    return f"/api/cover?file={urllib.parse.quote(fname)}"

# Global lock to prevent connection storms to liquidsoap
_liquidsoap_conn_lock = threading.Lock()

def _ls_lines(cmd: str, timeout: float = 1.5):
    """
    Send a command to Liquidsoap's telnet interface and return a list of lines,
    with trailing 'END' removed. Uses a global lock to prevent connection storms.
    """
    with _liquidsoap_conn_lock:
        data = b""
        s = None
        try:
            s = socket.create_connection((LS_HOST, LS_PORT), timeout=timeout)
            s.settimeout(timeout)
            s.sendall((cmd + "\n").encode("utf-8"))
            s.sendall(b"quit\n")
            s.shutdown(socket.SHUT_WR)  # Signal we're done sending
            
            start = time.time()
            while time.time() - start < timeout:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    # LS ends most responses with a line 'END'
                    if b"\nEND" in data or data.rstrip().endswith(b"END"):
                        break
                except socket.timeout:
                    break
        except (socket.error, ConnectionRefusedError, OSError) as e:
            print(f"Liquidsoap connection error: {e}")
            return []
        finally:
            if s:
                try:
                    s.close()
                except:
                    pass

        lines = data.decode("utf-8", errors="ignore").splitlines()
        # Strip any prompt/blank and the END sentinel
        return [ln for ln in lines if ln.strip() and ln.strip() != "END"]

def _parse_kv_lines(lines):
    """
    Parse Liquidsoap key="val" lines into a dict.
    """
    out = {}
    for ln in lines:
        # format: key="value"
        if "=" in ln:
            k, v = ln.split("=", 1)
            v = v.strip().strip('"')
            out[k.strip()] = v
    return out

def _maybe_record_play(now_dict: dict):
    """
    Record the track currently playing into HISTORY once, when it changes.
    Expects keys: title/artist/album/filename/time/artwork_url (some may be empty).
    """
    global _last_history_key

    if not now_dict:
        return
    # Require that we have *something* identifying the track
    if not (now_dict.get("filename") or now_dict.get("title")):
        return

    k = _history_key(now_dict)
    if not k:
        return

    if _last_history_key == k:
        return  # same track; skip

    # Update last-key immediately to avoid racing double inserts
    _last_history_key = k

    # Build the event the UI expects
    ev = {
        "type": "song",
        "time": int(now_dict.get("time") or time.time() * 1000),
        "title": now_dict.get("title") or "",
        "artist": now_dict.get("artist") or "",
        "album": now_dict.get("album") or "",
        "filename": now_dict.get("filename") or "",
        # Precompute artwork_url so the frontend doesn’t need extra lookups
        "artwork_url": "",
    }
    fn = ev["filename"]
    if fn:
        ev["artwork_url"] = f"/api/cover?file={urllib.parse.quote(fn, safe='/:')}"

    with HISTORY_LOCK:
        HISTORY.append(ev)
        if len(HISTORY) > MAX_HISTORY:
            del HISTORY[:-MAX_HISTORY]
        _save_history_to_disk()

_last_fingerprint = {"title":"", "artist":"", "filename":""}

def _fingerprint(now: dict):
    return (
        (now.get("filename") or "").strip(),
        (now.get("title") or "").strip().lower(),
        (now.get("artist") or "").strip().lower(),
    )

def _scrobble_loop():
    global _last_fingerprint
    while True:
        try:
            now = _get_now_playing()  # must return dict with title/artist/filename/time/album
            if now and (now.get("title") or now.get("filename")):
                fp = _fingerprint(now)
                if fp and fp != tuple(_last_fingerprint.values()):
                    # new track → write to history
                    _append_history(now)
                    _last_fingerprint = {
                        "filename": fp[0], "title": fp[1], "artist": fp[2]
                    }
        except Exception as e:
            # keep it quiet but don’t crash the thread
            pass
        time.sleep(2)  # small poll; cheap because it calls your existing logic

def _history_add_song(now_dict):
    """Append a 'song' event to HISTORY from a /api/now object."""
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title": now_dict.get("title") or "",
        "artist": now_dict.get("artist") or "",
        "album": now_dict.get("album") or "",
        "filename": now_dict.get("filename") or "",
    }
    # give frontend a ready-to-use cover URL if we have a filename
    if ev["filename"]:
        ev["artwork_url"] = "/api/cover?file=" + urllib.parse.quote(ev["filename"])
    with HISTORY_LOCK:
        HISTORY.append(ev)
        # keep list bounded
        if len(HISTORY) > 500:
            del HISTORY[:-500]
    save_history()

_SCROBBLER_STARTED = False

def _scrobble_loop():
    """
    Polls current track and records transitions into HISTORY.
    A 'transition' happens when (title, artist) or filename changes.
    """
    last_key = None
    stable_key = None
    stable_since = 0.0

    while True:
        try:
            # You already have this function in your app:
            now = _get_now_playing()  # must return dict with title/artist/filename/album/…
        except Exception:
            now = None

        if now:
            # build a key that tolerates minor metadata issues
            t = (now.get("title") or "").strip().lower()
            a = (now.get("artist") or "").strip().lower()
            f = (now.get("filename") or "").strip().lower()
            key = f or f"{t}|{a}"

            # consider a track "stable" after 3 seconds with same key
            now_ts = time.time()
            if key == stable_key:
                # already stable; do nothing
                pass
            else:
                # key changed; start (or reset) stability timer
                stable_key = key
                stable_since = now_ts

            became_stable = (stable_key == key) and (now_ts - stable_since >= 3.0)
            if became_stable and key and key != last_key:
                # new stable track -> record it
                _history_add_song(now)
                last_key = key
        time.sleep(1.0)

def _start_scrobbler_once():
    global _SCROBBLER_STARTED
    if _SCROBBLER_STARTED:
        return
    _SCROBBLER_STARTED = True
    load_history()
    threading.Thread(target=_scrobble_loop, name="scrobble", daemon=True).start()

_start_scrobbler_once()


# ── Routes ──────────────────────────────────────────────────────

@app.route("/api/event")
def api_event():
    """Ingest events from Liquidsoap (announce_song/after_song)."""
    ev_type = request.args.get("type", "song")
    now_ms = int(time.time() * 1000)

    if ev_type == "song":
        row = {
            "type": "song",
            "time": now_ms,  # always stamp
            "title": request.args.get("title", "")[:512],
            "artist": request.args.get("artist", "")[:512],
            "album": request.args.get("album", "")[:512],
            "filename": request.args.get("filename", ""),
        }
    elif ev_type == "dj":
        row = {
            "type": "dj",
            "time": now_ms,
            "text": request.args.get("text", "")[:2000],
            "audio_url": request.args.get("audio_url"),
        }
    else:
        return jsonify({"ok": False, "error": "unknown type"}), 400

    hist = _read_history()
    
    # Simple deduplication: don't add if identical entry exists within last 30 seconds
    if ev_type == "song":
        track_key = f"{row['artist']}|{row['title']}"
        for recent in reversed(hist[-10:]):  # Check last 10 entries
            if (recent.get("type") == "song" and
                recent.get("time", 0) > now_ms - 30000 and  # within 30 seconds
                f"{recent.get('artist', '')}|{recent.get('title', '')}" == track_key):
                print(f"DEBUG: Skipping duplicate song entry: {track_key}")
                return jsonify({"ok": True, "skipped": "duplicate"})
    
    hist.append(row)
    _write_history(hist)
    return jsonify({"ok": True})

def _build_art_url(path: str) -> str:
    """Return a URL that always resolves to cover art (or your default)."""
    if path and os.path.isabs(path) and os.path.exists(path):
        return request.url_root.rstrip("/") + "/api/cover?file=" + quote(path)
    # no valid file → use your station default
    return request.url_root.rstrip("/") + "/static/station-cover.jpg"

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

def _get_cached_cover_url(artist, album, filename):
    """Get cover URL with caching and fallback to online lookup."""
    cache_key = (artist.lower().strip(), album.lower().strip()) if artist and album else None
    
    # Try cache first
    if cache_key:
        with _cover_cache_lock:
            if cache_key in _cover_cache:
                cached = _cover_cache[cache_key]
                if cached:
                    return cached
                elif cached is None:
                    # Previously failed, use file-based cover
                    return f"/api/cover?file={quote(filename)}" if filename else None
    
    # Return file-based cover URL for now, online lookup happens async
    if filename:
        return f"/api/cover?file={quote(filename)}"
    elif cache_key:
        # Mark for online lookup and return placeholder
        with _cover_cache_lock:
            _cover_cache[cache_key] = f"/api/cover/online?artist={quote(artist)}&album={quote(album)}"
        return _cover_cache[cache_key]
    
    return None

def _normalize_history_item(ev):
    """Map history file rows into what the UI expects."""
    ev_type = ev.get("type", "song")
    ts = (
        ev.get("time")
        or ev.get("played_at")
        or int(time.time() * 1000)
    )

    if ev_type == "dj":
        return {
            "type": "dj",
            "time": ts,
            "text": ev.get("text") or ev.get("dj_text") or "",
            "audio_url": ev.get("audio_url"),
        }

    # song
    fn = ev.get("filename") or ev.get("file") or ""
    artist = ev.get("artist") or ""
    album = ev.get("album") or ""
    
    # Use optimized cover URL lookup
    art_url = _get_cached_cover_url(artist, album, fn)

    return {
        "type": "song",
        "time": ts,
        "title": ev.get("title") or "",
        "artist": artist,
        "album": album,
        "filename": fn,
        "artwork_url": art_url,
    }

def _load_history(limit=60):
    try:
        with open(HISTORY_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        return []

    # take the newest items, normalize, sort desc by time, and dedupe
    rows = [_normalize_history_item(x) for x in data[-(limit * 3):]]
    rows.sort(key=lambda x: x.get("time", 0), reverse=True)

    seen = set()
    out = []
    for r in rows:
        # collapse duplicate songs by title+artist; keep all DJ lines
        if r["type"] == "song":
            key = (r.get("title", "").lower(), r.get("artist", "").lower())
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out

@app.get("/static/<path:fname>")
def static_file(fname):
    return send_from_directory("/opt/ai-radio/static", fname, conditional=True)

def _fix_dj_transcription(entry):
    """Fix DJ entries that have file paths instead of transcriptions (optimized)."""
    if entry.get("type") != "dj":
        return entry
    
    text = entry.get("text", "")
    
    # Quick path for already-correct entries
    if text and not text.startswith("/opt/ai-radio/tts/"):
        return entry
    
    entry_copy = entry.copy()
    
    # Check if text looks like a file path and needs transcript loading
    if text.startswith("/opt/ai-radio/tts/") and text.endswith(".mp3"):
        txt_file = text.replace('.mp3', '.txt')
        try:
            # Simple file check and read
            if os.path.isfile(txt_file):
                with open(txt_file, 'r', encoding='utf-8') as f:
                    actual_text = f.read().strip()
                if actual_text and not actual_text.startswith("/opt/ai-radio/tts/"):
                    entry_copy["text"] = actual_text
                else:
                    entry_copy["text"] = "AI DJ Commentary"
            else:
                entry_copy["text"] = "AI DJ Commentary"
        except Exception:
            entry_copy["text"] = "AI DJ Commentary"
    
    # Fix audio URL format
    audio_url = entry_copy.get("audio_url", "")
    if audio_url.startswith("/tts_queue/"):
        entry_copy["audio_url"] = audio_url.replace("/tts_queue/", "/tts/")
    elif not audio_url and text.startswith("/opt/ai-radio/tts/"):
        filename = os.path.basename(text)
        entry_copy["audio_url"] = f"/tts/{filename}"
    
    return entry_copy

@app.get("/api/history")
def api_history():
    """Optimized history endpoint with optional pagination."""
    # Check if frontend expects paginated response
    wants_pagination = 'limit' in request.args or 'offset' in request.args
    
    if wants_pagination:
        # New paginated format
        limit = min(int(request.args.get('limit', 50)), 200)  # Cap at 200
        offset = int(request.args.get('offset', 0))
        
        with HISTORY_LOCK:
            # Sort once, slice efficiently  
            sorted_history = sorted(HISTORY, key=lambda e: e.get("time", 0), reverse=True)
            page_items = sorted_history[offset:offset + limit]
            
            # Only process the items we're returning
            fixed_history = [_fix_dj_transcription(entry) for entry in page_items]
            
            return jsonify({
                "items": fixed_history,
                "total": len(HISTORY),
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < len(HISTORY)
            })
    else:
        # Legacy format - return first 60 items as array for backward compatibility
        with HISTORY_LOCK:
            sorted_history = sorted(HISTORY, key=lambda e: e.get("time", 0), reverse=True)
            limited_items = sorted_history[:60]  # Reasonable limit for legacy
            fixed_history = [_fix_dj_transcription(entry) for entry in limited_items]
            return jsonify(fixed_history)

@app.get("/api/now")
def api_now():
    now = _get_now_playing()
    if not now:
        return jsonify({"error": "No track info"}), 404
    return jsonify(now)

@app.get("/api/next")
def api_next():
    # Try to read from daemon cache first
    cache_file = "/opt/ai-radio/cache/next_metadata.json"
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
            
            # Check if cache is reasonably fresh (less than 30 seconds old)
            cache_age = time.time() - os.path.getmtime(cache_file)
            if cache_age < 30:
                print(f"DEBUG: Using cached next tracks (age: {cache_age:.1f}s)")
                return jsonify(cached_data)
            else:
                print(f"DEBUG: Next tracks cache is stale (age: {cache_age:.1f}s)")
    except Exception as e:
        print(f"DEBUG: Error reading next tracks cache: {e}")
    
    # NO TELNET FALLBACK - return empty array to avoid connection storms
    print("DEBUG: Next tracks cache unavailable - daemon may be down")
    return jsonify([])

@app.get("/api/just-played")
def api_just_played():
    """Get recently played tracks from metadata daemon cache"""
    try:
        count = int(request.args.get('count', 10))
        count = min(count, 50)  # Cap at 50
        
        # Try to read from daemon cache first
        cache_file = "/opt/ai-radio/cache/just_played.json"
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                    return jsonify(cached_data[:count])
        except Exception as e:
            print(f"Error reading just-played cache: {e}")
        
        # Fallback to existing history if cache not available
        with HISTORY_LOCK:
            song_history = [item for item in HISTORY if item.get("type") == "song"]
            return jsonify(song_history[:count])
            
    except Exception as e:
        app.logger.exception("just-played endpoint failed")
        return jsonify({"error": str(e)}), 500

# Serve the synthesized DJ audio files
@app.get("/tts_queue/<path:fname>")
def tts_audio(fname):
    root = _tts_root()
    full = os.path.join(root, fname)
    if not os.path.exists(full):
        abort(404)
    return send_from_directory(root, fname, conditional=True)

# Also serve TTS files from /tts/ path for compatibility
@app.get("/tts/<path:fname>")
def tts_audio_compat(fname):
    root = _tts_root()
    full = os.path.join(root, fname)
    if not os.path.exists(full):
        abort(404)
    return send_from_directory(root, fname, conditional=True)

# Parse filenames like: intro_<Artist>__<Title>__<unix_ts>.mp3 (your scripts’ pattern)
_TTS_PAT = re.compile(r'^(?:dj_|intro_)?(.+?)__(.+?)__(\d+)$')

def _parse_tts_name(path: str):
    base = os.path.basename(path)
    stem, _ = os.path.splitext(base)
    m = _TTS_PAT.match(stem)
    if not m:
        return {"title": "", "artist": "", "ts": None}
    artist = m.group(1).replace("_", " ").strip()
    title  = m.group(2).replace("_", " ").strip()
    try:
        ts = int(m.group(3)) * 1000  # ms
    except Exception:
        ts = None
    return {"title": title, "artist": artist, "ts": ts}

@app.get("/api/tts_queue")
def api_tts_queue():
    root = _tts_root()
    if not os.path.isdir(root):
        return jsonify([])

    # Only include files that have BOTH audio and transcript
    files = [f for f in os.listdir(root)
             if f.lower().endswith((".mp3", ".m4a", ".wav", ".ogg"))]

    events = []
    for f in files:
        audio_path = os.path.join(root, f)
        try:
            st = os.stat(audio_path)
        except FileNotFoundError:
            continue

        # Check if transcript exists
        txt_file = audio_path.replace('.mp3', '.txt').replace('.m4a', '.txt').replace('.wav', '.txt').replace('.ogg', '.txt')
        if not os.path.isfile(txt_file):
            print(f"DEBUG: Skipping {f} - no transcript file")
            continue
        
        # Verify audio file is complete (not currently being written)
        if f.endswith('_temp.wav') or st.st_size < 1000:  # Skip temp files or very small files
            continue

        meta = _parse_tts_name(audio_path)
        title  = meta["title"]
        artist = meta["artist"]
        when   = meta["ts"] or int(st.st_mtime * 1000)

        # Read transcript
        actual_transcript = None
        try:
            with open(txt_file, 'r', encoding='utf-8') as tf:
                actual_transcript = tf.read().strip()
        except Exception as e:
            print(f"DEBUG: Could not read transcript file {txt_file}: {e}")
            continue  # Skip this entry if we can't read the transcript

        # Only include if we have a valid transcript
        if actual_transcript and not actual_transcript.startswith("/opt/ai-radio/"):
            text = actual_transcript
        else:
            # Fallback text
            if title and artist:
                text = f"Coming up next: {title} by {artist}"
            elif title:
                text = f"Coming up: {title}"
            else:
                text = "AI DJ intro"

        # Verify audio URL will work
        audio_url = f"/tts_queue/{f}"
        
        events.append({
            "type": "dj",
            "time": when,
            "text": text,
            "audio_url": audio_url,
            "status": "ready",  # New field to track completion
            "transcript_file": txt_file,
            "audio_file": audio_path,
        })

    events.sort(key=lambda e: e["time"], reverse=True)
    return jsonify(events[:50])

@app.post("/api/tts_queue")
def tts_queue_post():
    try:
        data = request.get_json(force=True, silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "No text provided"}), 400
        os.makedirs(TTS_DIR, exist_ok=True)
        stamp = int(time.time())
        txt_path = os.path.join(TTS_DIR, f"dj_{stamp}.txt")
        with open(txt_path, "w") as f:
            f.write(text + "\n")
        push_event({
            "type": "dj",
            "text": text,
            "audio_url": None,
            "time": int(time.time() * 1000),
        })
        return jsonify({"ok": True, "queued": os.path.basename(txt_path)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/skip")
def api_skip():
    # DISABLED to prevent telnet storms
    return {"ok": False, "error": "Skip disabled to prevent connection storms"}, 503

@app.post("/api/log_event")
def log_event():
    data = request.get_json(force=True) or {}
    push_event(data)
    return {"ok": True}

@app.post("/api/cleanup-history")
def cleanup_history():
    """Clean up duplicate entries and fix broken DJ transcriptions."""
    try:
        with HISTORY_LOCK:
            # Remove exact duplicates
            seen = set()
            cleaned = []
            original_count = len(HISTORY)
            
            for entry in HISTORY:
                # Create a unique key for each entry
                if entry.get("type") == "song":
                    key = (entry.get("title", ""), entry.get("artist", ""), entry.get("filename", ""), entry.get("time", 0))
                else:
                    key = (entry.get("text", ""), entry.get("audio_url", ""), entry.get("time", 0))
                
                if key not in seen:
                    seen.add(key)
                    cleaned.append(entry)
            
            # Replace history with cleaned version
            HISTORY.clear()
            HISTORY.extend(cleaned)
            save_history()
            
        return jsonify({"ok": True, "removed": original_count - len(cleaned), "remaining": len(cleaned)})
    except Exception as e:
        app.logger.exception("cleanup failed")
        return jsonify({"error": str(e)}), 500

@app.post("/api/cleanup-tts")
def cleanup_tts():
    """Clean up orphaned TTS files and report status."""
    try:
        root = _tts_root()
        if not os.path.isdir(root):
            return jsonify({"error": "TTS directory not found"}), 404
            
        orphaned_txt = []
        orphaned_audio = []
        temp_files = []
        old_files = []
        current_time = time.time()
        
        # Find all files
        all_files = os.listdir(root)
        txt_files = {f for f in all_files if f.endswith('.txt')}
        audio_files = {f for f in all_files if f.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg'))}
        
        # Check for orphaned txt files (no corresponding audio)
        for txt in txt_files:
            base_name = os.path.splitext(txt)[0]
            has_audio = any(os.path.splitext(audio)[0] == base_name for audio in audio_files)
            if not has_audio:
                orphaned_txt.append(txt)
                
        # Check for orphaned audio files (no corresponding txt)
        for audio in audio_files:
            base_name = os.path.splitext(audio)[0]
            txt_name = base_name + '.txt'
            if txt_name not in txt_files and not audio.endswith('_temp.wav'):
                orphaned_audio.append(audio)
                
        # Find temp files and old files (>7 days)
        for f in all_files:
            if f.endswith('_temp.wav'):
                temp_files.append(f)
            else:
                file_path = os.path.join(root, f)
                try:
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > (7 * 24 * 3600):  # 7 days
                        old_files.append(f)
                except OSError:
                    pass
        
        # Optionally clean up based on query parameter
        if request.args.get('clean') == 'true':
            removed_count = 0
            for f in orphaned_txt + temp_files:
                try:
                    os.remove(os.path.join(root, f))
                    removed_count += 1
                except OSError:
                    pass
                    
            return jsonify({
                "ok": True,
                "cleaned": removed_count,
                "orphaned_txt": len(orphaned_txt),
                "temp_files": len(temp_files)
            })
        else:
            return jsonify({
                "ok": True,
                "orphaned_txt_files": len(orphaned_txt),
                "orphaned_audio_files": len(orphaned_audio), 
                "temp_files": len(temp_files),
                "old_files": len(old_files),
                "details": {
                    "orphaned_txt": orphaned_txt[:10],  # Show first 10
                    "orphaned_audio": orphaned_audio[:10],
                    "temp_files": temp_files[:10],
                    "old_files": old_files[:5]
                }
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _should_generate_dj_intro(artist, title):
    """Check if we should generate a DJ intro, preventing duplicates and overload"""
    global _last_dj_generation, _last_dj_track_key
    
    current_time = time.time()
    track_key = f"{artist}|{title}".lower()
    
    with _dj_generation_lock:
        # Check cooldown period
        if current_time - _last_dj_generation < DJ_GENERATION_COOLDOWN:
            print(f"DEBUG: DJ generation on cooldown (last: {current_time - _last_dj_generation:.1f}s ago)")
            return False
            
        # Check if same track
        if track_key == _last_dj_track_key:
            print(f"DEBUG: Same track as last generation: {track_key}")
            return False
            
        # Update throttling state
        _last_dj_generation = current_time
        _last_dj_track_key = track_key
        print(f"DEBUG: DJ generation allowed for: {track_key}")
        return True

# Global variable to track last TTS generation time and track
_last_tts_generation = {"time": 0, "track_key": "", "cooldown": 30}  # 30 second cooldown

@app.post("/api/dj-next")
def api_dj_next():
    """Generate DJ intro for the NEXT upcoming track, not current track"""
    os.makedirs(TTS_DIR, exist_ok=True)
    ts = int(time.time())

    # Check if called with URL parameters (from Liquidsoap auto-DJ)
    artist_param = request.args.get('artist')
    title_param = request.args.get('title')
    
    if artist_param and title_param:
        # Called from Liquidsoap with track info
        artist = artist_param
        title = title_param
        print(f"DEBUG: Using Liquidsoap auto-DJ parameters - Artist: '{artist}', Title: '{title}'")
        
        # Check throttling before proceeding
        if not _should_generate_dj_intro(artist, title):
            return jsonify({"ok": True, "skipped": "throttled", "reason": "generation throttled"}), 200
    else:
        # NO TELNET FALLBACK - get from cache only to prevent connection storms
        try:
            print("DEBUG: Getting next track from cache only")
            cache_file = "/opt/ai-radio/cache/next_metadata.json"
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    next_tracks = json.load(f)
                
                if next_tracks:
                    # Use first upcoming track
                    next_track = next_tracks[0]
                    title = next_track.get("title", "Unknown Title")
                    artist = next_track.get("artist", "Unknown Artist")
                    print(f"DEBUG: Next track from cache - Title: '{title}', Artist: '{artist}'")
                else:
                    print("DEBUG: No upcoming tracks in cache")
                    return jsonify({"ok": True, "skipped": "no_tracks_in_cache"}), 200
            else:
                print("DEBUG: Next tracks cache not available")
                return jsonify({"ok": True, "skipped": "cache_unavailable"}), 200
                
        except Exception as e:
            print(f"DEBUG: Error reading next track cache: {e}")
            return jsonify({"ok": False, "error": "Cache read error"}), 500

    # XTTS synthesis - FORCE CORRECT SPEAKER
    audio_url = None
    ai_text = f"Up next: {title} by {artist}."  # Default fallback text
    
    try:
        if os.getenv("USE_XTTS", "1") in ("1", "true", "True"):
            print("DEBUG: Generating XTTS for upcoming track")
            
            # Generate AI text first
            ai_env = os.environ.copy()
            ai_env["DJ_INTRO_MODE"] = "1"
            ai_cmd = ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist]
            
            try:
                ai_result = subprocess.run(ai_cmd, capture_output=True, text=True, timeout=30, env=ai_env)
                if ai_result.returncode == 0 and ai_result.stdout.strip():
                    ai_text = ai_result.stdout.strip()
                    # Remove any surrounding quotes that might have been added
                    if ai_text.startswith('"') and ai_text.endswith('"'):
                        ai_text = ai_text[1:-1]
                    print(f"DEBUG: Generated AI text: '{ai_text}'")
                else:
                    ai_text = f"Up next: {title} by {artist}."
                    print(f"DEBUG: AI generation failed, using fallback: '{ai_text}'")
            except Exception as e:
                ai_text = f"Up next: {title} by {artist}."
                print(f"DEBUG: AI generation error: {e}, using fallback: '{ai_text}'")
            
            # FORCE the correct speaker name - override any environment setting
            xtts_speaker = "Damien Black"  # Hardcode the correct value
            print(f"DEBUG: FORCING XTTS speaker to: '{xtts_speaker}' (ignoring environment)")
            
            # Generate timestamped filename
            output_filename = f"intro_{ts}.mp3"
            expected_output = os.path.join(TTS_DIR, output_filename)
            
            # Setup environment with FORCED speaker setting
            env = os.environ.copy()
            env["HOME"] = "/root"
            env["PYTHONPATH"] = "/opt/ai-radio/xtts-venv/lib/python3.11/site-packages"
            env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
            env["XTTS_SPEAKER"] = xtts_speaker  # Override the environment variable
            
            # Pass the AI text to prevent duplicate generation
            env["CUSTOM_TEXT"] = ai_text  # Pass our generated text to the script
            
            # Build command with explicit speaker parameter and custom mode to use our text
            cmd = ["/opt/ai-radio/dj_enqueue_xtts_ai.sh", artist, title, "en", xtts_speaker, "custom"]
            print(f"DEBUG: XTTS command: {cmd}")
            print(f"DEBUG: Environment XTTS_SPEAKER set to: '{env['XTTS_SPEAKER']}'")
            print(f"DEBUG: Environment CUSTOM_TEXT set to: '{env['CUSTOM_TEXT']}'")

            # Check if XTTS is already running to prevent resource conflicts
            try:
                running_xtts = subprocess.run(["pgrep", "-f", "tts_xtts.py"], capture_output=True, text=True)
                if running_xtts.returncode == 0:
                    print(f"DEBUG: XTTS already running (PID: {running_xtts.stdout.strip()}), skipping generation")
                    return jsonify({"ok": True, "skipped": "xtts_already_running"}), 200
            except Exception as e:
                print(f"DEBUG: Could not check for running XTTS: {e}")
            
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd="/opt/ai-radio")
            print(f"DEBUG: XTTS return code: {r.returncode}")
            
            if r.stdout:
                print(f"DEBUG: XTTS stdout: {r.stdout}")
            if r.stderr:
                print(f"DEBUG: XTTS stderr: {r.stderr}")
                
            # File detection logic
            candidate_file = None
            
            if r.returncode == 0:
                # Look for explicit file path in stdout (should be last line)
                output_lines = [line.strip() for line in r.stdout.strip().split('\n') if line.strip()]
                for line in reversed(output_lines):  # Check from last line backwards
                    if line.startswith('/') and line.endswith('.mp3') and os.path.isfile(line):
                        candidate_file = line
                        print(f"DEBUG: Found explicit file: {candidate_file}")
                        break
                
                # If no explicit path, check expected location
                if not candidate_file and os.path.isfile(expected_output):
                    candidate_file = expected_output
                    print(f"DEBUG: Found expected file: {candidate_file}")
                
                # Last resort: newest intro file
                if not candidate_file:
                    try:
                        import glob
                        pattern = os.path.join(TTS_DIR, "intro_*.mp3")
                        files = glob.glob(pattern)
                        if files:
                            files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                            newest = files[0]
                            if time.time() - os.path.getmtime(newest) < 30:
                                candidate_file = newest
                                print(f"DEBUG: Found recent intro file: {candidate_file}")
                    except Exception as e:
                        print(f"DEBUG: Error searching for intro files: {e}")
            
            if candidate_file and os.path.isfile(candidate_file):
                audio_url = f"/tts/{os.path.basename(candidate_file)}"
                print(f"DEBUG: XTTS SUCCESS: {audio_url}")
                
                # Save transcript text file for frontend display
                txt_file = candidate_file.replace('.mp3', '.txt')
                try:
                    with open(txt_file, 'w', encoding='utf-8') as f:
                        f.write(ai_text)
                    print(f"DEBUG: Saved transcript to: {txt_file}")
                except Exception as e:
                    print(f"DEBUG: Failed to save transcript: {e}")
                
                # Push to Liquidsoap TTS queue
                try:
                    print(f"DEBUG: Pushing to Liquidsoap TTS queue: {candidate_file}")
                    safe_ai_text = ai_text.replace('"', '\\"').replace("'", "\\'")
                    metadata = f'title="DJ Intro",artist="AI DJ",comment="{safe_ai_text}"'
                    push_cmd = f"tts.push annotate:{metadata}:{candidate_file}"
                    
                    print(f"DEBUG: Push command: {push_cmd}")
                    liq_result = subprocess.run(
                        ["nc", "127.0.0.1", "1234"],
                        input=f"{push_cmd}\nquit\n".encode(),
                        capture_output=True,
                        timeout=5
                    )
                    print(f"DEBUG: Liquidsoap push result: {liq_result.returncode}")
                    if liq_result.stdout:
                        print(f"DEBUG: Liquidsoap stdout: {liq_result.stdout.decode()}")
                    if liq_result.stderr:
                        print(f"DEBUG: Liquidsoap stderr: {liq_result.stderr.decode()}")
                except Exception as e:
                    print(f"DEBUG: Liquidsoap push failed: {e}")
            else:
                print(f"DEBUG: XTTS did not produce a usable audio file. Return code: {r.returncode}")
                print(f"DEBUG: Expected file: {expected_output}, exists: {os.path.isfile(expected_output) if expected_output else 'N/A'}")
                if r.stderr:
                    print(f"DEBUG: XTTS full error: {r.stderr}")
                
    except Exception as e:
        print(f"DEBUG: XTTS exception: {e}")

    # Fallback to other TTS methods if XTTS failed
    if not audio_url:
        print("DEBUG: XTTS failed, falling back to other methods")
        # Add your fallback TTS logic here

    # Add to timeline
    push_event({
        "type": "dj",
        "text": ai_text,  # Use the AI-generated text
        "audio_url": audio_url,
        "time": int(time.time() * 1000),
    })

    return jsonify(ok=True, queued_text=ai_text, audio_url=audio_url, next_track={"title": title, "artist": artist}), 200

# Also update the original api_dj_now to redirect to the working version
@app.post("/api/dj-now")
def api_dj_now():
    """Redirect to next-track DJ generation"""
    return api_dj_next()

@app.route("/api/cover", methods=["GET"])
def api_cover():
    """
    Return embedded album art for a given audio file.
    Accepts:
      - file=/abs/path/with spaces.m4a
      - file=file:///abs/path/with%20spaces.m4a
    """
    raw = request.args.get("file", "") or ""
    if not raw:
        return abort(404)

    # Handle file:// and percent-encoding safely
    if raw.startswith("file://"):
        p = urlparse(raw).path  # /mnt/music/...
        p = unquote(p)
    else:
        p = unquote(raw)

    # Optional: harden path (only allow under your music roots)
    ALLOWED_ROOTS = ("/mnt/music/", "/mnt/music/media/", "/mnt/music/Music/")
    if not any(p.startswith(root) for root in ALLOWED_ROOTS):
        return abort(404)

    if not os.path.isfile(p):
        return abort(404)

    # Try to extract embedded cover art (Mutagen works well for mp3/m4a)
    try:
        import mutagen
        from mutagen.flac import Picture
        from mutagen.mp3 import MP3
        from mutagen.id3 import APIC
        from mutagen.mp4 import MP4

        audio = mutagen.File(p)
        art_bytes = None
        mime = "image/jpeg"

        if isinstance(audio, MP3):
            # ID3 APIC frames
            for k, v in audio.tags.items():
                if isinstance(v, APIC):
                    art_bytes = v.data
                    mime = v.mime or mime
                    break
        elif isinstance(audio, MP4):
            # iTunes cover atom: 'covr'
            covr = audio.tags.get("covr")
            if covr:
                art_bytes = bytes(covr[0])
                # Heuristic for MIME
                if art_bytes.startswith(b"\x89PNG"):
                    mime = "image/png"
        else:
            # Some formats (e.g., FLAC) hold pictures differently
            pics = getattr(audio, "pictures", None)
            if pics:
                if isinstance(pics[0], Picture):
                    art_bytes = pics[0].data
                    if pics[0].mime:
                        mime = pics[0].mime

        if art_bytes:
            return send_file(io.BytesIO(art_bytes), mimetype=mime,
                             as_attachment=False, download_name="cover")

    except Exception:
        # fall through to placeholder
        pass

    # Fallback: serve your station placeholder so the UI never 404s
    placeholder = os.path.join(app.static_folder or "static", "station-cover.jpg")
    if os.path.isfile(placeholder):
        return send_file(placeholder, mimetype="image/jpeg")
    return abort(404)

@app.route("/api/dj-prompts", methods=["GET"])
def get_dj_prompts():
    """Get current DJ prompt configuration"""
    try:
        with open("/opt/ai-radio/dj_settings.json", "r") as f:
            config = json.load(f)
        
        prompts = config.get("ai_prompts", {})
        return jsonify({
            "intro_prompts": prompts.get("intro_prompts", []),
            "outro_prompts": prompts.get("outro_prompts", []),
            "active_intro_prompt": prompts.get("active_intro_prompt", "Default Energetic"),
            "active_outro_prompt": prompts.get("active_outro_prompt", "Default Conversational")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/dj-prompts/active", methods=["POST"])
def set_active_prompts():
    """Set active prompt styles"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON data required"}), 400
        
        with open("/opt/ai-radio/dj_settings.json", "r") as f:
            config = json.load(f)
        
        if "ai_prompts" not in config:
            config["ai_prompts"] = {}
        
        if "active_intro_prompt" in data:
            config["ai_prompts"]["active_intro_prompt"] = data["active_intro_prompt"]
        
        if "active_outro_prompt" in data:
            config["ai_prompts"]["active_outro_prompt"] = data["active_outro_prompt"]
        
        with open("/opt/ai-radio/dj_settings.json", "w") as f:
            json.dump(config, f, indent=2)
        
        return jsonify({"success": True, "message": "Active prompts updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/dj-prompts/custom", methods=["POST"])
def add_custom_prompt():
    """Add a custom prompt template"""
    try:
        data = request.get_json()
        if not data or not all(k in data for k in ["name", "prompt", "type"]):
            return jsonify({"error": "name, prompt, and type (intro/outro) required"}), 400
        
        with open("/opt/ai-radio/dj_settings.json", "r") as f:
            config = json.load(f)
        
        if "ai_prompts" not in config:
            config["ai_prompts"] = {"intro_prompts": [], "outro_prompts": []}
        
        prompt_type = data["type"]
        if prompt_type not in ["intro", "outro"]:
            return jsonify({"error": "type must be 'intro' or 'outro'"}), 400
        
        prompt_list = f"{prompt_type}_prompts"
        if prompt_list not in config["ai_prompts"]:
            config["ai_prompts"][prompt_list] = []
        
        new_prompt = {
            "name": data["name"],
            "prompt": data["prompt"]
        }
        
        # Check if prompt with same name exists
        existing = [p for p in config["ai_prompts"][prompt_list] if p.get("name") == data["name"]]
        if existing:
            return jsonify({"error": f"Prompt '{data['name']}' already exists"}), 400
        
        config["ai_prompts"][prompt_list].append(new_prompt)
        
        with open("/opt/ai-radio/dj_settings.json", "w") as f:
            json.dump(config, f, indent=2)
        
        return jsonify({"success": True, "message": f"Custom {prompt_type} prompt added"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cover/online", methods=["GET"])
def api_cover_online():
    """
    Fetch album art online for tracks missing embedded covers.
    Query params: artist, album
    """
    artist = request.args.get("artist", "").strip()
    album = request.args.get("album", "").strip()
    
    if not artist or not album:
        return abort(404)
    
    cache_key = (artist.lower(), album.lower())
    
    # Check cache first
    with _cover_cache_lock:
        if cache_key in _cover_cache:
            cached_result = _cover_cache[cache_key]
            if cached_result is None:
                # Previously failed
                return abort(404)
            elif cached_result.startswith("http"):
                # Have a real URL, redirect to it
                return redirect(cached_result)
    
    # Try to fetch online
    try:
        cover_data = _fetch_online_cover(artist, album, "", size=300, timeout=8)
        if cover_data:
            art_bytes, mime_type = cover_data
            # Cache success (could store to disk and return URL)
            with _cover_cache_lock:
                _cover_cache[cache_key] = "data:image/jpeg;base64," + str(art_bytes)[:100]  # Simplified
            
            return Response(art_bytes, mimetype=mime_type)
        else:
            # Cache failure
            with _cover_cache_lock:
                _cover_cache[cache_key] = None
            return abort(404)
            
    except Exception as e:
        print(f"Online cover fetch failed for {artist} - {album}: {e}")
        with _cover_cache_lock:
            _cover_cache[cache_key] = None
        return abort(404)

# ── Startup ─────────────────────────────────────────────────────
load_history()
_load_history_from_disk()

if os.environ.get("WERKZEUG_RUN_MAIN") != "true":  # avoid double-start in debug
    t = threading.Thread(target=_scrobble_loop, name="scrobble", daemon=True)
    t.start()

# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
def _safe_read_history():
    try:
        with open(HISTORY_PATH, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def push_event(ev):
    global HISTORY
    # Normalize in-memory history
    if not isinstance(HISTORY, list):
        HISTORY = _safe_read_history()
        if not isinstance(HISTORY, list):
            HISTORY = []

    # Ensure timestamp (ms)
    ev.setdefault("time", int(time.time() * 1000))

    # Prepend newest
    HISTORY.insert(0, ev)

    # Trim safely
    try:
        if len(HISTORY) > int(MAX_HISTORY):
            del HISTORY[int(MAX_HISTORY):]
    except Exception:
        HISTORY = HISTORY[:500]

    # Persist atomically
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(HISTORY, f)
    os.replace(tmp, HISTORY_PATH)
    return True
