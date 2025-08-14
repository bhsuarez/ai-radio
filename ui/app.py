#!/usr/bin/env python3
import os, json, socket, time, hashlib, io, re, subprocess
from datetime import datetime
from pathlib import Path
from collections import deque
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
try:
    import requests
except Exception:
    requests = None
from urllib.parse import quote

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))

NOW_JSON   = "/opt/ai-radio/now.json"
NOW_TXT    = "/opt/ai-radio/nowplaying.txt"

TTS_DIR    = "/opt/ai-radio/tts"
VOICE      = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"

LOG_DIR    = "/opt/ai-radio/logs"
DJ_LOG     = os.path.join(LOG_DIR, "dj-now.log")

COVER_CACHE = Path("/opt/ai-radio/cache/covers"); COVER_CACHE.mkdir(parents=True, exist_ok=True)

HISTORY_FILE = "/opt/ai-radio/play_history.json"
MAX_HISTORY = 200
DEDUP_WINDOW_MS = 60_000  # suppress exact duplicate song events within 60s

os.makedirs(LOG_DIR, exist_ok=True)

ANSI = re.compile(r'\x1B\[[0-9;?]*[ -/]*[@-~]')

# Optional dependencies (album art)
_MUTAGEN_OK = True
try:
    from mutagen import File as MutaFile
    from mutagen.id3 import APIC
    from mutagen.flac import FLAC
except Exception:
    _MUTAGEN_OK = False

try:
    from PIL import Image  # not strictly required
except Exception:
    Image = None

app = Flask(__name__)

# ── In-memory state ─────────────────────────────────────────────
HISTORY = deque(maxlen=MAX_HISTORY)   # newest-first
UPCOMING = []                         # optional future items

