#!/usr/bin/env python3
import os, json, glob, socket, subprocess, time, re, hashlib
from datetime import datetime
from urllib.parse import quote
from flask import Flask, jsonify, request, send_from_directory, send_file, abort

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
COVER_CACHE = "/opt/ai-radio/ui/cache/covers"

HISTORY_FILE = "/opt/ai-radio/play_history.json"
MAX_HISTORY = 100

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(COVER_CACHE, exist_ok=True)

# Optional dependency (album art)
_MUTAGEN_OK = True
try:
    from mutagen import File as MFile
    from mutagen.id3 import APIC
    from mutagen.flac import FLAC
except Exception:
    _MUTAGEN_OK = False

app = Flask(__name__)

# ── Helpers ─────────────────────────────────────────────────────
def add_to_history(entry):
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except Exception:
            history = []
    if not history or (
        history[0].get("type") != entry.get("type") or
        history[0].get("title") != entry.get("title") or
        history[0].get("text") != entry.get("text")
    ):
        entry["time"] = datetime.utcnow().isoformat() + "Z"
        history.insert(0, entry)
    history = history[:MAX_HISTORY]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

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
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out

def read_now() -> dict:
    data = {}
    # Preferred: NOW_JSON
    if os.path.exists(NOW_JSON):
        try:
            with open(NOW_JSON, "r") as f:
                j = json.load(f)
            if isinstance(j, dict):
                data.update(j)
        except Exception:
            pass
    # Fallback: NOW_TXT
    if not data.get("title") and os.path.exists(NOW_TXT):
        try:
            with open(NOW_TXT, "r") as f:
                kv = parse_kv_text(f.read())
            for k in ("title","artist","album","artwork_url","started_at","duration","filename"):
                if k not in data and k in kv:
                    data[k] = kv[k]
        except Exception:
            pass
    # New fallback: Telnet metadata
    if not data.get("title"):
        try:
            raw = telnet_cmd("metadata")
            kv = parse_kv_text(raw)
            data["title"] = kv.get("title") or kv.get("song") or "Unknown title"
            data["artist"] = kv.get("artist") or "Unknown artist"
            data["album"] = kv.get("album") or ""
            data["filename"] = kv.get("filename") or kv.get("file") or ""
        except Exception:
            pass
    data.setdefault("title", "Unknown title")
    data.setdefault("artist", "Unknown artist")
    return data

# ── Routes ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/history")
def api_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify([])

@app.route("/api/now")
def api_now():
    now = read_now()
    if "artwork_url" not in now or not now["artwork_url"]:
        if "filename" in now and now["filename"]:
            cover_url = request.url_root.rstrip("/") + "/api/cover?file=" + quote(now["filename"])
            now["artwork_url"] = cover_url
        else:
            now["artwork_url"] = request.url_root.rstrip("/") + "/static/no-cover.png"
    add_to_history({
        "type": "song",
        "title": now.get("title"),
        "artist": now.get("artist"),
        "album": now.get("album"),
        "filename": now.get("filename"),
        "artwork_url": now.get("artwork_url")
    })
    return jsonify(now)

@app.get("/api/next")
def api_next():
    items = []
    try:
        raw = telnet_cmd("requests")
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        for l in lines:
            path = l.split(" ", 1)[-1] if " " in l else l
            if os.path.exists(path):
                item = {
                    "title": os.path.basename(path),
                    "filename": path
                }
                cover_url = request.url_root.rstrip("/") + "/api/cover?file=" + quote(path)
                item["artwork_url"] = cover_url
                items.append(item)
    except Exception:
        pass
    return jsonify(items)

@app.get("/api/tts_queue")
def api_tts_queue():
    items = []
    os.makedirs(TTS_DIR, exist_ok=True)
    files = sorted(
        glob.glob(os.path.join(TTS_DIR, "dj_*.wav")),
        key=os.path.getmtime,
        reverse=True
    )
    for wav in files[:20]:
        base = os.path.splitext(os.path.basename(wav))[0]
        txt_path = os.path.join(TTS_DIR, base + ".txt")
        text = ""
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r") as f:
                    text = f.read().strip()
            except Exception:
                pass
        item = {
            "type": "dj",
            "text": text,
            "audio_url": f"/tts/{quote(os.path.basename(wav))}",
            "file": os.path.basename(wav),
            "time": datetime.utcfromtimestamp(os.path.getmtime(wav)).isoformat() + "Z"
        }
        items.append(item)
    return jsonify(items)

@app.post("/api/tts_queue")
def api_tts_enqueue():
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
        add_to_history({
            "type": "dj",
            "text": text,
            "audio_url": None
        })
        return jsonify({"ok": True, "queued": os.path.basename(txt_path)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/skip")
def api_skip():
    try:
        telnet_cmd("AI_Plex_DJ.skip")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/api/cover")
def api_cover():
    fpath = request.args.get("file", "")
    if not fpath or not os.path.isabs(fpath) or not os.path.exists(fpath):
        return abort(404)
    key = hashlib.sha1(fpath.encode("utf-8")).hexdigest()
    cache_jpg = os.path.join(COVER_CACHE, key + ".jpg")
    cache_png = os.path.join(COVER_CACHE, key + ".png")
    if os.path.exists(cache_jpg):
        return send_file(cache_jpg, mimetype="image/jpeg", conditional=True)
    if os.path.exists(cache_png):
        return send_file(cache_png, mimetype="image/png", conditional=True)
    found = None
    if _MUTAGEN_OK:
        try:
            audio = MFile(fpath)
            if audio:
                if getattr(audio, "tags", None):
                    apics = [v for _, v in audio.tags.items() if isinstance(v, APIC)]
                    if apics:
                        found = (apics[0].data, (apics[0].mime or "image/jpeg"))
                if isinstance(audio, FLAC) and audio.pictures:
                    pic = audio.pictures[0]
                    found = (pic.data, (pic.mime or "image/jpeg"))
        except Exception:
            pass
    if not found:
        return abort(404)
    data, mime = found
    ext = ".jpg" if "jpeg" in (mime or "").lower() else ".png"
    out = os.path.join(COVER_CACHE, key + ext)
    with open(out, "wb") as w:
        w.write(data)
    return send_file(out, mimetype="image/jpeg" if ext == ".jpg" else "image/png", conditional=True)

# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host=HOST, port=PORT)