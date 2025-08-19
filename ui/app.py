#!/usr/bin/env python3
import os, json, socket, time, html, hashlib, io, re, requests, subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from urllib.parse import quote
from urllib.parse import unquote, unquote_plus
from contextlib import closing
from collections import deque

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

HISTORY_FILE = "/opt/ai-radio/play_history.json"
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
#HISTORY = []          # newest first
UPCOMING = []         # optional future items
NEXT_CACHE = Path("/opt/ai-radio/next.json")

HISTORY = deque(maxlen=400)  # keep more if you like
_last_now_key = None
_last_now_payload = None 

# ── Helpers ─────────────────────────────────────────────────────
def telnet_cmd(cmd: str, timeout=5) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((TELNET_HOST, TELNET_PORT))
    s.sendall((cmd + "\n").encode())
    chunks = []
    try:
        while True:
            try:
                b = s.recv(65535)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
    finally:
        s.close()
    return (b"".join(chunks).decode(errors="ignore") or "").strip()

def parse_kv_text(text: str) -> dict:
    """Parse lines like key=value"""
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out

def load_history():
    global HISTORY
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                HISTORY[:] = json.load(f)
        else:
            HISTORY[:] = []
    except Exception:
        HISTORY[:] = []

def save_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(HISTORY[:MAX_HISTORY], f)
    except Exception:
        pass

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
            # Use the correct telnet command
            raw = telnet_cmd("output.icecast.metadata")
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

    # De-dupe against last entry within window
    if HISTORY:
        last = HISTORY[0]
        if ev.get("type") == "song" and last.get("type") == "song":
            same = (
                (ev.get("title") or "") == (last.get("title") or "") and
                (ev.get("artist") or "") == (last.get("artist") or "") and
                (ev.get("filename") or "") == (last.get("filename") or "")
            )
            if same and (now_ms - int(last.get("time", now_ms))) < DEDUP_WINDOW_MS:
                return
        if ev.get("type") == "dj" and last.get("type") == "dj":
            if (ev.get("text") or "") == (last.get("text") or "") and \
               (now_ms - int(last.get("time", now_ms))) < 5000:
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

def _ls_cmd(cmd: str, expect_end=True, timeout=2.0):
    """Send one command to Liquidsoap telnet and return lines (without END)."""
    with closing(socket.create_connection((LS_HOST, LS_PORT), timeout=timeout)) as s:
        s.sendall((cmd.strip() + "\n").encode("utf-8"))
        # read until END or connection closes
        buf = b""
        s.settimeout(timeout)
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\nEND" in buf or b"END\r" in buf:
                    break
            except socket.timeout:
                break
    text = buf.decode("utf-8", "replace")
    # strip leading echo/connection banners and trailing END
    lines = [ln.strip() for ln in text.splitlines()]
    if "END" in lines:
        lines = lines[:lines.index("END")]
    # Liquidsoap often echoes our command; drop it if present
    if lines and lines[0].lower().startswith(cmd.split()[0].lower()):
        lines = lines[1:]
    return [ln for ln in lines if ln]

def _metadata_for_rid(rid: int) -> dict:
    lines = _ls_cmd(f"request.metadata {rid}")
    md = {"rid": rid}
    for ln in lines:
        m = _key_val_re.match(ln)
        if not m: 
            continue
        k, v = m.group(1).strip(), m.group(2)
        # unescape any quoted data
        v = v.replace(r'\n', '\n')
        md[k] = v
    # normalize fields our frontend expects
    title   = md.get("title") or md.get("song") or ""
    artist  = md.get("artist", "")
    album   = md.get("album", "")
    fname   = md.get("filename") or md.get("initial_uri", "").replace("file://", "")
    out = {
        "rid": rid,
        "title": title,
        "artist": artist,
        "album": album,
        "filename": fname,
    }
    if fname:
        out["artwork_url"] = f"/api/cover?file={urllib.parse.quote(fname)}"
    return out

