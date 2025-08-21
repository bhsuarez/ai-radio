#!/usr/bin/env python3
import os, json, socket, time, html, hashlib, io, re, requests, subprocess, threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
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

HISTORY = deque(maxlen=400)
_last_now_key = None
_last_history_key = None 
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
    Determine current track:
      1) request.all -> list of RIDs (lower RID == currently playing)
      2) request.metadata <rid> -> kv metadata
      Fallback: parse block --- 1 --- from output.icecast.metadata
    """
    try:
        raw = _ls_query("request.all")
        rids = [int(x) for x in re.findall(r"\b\d+\b", raw)]
        if rids:
            rid = min(rids)  # lower RID is "on air" in your setup
            md = _metadata_for_rid(rid)
            if md:
                return md
        # Fallback: take block --- 1 --- from output.icecast.metadata
        raw2 = _ls_query("output.icecast.metadata")
        # capture lines between --- 1 --- and next --- or END
        block = []
        in_one = False
        for ln in raw2.splitlines():
            if ln.strip().startswith("--- 1 ---"):
                in_one = True
                continue
            if in_one and ln.strip().startswith("--- "):
                break
            if in_one and ln.strip() != "END":
                block.append(ln)
        d = _parse_kv_block("\n".join(block))
        if d:
            return {
                "title": d.get("title") or "Unknown",
                "artist": d.get("artist") or "Unknown",
                "album": d.get("album") or "",
                "filename": "",  # not present in this view
                "time": int(__import__("time").time() * 1000),
                "duration_ms": None,
                "elapsed_ms": None,
            }
        return None
    except Exception:
        return None

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

def _ls_query(cmd: str, host: str = "127.0.0.1", port: int = 1234, timeout: float = 2.5) -> str:
    """Send one command to LS telnet interface and return raw text (until END)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    try:
        # some LS builds print a banner; ignore
        try:
            _ = s.recv(4096)
        except Exception:
            pass
        s.sendall((cmd + "\n").encode("utf-8"))

        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\nEND\n" in buf or buf.endswith(b"END\n") or b"END\r\n" in buf:
                break
        # be polite
        try:
            s.sendall(b"quit\n")
        except Exception:
            pass
        return buf.decode("utf-8", "ignore")
    finally:
        s.close()

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