# ── Telnet helpers ─────────────────────────────────────────────
def telnet_lines(cmd: str, timeout=1.5):
    """Send one command, yield lines until connection closes or 'END' is seen."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((TELNET_HOST, TELNET_PORT))
    # send command and a quit to be safe (server ignores unknown lines after END)
    s.sendall((cmd + "\n").encode())
    lines = []
    data = b""
    try:
        while True:
            chunk = s.recv(65535)
            if not chunk:
                break
            data += chunk
            while b"\n" in data:
                line, data = data.split(b"\n", 1)
                t = line.decode(errors="ignore").rstrip("\r")
                lines.append(t)
                if t.strip() == "END":
                    return lines
    except socket.timeout:
        pass
    finally:
        try: s.close()
        except: pass
    return lines

def telnet_text(cmd: str, timeout=1.5) -> str:
    return "\n".join(telnet_lines(cmd, timeout=timeout))

def parse_kv_block(lines):
    """Parse key="value" lines into dict."""
    out = {}
    for ln in lines:
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out

# ── History persistence ────────────────────────────────────────
def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                raw = json.load(f)
            HISTORY.clear()
            for ev in raw:
                HISTORY.append(ev)
    except Exception:
        HISTORY.clear()

def save_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(list(HISTORY), f)
    except Exception:
        pass

# ── “Now playing” (robust) ─────────────────────────────────────
def read_now():
    """
    Prefer Liquidsoap metadata for the active output:
      - We scan the 'output.icecast.metadata' block between --- 1 --- and END.
    Fallbacks: NOW_TXT (key=value) or a generic Unknown.
    """
    # Try Liquidsoap output.icecast.metadata
    lines = telnet_lines("output.icecast.metadata", timeout=1.5)
    # Grab the block under the first header --- 1 --- … END
    blk = []
    active = False
    for ln in lines:
        if ln.strip().startswith("--- 1 ---"):
            active = True
            continue
        if ln.strip() == "END":
            break
        if active:
            blk.append(ln)

    if blk:
        m = parse_kv_block(blk)
        data = {
            "title": m.get("title",""),
            "artist": m.get("artist",""),
            "album": m.get("album",""),
            "filename": m.get("filename","") or m.get("file",""),
        }
        return data

    # Fallback: NOW_TXT
    data = {}
    if os.path.exists(NOW_TXT):
        try:
            raw = open(NOW_TXT, "r").read().strip()
            if "=" in raw:  # key=value lines
                for ln in raw.splitlines():
                    if "=" in ln:
                        k,v = ln.split("=",1)
                        data[k.strip()] = v.strip()
            elif " - " in raw and "\n" not in raw:  # "Artist - Title"
                a, t = raw.split(" - ", 1)
                data["artist"], data["title"] = a.strip(), t.strip()
        except Exception:
            pass

    data.setdefault("title", "Unknown")
    data.setdefault("artist", "")
    data.setdefault("album", "")
    data.setdefault("filename", "")
    return data

# ── Event push (dedup + normalize) ─────────────────────────────
def push_event(ev: dict):
    now_ms = int(time.time() * 1000)

    # normalize timestamp to ms
    if isinstance(ev.get("time"), (int, float)):
        t = int(ev["time"])
        if t < 10_000_000_000:  # seconds → ms
            ev["time"] = t * 1000
    else:
        ev["time"] = now_ms

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
        if ev.get("type") == last.get("type") == "song":
            same = (
                (ev.get("title") or "") == (last.get("title") or "") and
                (ev.get("artist") or "") == (last.get("artist") or "") and
                (ev.get("filename") or "") == (last.get("filename") or "")
            )
            if same and (now_ms - int(last.get("time", now_ms))) < DEDUP_WINDOW_MS:
                return
        if ev.get("type") == last.get("type") == "dj":
            if (ev.get("text") or "") == (last.get("text") or "") and \
               (now_ms - int(last.get("time", now_ms))) < 5000:
                return

    HISTORY.appendleft(ev)
    save_history()

# ── Cover art helpers ──────────────────────────────────────────
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

    # MusicBrainz → Cover Art Archive
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

    # iTunes fallback
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
                        import re as _re
                        url = _re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url)
                        img = requests.get(url, headers=headers, timeout=timeout)
                        if img.ok and img.content:
                            return img.content, "image/jpeg"
    except Exception:
        pass

    return None

def _build_art_url(path: str) -> str:
    if path and os.path.isabs(path) and os.path.exists(path):
        return request.url_root.rstrip("/") + "/api/cover?file=" + quote(path)
    return request.url_root.rstrip("/") + "/static/station-cover.jpg"

# ── Routes ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/api/history")
def api_history():
    if not HISTORY and os.path.exists(HISTORY_FILE):
        load_history()
    return jsonify(list(HISTORY))

@app.get("/api/event")
def api_event_compat():
    # Accept old Liquidsoap GET/qs style
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title": request.args.get("title", ""),
        "artist": request.args.get("artist", ""),
        "album": request.args.get("album", ""),
        "filename": request.args.get("filename", ""),
    }
    push_event(ev)
    return jsonify({"ok": True, "stored": ev})

@app.get("/api/now")
def api_now():
    now_ms = int(time.time() * 1000)
    # Prefer a recent song event we already stored
    for ev in HISTORY:
        if ev.get("type") == "song" and now_ms - ev["time"] < 15*60*1000:
            # backfill artwork lazily
            if not ev.get("artwork_url"):
                ev["artwork_url"] = _build_art_url(ev.get("filename"))
            return jsonify(ev)

    data = read_now() or {}
    ev = {
        "type": "song",
        "time": now_ms,
        "title":    data.get("title") or "Unknown",
        "artist":   data.get("artist") or "",
        "album":    data.get("album") or "",
        "filename": data.get("filename") or "",
        "artwork_url": data.get("artwork_url") or _build_art_url(data.get("filename")),
    }
    return jsonify(ev)

@app.get("/api/next")
def api_next():
    """
    Ask Liquidsoap what requests are queued:
      - 'request.all' returns a space-separated list of RIDs (first is current).
      - For each RID after the first, we call 'request.metadata <rid>'.
    """
    try:
        lines = telnet_lines("request.all", timeout=1.2)
        # Expect something like: "4 5" then "END"
        rid_line = ""
        for ln in lines:
            ln = ln.strip()
            if ln and ln != "END" and not ln.startswith("|") and not ln.startswith("---"):
                rid_line = ln
                break
        rids = [x for x in rid_line.split() if x.isdigit()]
        if not rids:
            return jsonify([])

        # drop the first (currently playing), keep next few
        rids = rids[1:4]

        upcoming = []
        for rid in rids:
            meta_lines = telnet_lines(f"request.metadata {rid}", timeout=1.2)
            md = parse_kv_block(meta_lines)
            # minimal normalization
            ev = {
                "title": md.get("title",""),
                "artist": md.get("artist",""),
                "album": md.get("album",""),
                "filename": md.get("filename","") or md.get("file",""),
                "artwork_url": _build_art_url(md.get("filename") or md.get("file","")),
                "time": int(time.time() * 1000) + 10000,  # fake-ish future time for UI ordering
                "type": "song",
            }
            upcoming.append(ev)

        return jsonify(upcoming)
    except Exception:
        return jsonify([])

@app.get("/api/tts_queue")
def tts_queue_get():
    items = [e for e in list(HISTORY) if e.get("type") == "dj"][:5]
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
    # Try a few reasonable skip targets; ignore failures
    for cmd in ("output.icecast.skip", "library_clean_m3u.skip"):
        try:
            telnet_text(cmd, timeout=1.0)
            return {"ok": True, "via": cmd}
        except Exception:
            pass
    return {"ok": False, "error": "No skip command succeeded"}, 500

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
        push_event(ev)
    return jsonify({"ok": True})

@app.post("/api/dj-now")
def api_dj_now():
    os.makedirs(TTS_DIR, exist_ok=True)
    ts = int(time.time())

    # What's next? (fallback to now)
    base = request.host_url.rstrip('/')
    cand = {}
    try:
        r = requests.get(f"{base}/api/next", timeout=3) if requests else None
        if r and r.ok:
            js = r.json()
            cand = (js[0] if isinstance(js, list) and js else (js if isinstance(js, dict) else {})) or {}
    except Exception:
        pass
    if not cand:
        try:
            r = requests.get(f"{base}/api/now", timeout=3) if requests else None
            cand = r.json() if (r and r.ok) else {}
        except Exception:
            cand = {}

    title  = cand.get("title")  or "Unknown Title"
    artist = cand.get("artist") or "Unknown Artist"

    # Generate a DJ line (your script handles LLM)
    try:
        out = subprocess.check_output(
            ["/opt/ai-radio/gen_ai_dj_line.sh", title, artist],
            stderr=subprocess.DEVNULL, timeout=60
        ).decode("utf-8", "ignore").strip()
    except Exception:
        out = f"That was '{title}' by {artist}."
    line = ANSI.sub('', out)

    # TTS via piper -> wav -> mp3 (optional)
    wav = os.path.join(TTS_DIR, f"intro_{ts}.wav")
    mp3 = os.path.join(TTS_DIR, f"intro_{ts}.mp3")
    audio_url = None
    try:
        subprocess.run(
            ["piper", "--model", VOICE, "--output_file", wav],
            input=line.encode("utf-8"), timeout=60, check=True
        )
        try:
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-i", wav, "-codec:a", "libmp3lame", "-q:a", "3", mp3],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True
            )
            audio_url = f"/tts/{os.path.basename(mp3)}"
            uri = f"file://{mp3}"
        except Exception:
            audio_url = f"/tts/{os.path.basename(wav)}"
            uri = f"file://{wav}"

        # push into liquidsoap request.queue named 'tts' (your radio.liq exposes tts.push)
        try:
            proc = subprocess.run(["nc", "127.0.0.1", "1234"], input=f"tts.push {uri}\n".encode(), check=True)
        except Exception:
            pass
    except Exception:
        pass

    push_event({"type":"dj","text":line,"audio_url":audio_url,"time":int(time.time()*1000)})
    return jsonify(ok=True, queued_text=line, audio_url=audio_url), 200

@app.get("/api/cover")
def api_cover():
    """
    GET /api/cover?file=/abs/path/to/song.ext
    Order:
      1) embedded tags
      2) folder images
      3) online (MusicBrainz/CAA → iTunes)
      4) default cover
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

    # cache hit
    if os.path.exists(cache_jpg):
        return send_file(cache_jpg, mimetype="image/jpeg", conditional=True)
    if os.path.exists(cache_png):
        return send_file(cache_png, mimetype="image/png", conditional=True)

    data = None
    mime = None

    # embedded art
    if _MUTAGEN_OK:
        try:
            audio = MutaFile(fpath)
            if audio:
                if getattr(audio, "tags", None):
                    # MP3
                    for _, v in audio.tags.items():
                        if isinstance(v, APIC):
                            data, mime = v.data, (v.mime or "image/jpeg"); break
                if data is None and isinstance(audio, FLAC):
                    if audio.pictures:
                        pic = audio.pictures[0]
                        data, mime = pic.data, (pic.mime or "image/jpeg")
                if data is None and getattr(audio, "tags", None):
                    covr = audio.tags.get("covr") or audio.tags.get("----:com.apple.iTunes:cover")
                    if covr:
                        b = covr[0] if isinstance(covr, list) else covr
                        data, mime = bytes(b), "image/jpeg"
        except Exception:
            pass

    # folder images
    if data is None:
        folder = os.path.dirname(fpath)
        for name in ("cover.jpg","cover.png","folder.jpg","folder.png","front.jpg","front.png"):
            p = os.path.join(folder, name)
            if os.path.exists(p):
                with open(p, "rb") as imgf:
                    data = imgf.read()
                mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
                break

    # online
    if data is None:
        artist = album = title = None
        if _MUTAGEN_OK:
            try:
                audio = MutaFile(fpath)
                if audio and getattr(audio, "tags", None):
                    title  = _first_tag(audio.tags.get("title"))  or _first_tag(audio.tags.get("TIT2"))
                    artist = _first_tag(audio.tags.get("artist")) or _first_tag(audio.tags.get("TPE1"))
                    album  = _first_tag(audio.tags.get("album"))  or _first_tag(audio.tags.get("TALB"))
            except Exception:
                pass
        if not artist or not title:
            parts = os.path.normpath(fpath).split(os.sep)
            if len(parts) >= 3:
                album = album or parts[-2]
                artist = artist or parts[-3]
                title = title or os.path.splitext(os.path.basename(fpath))[0]

        fetched = _fetch_online_cover(artist, album, title)
        if fetched:
            data, mime = fetched

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