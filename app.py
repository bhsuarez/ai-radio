#!/usr/bin/env python3
import os, json, socket, time, hashlib, io, re
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file
try:
    import requests
except Exception:
    requests = None  # online lookup disabled if requests isn't available

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))

NOW_JSON   = "/opt/ai-radio/now.json"
NOW_TXT    = "/opt/ai-radio/nowplaying.txt"

TTS_DIR    = "/opt/ai-radio/tts_queue"
GEN_SCRIPT = "/opt/ai-radio/gen_dj_clip.sh"

LOG_DIR    = "/opt/ai-radio/logs"
DJ_LOG     = os.path.join(LOG_DIR, "dj-now.log")

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
HISTORY = []          # newest first
UPCOMING = []         # optional future items

# ── Helpers ─────────────────────────────────────────────────────
def telnet_cmd(cmd: str, timeout=1.5) -> str:
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

    # Fallback: Liquidsoap telnet (namespaced command)
    if not data.get("title"):
        try:
            raw = telnet_cmd("AI_Plex_DJ.metadata")
            kv = parse_kv_text(raw)
            data["title"]     = kv.get("title") or kv.get("song") or data.get("title") or "Unknown title"
            data["artist"]    = kv.get("artist") or data.get("artist") or "Unknown artist"
            data["album"]     = kv.get("album") or data.get("album") or ""
            data["filename"]  = kv.get("filename") or kv.get("file") or data.get("filename") or ""
            data["artwork_url"] = data.get("artwork_url") or ""
        except Exception:
            pass

    data.setdefault("title", "Unknown title")
    data.setdefault("artist", "Unknown artist")
    return data

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

# ── Routes ──────────────────────────────────────────────────────
def _build_art_url(path: str) -> str:
    """Return a URL that always resolves to cover art (or your default)."""
    if path and os.path.isabs(path) and os.path.exists(path):
        return request.url_root.rstrip("/") + "/api/cover?file=" + quote(path)
    # no valid file → use your station default
    return request.url_root.rstrip("/") + "/static/station-cover.jpg"

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/api/history")
def api_history():
    # If memory empty, try to hydrate from disk
    if not HISTORY and os.path.exists(HISTORY_FILE):
        load_history()
    return jsonify(HISTORY[:MAX_HISTORY])

@app.get("/api/now")
def api_now():
    # Prefer the newest song event from memory
    for ev in HISTORY:
        if ev.get("type") == "song":
            return jsonify(ev)
    # Fallback to live metadata (works mid-track)
    data = read_now()
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title": data.get("title") or "Unknown",
        "artist": data.get("artist") or "",
        "album": data.get("album") or "",
        "filename": data.get("filename") or "",
        "artwork_url": data.get("artwork_url") or _build_art_url(data.get("filename"))
    }
    return jsonify(ev)

@app.get("/api/next")
def api_next():
    return jsonify(UPCOMING)

@app.get("/api/tts_queue")
def tts_queue_get():
    # Mirror recent DJ events for UI compatibility
    items = [e for e in HISTORY if e.get("type") == "dj"][:5]
    return jsonify(items)

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
        telnet_cmd("AI_Plex_DJ.skip")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.post("/api/log_event")
def log_event():
    data = request.get_json(force=True) or {}
    push_event(data)
    return {"ok": True}

@app.get("/api/cover")
def api_cover():
    """
    GET /api/cover?file=/abs/path/to/song.ext
    Returns cover art from:
      1) embedded tags
      2) folder images
      3) online lookup (MusicBrainz → Cover Art Archive, then iTunes)
      4) default station cover (static/station-cover.jpg)
    Caches results in COVER_CACHE.
    """
    fpath = request.args.get("file", "")
    default_cover_path = os.path.join(BASE_DIR, "static", "station-cover.jpg")
    default_mime = "image/jpeg"

    def _send_default():
        if os.path.exists(default_cover_path):
            return send_file(default_cover_path, mimetype=default_mime, conditional=True)
        return abort(404)

    if not fpath or not os.path.isabs(fpath) or not os.path.exists(fpath):
        return _send_default()

    key = hashlib.sha1(fpath.encode("utf-8")).hexdigest()
    cache_jpg = os.path.join(COVER_CACHE, key + ".jpg")
    cache_png = os.path.join(COVER_CACHE, key + ".png")

    # 0) serve from cache
    if os.path.exists(cache_jpg):
        return send_file(cache_jpg, mimetype="image/jpeg", conditional=True)
    if os.path.exists(cache_png):
        return send_file(cache_png, mimetype="image/png", conditional=True)

    data = None
    mime = None

    # 1) embedded art
    if _MUTAGEN_OK:
        try:
            audio = MFile(fpath)
            if audio:
                # MP3 APIC
                try:
                    from mutagen.id3 import APIC
                    if getattr(audio, "tags", None):
                        for _, v in audio.tags.items():
                            if isinstance(v, APIC):
                                data, mime = v.data, (v.mime or "image/jpeg"); break
                except Exception:
                    pass
                # FLAC picture
                if data is None:
                    try:
                        from mutagen.flac import FLAC
                        if isinstance(audio, FLAC) and audio.pictures:
                            pic = audio.pictures[0]
                            data, mime = pic.data, (pic.mime or "image/jpeg")
                    except Exception:
                        pass
                # MP4/M4A covr
                if data is None:
                    try:
                        covr = None
                        if getattr(audio, "tags", None):
                            covr = audio.tags.get("covr") or audio.tags.get("----:com.apple.iTunes:cover")
                        if covr:
                            b = covr[0] if isinstance(covr, list) else covr
                            data, mime = bytes(b), "image/jpeg"
                    except Exception:
                        pass
        except Exception:
            pass

    # 2) folder images
    if data is None:
        folder = os.path.dirname(fpath)
        for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png"):
            p = os.path.join(folder, name)
            if os.path.exists(p):
                with open(p, "rb") as imgf:
                    data = imgf.read()
                mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
                break

    # 3) online lookup (MusicBrainz → CAA → iTunes)
    if data is None:
        artist = album = title = None
        if _MUTAGEN_OK:
            try:
                audio = MFile(fpath)
                if audio and getattr(audio, "tags", None):
                    title  = _first_tag(audio.tags.get("title"))  or _first_tag(audio.tags.get("TIT2"))
                    artist = _first_tag(audio.tags.get("artist")) or _first_tag(audio.tags.get("TPE1"))
                    album  = _first_tag(audio.tags.get("album"))  or _first_tag(audio.tags.get("TALB"))
            except Exception:
                pass
        # fallback: infer from path
        if not artist or not title:
            parts = os.path.normpath(fpath).split(os.sep)
            if len(parts) >= 3:
                album = album or parts[-2]
                artist = artist or parts[-3]
                title = title or os.path.splitext(os.path.basename(fpath))[0]

        fetched = _fetch_online_cover(artist, album, title)
        if fetched:
            data, mime = fetched

    # 4) cache + return (or default)
    if data is None:
        return _send_default()

    ext = ".jpg" if "jpeg" in (mime or "").lower() else ".png"
    out = os.path.join(COVER_CACHE, key + ext)
    with open(out, "wb") as w:
        w.write(data)
    return send_file(out, mimetype="image/jpeg" if ext == ".jpg" else "image/png", conditional=True)

# ── Startup ─────────────────────────────────────────────────────
load_history()

# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host=HOST, port=PORT)