_key_val_re = re.compile(r'([^=]+)="(.*)"$')

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
    art_url = f"/api/cover?file={quote(fn)}" if fn else ev.get("artwork_url")

    return {
        "type": "song",
        "time": ts,
        "title": ev.get("title") or "",
        "artist": ev.get("artist") or "",
        "album": ev.get("album") or "",
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

@app.route("/api/history")
def api_history():
    return jsonify(_load_history(60))

@app.get("/api/now")
def api_now():
    # 1) however you currently build "now" (from Liquidsoap / telnet)
    now = _get_current_now_dict_somehow()  # <-- your existing logic returns a dict

    # 2) normalize and ensure artwork if we have filename
    global _last_now_key, _last_now_payload
    now = _with_artwork(now)
    key = _track_key(now) if (now.get("title") or now.get("filename")) else None

    # 3) detect change -> log previous into HISTORY
    if key and _last_now_key and key != _last_now_key:
        _maybe_log_previous(_last_now_payload)

    # 4) update trackers and return
    if key:
        _last_now_key = key
        _last_now_payload = now
    return jsonify(now)

@app.get("/api/next")
def api_next():
    """
    Return upcoming tracks from Liquidsoap request queue:
    - ask `request.all` for RIDs
    - drop the lowest RID (current/playing)
    - fetch metadata for the rest, in ascending RID order
    """
    try:
        rid_lines = _ls_cmd("request.all")
        # lines are space-separated RIDs like: "78 79"
        rids = []
        for ln in rid_lines:
            rids.extend([int(x) for x in ln.split() if x.isdigit()])
        rids = sorted(set(rids))
        if not rids:
            return jsonify([])

        # lowest RID == currently playing; remove it
        if len(rids) >= 1:
            rids = rids[1:]

        upcoming = [_metadata_for_rid(r) for r in rids]
        return jsonify(upcoming)
    except Exception as e:
        app.logger.exception("next endpoint failed")
        return jsonify([]), 200

# Serve the synthesized DJ audio files
@app.get("/tts_queue/<path:fname>")
def tts_audio(fname):
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

    files = [f for f in os.listdir(root)
             if f.lower().endswith((".mp3", ".m4a", ".wav", ".ogg"))]

    events = []
    for f in files:
        p = os.path.join(root, f)
        try:
            st = os.stat(p)
        except FileNotFoundError:
            continue

        meta = _parse_tts_name(p)
        title  = meta["title"]
        artist = meta["artist"]
        when   = meta["ts"] or int(st.st_mtime * 1000)

        # Build friendly text line
        text = (f"That was {title} by {artist}."
                if title or artist
                else os.path.splitext(f)[0])

        events.append({
            "type": "dj",              # <--- important
            "time": when,
            "text": text,
            "audio_url": f"/tts_queue/{f}",
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
    try:
        telnet_cmd("output.icecast.skip")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.post("/api/log_event")
def log_event():
    data = request.get_json(force=True) or {}
    push_event(data)
    return {"ok": True}

@app.post("/api/dj-now")
def api_dj_now():
    os.makedirs(TTS_DIR, exist_ok=True)
    ts = int(time.time())

    # Instead of making an HTTP call to ourselves, use the same logic as /api/now directly
    title = "Unknown Title"
    artist = "Unknown Artist"
    
    try:
        print("DEBUG: Getting track data directly (not via HTTP)")
        
        # FIRST try live metadata (most current)
        print("DEBUG: Reading live metadata first")
        track_data = read_now()
        print(f"DEBUG: Live metadata: {track_data}")
        
        if track_data and track_data.get("title") and track_data.get("title") != "Unknown title":
            title = track_data.get("title", "Unknown Title")
            artist = track_data.get("artist", "Unknown Artist")
            print(f"DEBUG: Using live metadata - Title: '{title}', Artist: '{artist}'")
        else:
            # Fallback to history only if live data is incomplete
            print("DEBUG: Live metadata incomplete, checking history")
            for ev in HISTORY:
                if ev.get("type") == "song":
                    title = ev.get("title", "Unknown Title")
                    artist = ev.get("artist", "Unknown Artist")
                    print(f"DEBUG: Using history fallback - Title: '{title}', Artist: '{artist}'")
                    break
                
    except Exception as e:
        print(f"DEBUG: Error getting track data: {e}")
        import traceback
        traceback.print_exc()

    print(f"DEBUG: Final track data - Title: '{title}', Artist: '{artist}'")

    # Generate DJ line
    try:
        print(f"DEBUG: Running DJ script: /opt/ai-radio/gen_ai_dj_line.sh '{title}' '{artist}'")
        result = subprocess.run(
            ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist],
            capture_output=True, text=True, timeout=35
        )
        print(f"DEBUG: DJ script return code: {result.returncode}")
        print(f"DEBUG: DJ script stdout: '{result.stdout.strip()}'")
        print(f"DEBUG: DJ script stderr: '{result.stderr.strip()}'")
        
        if result.returncode == 0 and result.stdout.strip():
            line = ANSI.sub('', result.stdout.strip())
            print(f"DEBUG: Using DJ script output: '{line}'")
        else:
            line = f"That was '{title}' by {artist}."
            print(f"DEBUG: DJ script failed, using fallback: '{line}'")
    except subprocess.TimeoutExpired:
        line = f"That was '{title}' by {artist}."
        print(f"DEBUG: DJ script timed out, using fallback: '{line}'")
    except Exception as e:
        line = f"That was '{title}' by {artist}."
        print(f"DEBUG: DJ script error: {e}, using fallback: '{line}'")

    # TTS synthesis
    mp3 = os.path.join(TTS_DIR, f"intro_{ts}.mp3")
    audio_url = None
    
    try:
        # Check if ElevenLabs is available
        api_key = os.getenv("ELEVENLABS_API_KEY")
        print(f"DEBUG: ElevenLabs API key present: {bool(api_key)}")
        
        if api_key and 'synthesize_with_elevenlabs' in globals():
            print("DEBUG: Trying ElevenLabs synthesis")
            if synthesize_with_elevenlabs(line, mp3):
                audio_url = f"/tts/{os.path.basename(mp3)}"
                print(f"DEBUG: ElevenLabs synthesis successful: {audio_url}")
            else:
                print("DEBUG: ElevenLabs failed, falling back to Piper")
                raise Exception("ElevenLabs failed")
        else:
            print("DEBUG: No ElevenLabs available, using Piper")
            raise Exception("No ElevenLabs")
            
    except Exception as e:
        print(f"DEBUG: ElevenLabs exception: {e}, trying Piper")
        # Piper fallback
        try:
            wav = os.path.join(TTS_DIR, f"intro_{ts}.wav")
            print(f"DEBUG: Running Piper to create {wav}")
            
            piper_result = subprocess.run(
                ["piper", "--model", VOICE, "--output_file", wav],
                input=line.encode("utf-8"), 
                capture_output=True,
                timeout=30
            )
            print(f"DEBUG: Piper return code: {piper_result.returncode}")
            if piper_result.stderr:
                print(f"DEBUG: Piper stderr: {piper_result.stderr.decode()}")
            
            if piper_result.returncode == 0:
                # Try to convert to MP3
                try:
                    print("DEBUG: Converting WAV to MP3")
                    ffmpeg_result = subprocess.run(
                        ["ffmpeg", "-nostdin", "-y", "-i", wav, "-codec:a", "libmp3lame", "-q:a", "3", mp3],
                        capture_output=True, timeout=15
                    )
                    if ffmpeg_result.returncode == 0:
                        audio_url = f"/tts/{os.path.basename(mp3)}"
                        print(f"DEBUG: MP3 conversion successful: {audio_url}")
                    else:
                        audio_url = f"/tts/{os.path.basename(wav)}"
                        print(f"DEBUG: MP3 conversion failed, using WAV: {audio_url}")
                except Exception as ffmpeg_error:
                    audio_url = f"/tts/{os.path.basename(wav)}"
                    print(f"DEBUG: FFmpeg error: {ffmpeg_error}, using WAV: {audio_url}")
            else:
                print("DEBUG: Piper synthesis failed")
                
        except Exception as piper_error:
            print(f"DEBUG: Piper completely failed: {piper_error}")
    
    # Push to Liquidsoap (best effort)
    if audio_url:
        try:
            audio_filename = os.path.basename(audio_url.replace('/tts/', ''))
            full_path = os.path.join(TTS_DIR, audio_filename)
            uri = full_path
            print(f"DEBUG: Pushing to Liquidsoap: {uri}")
            
            liq_result = subprocess.run(
                ["nc", "127.0.0.1", "1234"],
                input=f"tts.push {uri}\nquit\n".encode(),
                capture_output=True,
                timeout=5,
                check=False
            )
            print(f"DEBUG: Liquidsoap push result: {liq_result.returncode}")
            if liq_result.stdout:
                print(f"DEBUG: Liquidsoap stdout: {liq_result.stdout.decode()}")
                
        except Exception as e:
            print(f"DEBUG: Liquidsoap push failed: {e}")

    # Add to timeline
    push_event({
        "type": "dj",
        "text": line,
        "audio_url": audio_url,
        "time": int(time.time() * 1000),
    })

    print(f"DEBUG: Final result - Text: '{line}', Audio URL: {audio_url}")
    return jsonify(ok=True, queued_text=line, audio_url=audio_url), 200

@app.get("/api/cover")
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

# ── Startup ─────────────────────────────────────────────────────
load_history()

# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host=HOST, port=PORT)