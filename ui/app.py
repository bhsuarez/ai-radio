#!/usr/bin/env python3
import os, json, socket, time, hashlib, io, re, subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
try:
    import requests
except Exception:
    requests = None  # online lookup disabled if requests isn't available
from urllib.parse import quote

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

def read_now():
    try:
        with socket.create_connection(("127.0.0.1", 1234), timeout=2.0) as s:
            s.sendall(b"output.icecast.metadata\n")
            buf = b""
            s.settimeout(2.0)
            while b"END\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        text = buf.decode(errors="ignore")

        # grab the current block (--- 1 --- ... END)
        m = re.search(r"---\s*1\s*---\s*(.*?)\s*END", text, re.S)
        if not m:
            return {}
        block = m.group(1)

        meta = {}
        for line in block.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                meta[k.strip()] = v.strip().strip('"')

        return {
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "album": meta.get("album", ""),
            # filename usually isn’t in this telnet block; ok to leave blank.
            "filename": meta.get("filename", "")
        }
    except Exception:
        return {}

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

@app.get("/api/event")
def api_event_compat():
    # accept old querystring style from Liquidsoap
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title": request.args.get("title", ""),
        "artist": request.args.get("artist", ""),
        "album": request.args.get("album", ""),
        "filename": request.args.get("filename", ""),
        # let the UI compute art if not provided
    }
    HISTORY.insert(0, ev)
    del HISTORY[200:]
    return jsonify({"ok": True, "stored": ev})

@app.get("/api/now")
def api_now():
    data = read_now() or {}
    if data.get("title") or data.get("artist") or data.get("album") or data.get("filename"):
        return jsonify({
            "type": "song",
            "time": int(time.time() * 1000),
            "title": data.get("title","Unknown"),
            "artist": data.get("artist",""),
            "album": data.get("album",""),
            "filename": data.get("filename",""),
            "artwork_url": data.get("artwork_url") or _build_art_url(data.get("filename"))
        })
    # fallback: newest song we logged
    for ev in HISTORY:
        if ev.get("type") == "song":
            return jsonify(ev)
    # last resort
    return jsonify({
        "type": "song","time": int(time.time()*1000),
        "title":"Unknown","artist":"","album":"","filename":"",
        "artwork_url": url_for("static", filename="station-cover.jpg", _external=True)
    })

@app.get("/api/next")
def api_next():
    # naive heuristic: anything in HISTORY after the first 'song'
    seen_current = False
    nxt = []
    for ev in HISTORY:
        if ev.get("type") == "song":
            if not seen_current:
                seen_current = True
            else:
                nxt.append(ev)
        if len(nxt) >= 3:
            break
    return jsonify(nxt)

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
    payload = request.get_json(silent=True) or request.form or request.args
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title":  (payload.get("title")  or "").strip(),
        "artist": (payload.get("artist") or "").strip(),
        "album":  (payload.get("album")  or "").strip(),
        "filename": (payload.get("filename") or "").strip(),
    }
    if ev["title"] or ev["filename"]:
        HISTORY.appendleft(ev)        # newest first
        while len(HISTORY) > 200:
            HISTORY.pop()
    return jsonify({"ok": True})

from flask import jsonify, request
import os, time, re, subprocess, requests

ANSI = re.compile(r'\x1B\[[0-9;?]*[ -/]*[@-~]')
TTS_DIR = "/opt/ai-radio/tts"
VOICE   = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"

@app.post("/api/dj-now")
def api_dj_now():
    os.makedirs(TTS_DIR, exist_ok=True)
    ts = int(time.time())

    # 1) What's next? (fall back to what's playing)
    try:
        base = request.host_url.rstrip('/')  # respects your real port
        nxt = requests.get(f"{base}/api/next", timeout=3).json()  # typically a list
        if isinstance(nxt, list) and nxt:
            cand = nxt[0]  # first upcoming track
        elif isinstance(nxt, dict):  # if your /api/next returns a single dict
            cand = nxt
        else:
            cand = {}
    except Exception:
        cand = {}

    # Fallback to "now" if we didn't get anything for next
    if not cand:
        try:
            now = requests.get(f"{base}/api/now", timeout=3).json()
        except Exception:
            now = {}
        cand = now or {}

    title  = cand.get("title")  or "Unknown Title"
    artist = cand.get("artist") or "Unknown Artist"

    # 2) Generate a DJ line (Ollama/OpenAI is inside your script)
    try:
        out = subprocess.check_output(
            ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist],
            stderr=subprocess.DEVNULL, timeout=60
        ).decode("utf-8", "ignore").strip()
    except Exception:
        out = f"That was '{title}' by {artist}."

    line = ANSI.sub('', out)  # strip any ANSI control codes

    # 3) TTS via Piper → WAV → MP3 (if ffmpeg available)
    wav = os.path.join(TTS_DIR, f"intro_{ts}.wav")
    mp3 = os.path.join(TTS_DIR, f"intro_{ts}.mp3")
    audio_url = None
    try:
        subprocess.check_call(
            ["piper", "--model", VOICE, "--output_file", wav],
            input=line.encode("utf-8"), timeout=60
        )
        try:
            subprocess.check_call(
                ["ffmpeg", "-nostdin", "-y", "-i", wav, "-codec:a", "libmp3lame", "-q:a", "3", mp3],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60
            )
            audio_url = f"/tts/{os.path.basename(mp3)}"

            # NEW — push to Liquidsoap
            uri = f"file://{mp3}"
            subprocess.run(
                ["nc", "127.0.0.1", "1234"],
                input=f"tts.push {uri}\n".encode(),
                check=True
            )

        except Exception:
            audio_url = f"/tts/{os.path.basename(wav)}"
            uri = f"file://{wav}"
            subprocess.run(
                ["nc", "127.0.0.1", "1234"],
                input=f"tts.push {uri}\n".encode(),
                check=True
            )
    except Exception:
        pass  # fall back to text-only event

    # 4) Show it in the UI timeline immediately
    push_event({
        "type": "dj",
        "text": line,
        "audio_url": audio_url,   # UI will render <audio> if present
        "time": int(time.time() * 1000),
    })

    return jsonify(ok=True, queued_text=line, audio_url=audio_url), 200

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
            audio = MutaFile(fpath)
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