def _ls_lines(cmd: str, timeout: float = 2.5):
    """
    Send a command to Liquidsoap's telnet interface and return a list of lines,
    with trailing 'END' removed.
    """
    data = b""
    with socket.create_connection((LS_HOST, LS_PORT), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\n").encode("utf-8"))
        s.sendall(b"quit\n")
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

@app.get("/api/history")
def api_history():
    with HISTORY_LOCK:
        # Return newest first
        return jsonify(sorted(HISTORY, key=lambda e: e.get("time", 0), reverse=True))

@app.get("/api/now")
def api_now():
    now = _get_now_playing()
    if not now:
        return jsonify({"error": "No track info"}), 404
    return jsonify(now)

@app.get("/api/next")
def api_next():
    try:
        rid_lines = _ls_cmd("request.all")  # e.g. ["78 79"] or ["78", "79"]
        rids = []
        for ln in rid_lines:
            rids.extend(x for x in ln.strip().split() if x.isdigit())
        # Keep ordering as returned by LS (first up next first)
        upcoming = [_metadata_for_rid(r) for r in rids]
        return jsonify(upcoming)
    except Exception as e:
        app.logger.exception("next endpoint failed")
        # return stable empty array on error so UI doesn't explode
        return jsonify([])  # optionally: jsonify({"error": str(e)})

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

# @app.post("/api/dj-now")
# def api_dj_now():
#     os.makedirs(TTS_DIR, exist_ok=True)
#     ts = int(time.time())

#     # Get track data
#     title = "Unknown Title"
#     artist = "Unknown Artist"
    
#     try:
#         print("DEBUG: Getting track data for DJ intro")
#         track_data = read_now()
#         print(f"DEBUG: Live metadata: {track_data}")
        
#         if track_data and track_data.get("title") and track_data.get("title") != "Unknown title":
#             title = track_data.get("title", "Unknown Title")
#             artist = track_data.get("artist", "Unknown Artist")
#             print(f"DEBUG: Using live metadata - Title: '{title}', Artist: '{artist}'")
#         else:
#             print("DEBUG: Live metadata incomplete, checking history")
#             for ev in HISTORY:
#                 if ev.get("type") == "song":
#                     title = ev.get("title", "Unknown Title")
#                     artist = ev.get("artist", "Unknown Artist")
#                     print(f"DEBUG: Using history fallback - Title: '{title}', Artist: '{artist}'")
#                     break
                
#     except Exception as e:
#         print(f"DEBUG: Error getting track data: {e}")

#     print(f"DEBUG: Final track data - Title: '{title}', Artist: '{artist}'")

#     # Generate DJ line (if using AI generation)
#     line = f"That was '{title}' by {artist}."
#     try:
#         print(f"DEBUG: Running DJ script: /opt/ai-radio/gen_ai_dj_line.sh '{title}' '{artist}'")
#         result = subprocess.run(
#             ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist],
#             capture_output=True, text=True, timeout=35
#         )
#         if result.returncode == 0 and result.stdout.strip():
#             line = ANSI.sub('', result.stdout.strip())
#             print(f"DEBUG: Using DJ script output: '{line}'")
#         else:
#             print(f"DEBUG: DJ script failed, using fallback: '{line}'")
#     except Exception as e:
#         print(f"DEBUG: DJ script error: {e}, using fallback: '{line}'")

#     # XTTS synthesis
#     audio_url = None
    
#     try:
#         if os.getenv("USE_XTTS", "1") in ("1", "true", "True"):
#             print("DEBUG: Trying XTTS via dj_enqueue_xtts.sh")
            
#             # Build command with proper arguments
#             xtts_speaker = os.getenv("XTTS_SPEAKER")
#             cmd = ["/opt/ai-radio/dj_enqueue_xtts.sh", artist, title]
#             if xtts_speaker:
#                 cmd += ["en", xtts_speaker]
            
#             print(f"DEBUG: XTTS command: {cmd}")
            
#             # Run XTTS script
#             r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
#             print(f"DEBUG: XTTS return code: {r.returncode}")
            
#             if r.stdout:
#                 print(f"DEBUG: XTTS stdout: {r.stdout.strip()}")
#             if r.stderr:
#                 print(f"DEBUG: XTTS stderr (first 500 chars): {r.stderr[:500]}")
                
#             # Check if script output contains a file path
#             output_lines = r.stdout.strip().split('\n')
#             candidate_file = None
            
#             # Look for a file path in the output
#             for line in output_lines:
#                 line = line.strip()
#                 if line.startswith('/') and line.endswith('.mp3') and os.path.isfile(line):
#                     candidate_file = line
#                     break
            
#             # If no explicit path found, look for newest intro file
#             if not candidate_file:
#                 try:
#                     print("DEBUG: No explicit file path found, searching for newest intro file")
#                     pattern = os.path.join(TTS_DIR, "intro_*.mp3")
#                     import glob
#                     files = glob.glob(pattern)
#                     if files:
#                         # Sort by modification time, newest first
#                         files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
#                         # Check if the newest file was created in the last 30 seconds
#                         newest = files[0]
#                         if time.time() - os.path.getmtime(newest) < 30:
#                             candidate_file = newest
#                             print(f"DEBUG: Found recent intro file: {candidate_file}")
#                         else:
#                             print(f"DEBUG: Newest intro file too old: {newest}")
#                     else:
#                         print("DEBUG: No intro files found")
#                 except Exception as e:
#                     print(f"DEBUG: Error searching for intro files: {e}")
            
#             if candidate_file and r.returncode == 0:
#                 audio_url = f"/tts/{os.path.basename(candidate_file)}"
#                 print(f"DEBUG: XTTS SUCCESS: {audio_url}")
                
#                 # Push to Liquidsoap immediately
#                 try:
#                     print(f"DEBUG: Pushing to Liquidsoap: {candidate_file}")
#                     liq_result = subprocess.run(
#                         ["nc", "127.0.0.1", "1234"],
#                         input=f"tts.push {candidate_file}\nquit\n".encode(),
#                         capture_output=True,
#                         timeout=5
#                     )
#                     print(f"DEBUG: Liquidsoap push result: {liq_result.returncode}")
#                     if liq_result.stdout:
#                         print(f"DEBUG: Liquidsoap stdout: {liq_result.stdout.decode()}")
#                 except Exception as e:
#                     print(f"DEBUG: Liquidsoap push failed: {e}")
                    
#             else:
#                 print("DEBUG: XTTS did not produce a usable audio file")
                
#     except Exception as e:
#         print(f"DEBUG: XTTS exception: {e}")

#     # Fallback to ElevenLabs/Piper only if XTTS failed
#     if not audio_url:
#         print("DEBUG: XTTS failed, falling back to ElevenLabs/Piper")
#         mp3 = os.path.join(TTS_DIR, f"intro_{ts}.mp3")
        
#         try:
#             api_key = os.getenv("ELEVENLABS_API_KEY")
#             if api_key and 'synthesize_with_elevenlabs' in globals():
#                 print("DEBUG: Trying ElevenLabs synthesis")
#                 if synthesize_with_elevenlabs(line, mp3):
#                     audio_url = f"/tts/{os.path.basename(mp3)}"
#                     print(f"DEBUG: ElevenLabs synthesis successful: {audio_url}")
#                 else:
#                     raise Exception("ElevenLabs failed")
#             else:
#                 raise Exception("No ElevenLabs available")
#         except Exception as e:
#             print(f"DEBUG: ElevenLabs failed: {e}, trying Piper")
#             # Piper fallback code here...

#     # Add to timeline
#     push_event({
#         "type": "dj",
#         "text": line,
#         "audio_url": audio_url,
#         "time": int(time.time() * 1000),
#     })

#     print(f"DEBUG: Final result - Text: '{line}', Audio URL: {audio_url}")
#     return jsonify(ok=True, queued_text=line, audio_url=audio_url), 200

@app.post("/api/dj-next")
def api_dj_next():
    """Generate DJ intro for the NEXT upcoming track, not current track"""
    os.makedirs(TTS_DIR, exist_ok=True)
    ts = int(time.time())

    # Get NEXT track from Liquidsoap queue
    try:
        print("DEBUG: Getting next track from Liquidsoap queue")
        rid_lines = _ls_cmd("request.all")
        rids = []
        for ln in rid_lines:
            rids.extend(x for x in ln.strip().split() if x.isdigit())
        
        if not rids:
            print("DEBUG: No tracks in queue")
            return jsonify({"ok": False, "error": "No tracks in queue"}), 400
            
        # Get metadata for the first (next) track
        next_rid = rids[0]
        next_track = _metadata_for_rid(next_rid)
        
        if not next_track or not next_track.get("title"):
            print("DEBUG: Could not get metadata for next track")
            return jsonify({"ok": False, "error": "No metadata for next track"}), 400
            
        title = next_track.get("title", "Unknown Title")
        artist = next_track.get("artist", "Unknown Artist")
        
        print(f"DEBUG: Next track - Title: '{title}', Artist: '{artist}'")
        
    except Exception as e:
        print(f"DEBUG: Error getting next track: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    # Generate DJ line for UPCOMING track (not past track)
    line = f"Up next: '{title}' by {artist}."
    
    # Set DJ_INTRO_MODE=1 to change the prompt style
    env = os.environ.copy()
    env["DJ_INTRO_MODE"] = "1"
    
    try:
        print(f"DEBUG: Running DJ script in intro mode")
        result = subprocess.run(
            ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist],
            capture_output=True, text=True, timeout=35, env=env
        )
        if result.returncode == 0 and result.stdout.strip():
            line = ANSI.sub('', result.stdout.strip())
            print(f"DEBUG: Using DJ script output: '{line}'")
        else:
            print(f"DEBUG: DJ script failed, using fallback: '{line}'")
    except Exception as e:
        print(f"DEBUG: DJ script error: {e}, using fallback: '{line}'")

    # XTTS synthesis
    audio_url = None
    
    try:
        if os.getenv("USE_XTTS", "1") in ("1", "true", "True"):
            print("DEBUG: Generating XTTS for upcoming track")
            
            cmd = ["/opt/ai-radio/dj_enqueue_xtts.sh", artist, title, "en"]
            print(f"DEBUG: XTTS command: {cmd}")
            
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            print(f"DEBUG: XTTS return code: {r.returncode}")
            
            if r.stdout:
                print(f"DEBUG: XTTS stdout: {r.stdout.strip()}")
            if r.stderr:
                print(f"DEBUG: XTTS stderr (first 300 chars): {r.stderr[:300]}")
                
            # Look for output file path
            output_lines = r.stdout.strip().split('\n')
            candidate_file = None
            
            for line in output_lines:
                line = line.strip()
                if line.startswith('/') and line.endswith('.mp3') and os.path.isfile(line):
                    candidate_file = line
                    break
            
            if candidate_file and r.returncode == 0:
                audio_url = f"/tts/{os.path.basename(candidate_file)}"
                print(f"DEBUG: XTTS SUCCESS: {audio_url}")
                
                # Push to Liquidsoap TTS queue
                try:
                    print(f"DEBUG: Pushing to Liquidsoap TTS queue: {candidate_file}")
                    liq_result = subprocess.run(
                        ["nc", "127.0.0.1", "1234"],
                        input=f"tts.push {candidate_file}\nquit\n".encode(),
                        capture_output=True,
                        timeout=5
                    )
                    print(f"DEBUG: Liquidsoap push result: {liq_result.returncode}")
                    if liq_result.stdout:
                        print(f"DEBUG: Liquidsoap stdout: {liq_result.stdout.decode()}")
                except Exception as e:
                    print(f"DEBUG: Liquidsoap push failed: {e}")
            else:
                print("DEBUG: XTTS did not produce a usable audio file")
                
    except Exception as e:
        print(f"DEBUG: XTTS exception: {e}")

    # Add to timeline
    push_event({
        "type": "dj",
        "text": line,
        "audio_url": audio_url,
        "time": int(time.time() * 1000),
    })

    return jsonify(ok=True, queued_text=line, audio_url=audio_url, next_track={"title": title, "artist": artist}), 200


# Modify the existing dj-now endpoint to use next track instead of current
@app.post("/api/dj-now")
def api_dj_now():
    """Redirect to next-track DJ generation"""
    return api_dj_next()

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
