#!/usr/bin/env python3
import os, json, glob, socket, subprocess, time, re, hashlib
from datetime import datetime
from urllib.parse import quote
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from flask import request
from pathlib import Path
import re
from mutagen import File as MutaFile
from PIL import Image
import io, hashlib, os

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

COVER_CACHE = Path("/opt/ai-radio/cache/covers")
COVER_CACHE.mkdir(parents=True, exist_ok=True)

HISTORY = []      
UPCOMING = []       
TTS_QUEUE_VIEW = []

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
NOW_JSON   = "/opt/ai-radio/now.json"
NOW_TXT    = "/opt/ai-radio/nowplaying.txt"
TTS_DIR    = "/opt/ai-radio/tts_queue"
GEN_SCRIPT = "/opt/ai-radio/gen_dj_clip.sh"

LOG_DIR    = "/opt/ai-radio/logs"
DJ_LOG     = os.path.join(LOG_DIR, "dj-now.log")

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

def push_event(ev):
    # normalize timestamp to ms
    now_ms = int(time.time() * 1000)
    if isinstance(ev.get("time"), (int, float)) and ev["time"] < 10_000_000_000:
        ev["time"] = int(ev["time"] * 1000)

    # filename/title/artist normalization (kept from your version)
    if ev.get("type") == "song":
        title = (ev.get("title") or "").strip()
        artist = (ev.get("artist") or "").strip()
        fn = (ev.get("filename") or "")
        if not title and fn:
            # quick filename fallbacks: "Artist - Title.mp3" or folder/name.mp3
            import re
            m = re.search(r'([^/\\]+?)\s*-\s*([^/\\]+?)\.(mp3|flac|m4a|wav)$', fn, re.I)
            if m:
                artist = artist or m.group(1)
                title  = title  or m.group(2)
        ev["artist"] = artist or "Unknown Artist"
        ev["title"]  = title  or "Unknown"

    # ---- de-duplicate recent identical events ----
    if HISTORY:
        last = HISTORY[0]
        # song de-dupe (title+artist+filename within window)
        if ev.get("type") == "song" and last.get("type") == "song":
            same = (
                (ev.get("title") or "") == (last.get("title") or "") and
                (ev.get("artist") or "") == (last.get("artist") or "") and
                (ev.get("filename") or "") == (last.get("filename") or "")
            )
            if same and (now_ms - int(last.get("time", now_ms))) < DEDUP_WINDOW_MS:
                return  # drop duplicate

        # DJ de-dupe (text within short window)
        if ev.get("type") == "dj" and last.get("type") == "dj":
            if (ev.get("text") or "") == (last.get("text") or "") and \
               (now_ms - int(last.get("time", now_ms))) < 5000:
                return  # drop duplicate

    HISTORY.insert(0, ev)
    del HISTORY[200:]

# ── Routes ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/api/history")
def history():
    return jsonify(HISTORY)


@app.get("/api/now")
def now():
    # newest song
    for ev in HISTORY:
        if ev.get("type") == "song":
            return jsonify(ev)
    return jsonify({})

@app.get("/api/next")
def next_():
    return jsonify(UPCOMING)

@app.get("/api/tts_queue")
def tts_queue():
    # We already log DJ items into HISTORY; keep this for UI compatibility.
    # Optionally, mirror recent DJ events:
    items = [e for e in HISTORY if e.get("type") == "dj"][:5]
    return jsonify(items)

@app.get("/api/cover")
def cover():
    fp = request.args.get("file", "")
    if not fp or not os.path.exists(fp):
        return send_file("/opt/ai-radio/static/no-cover.png", mimetype="image/png")

    key = hashlib.sha1(fp.encode()).hexdigest() + ".png"
    out = COVER_CACHE / key
    if out.exists():
        return send_file(out, mimetype="image/png")

    try:
        mf = MutaFile(fp)
        art_bytes = None
        # MP3: APIC frames
        if hasattr(mf, "tags") and mf.tags:
            for k in mf.tags.keys():
                v = mf.tags[k]
                # mutagen/id3: APIC: data in .data
                if k.startswith("APIC") and hasattr(v, "data"):
                    art_bytes = v.data; break
        # MP4/M4A: covr
        if not art_bytes and mf and mf.tags and "covr" in mf.tags:
            art_bytes = bytes(mf.tags["covr"][0])

        if art_bytes:
            img = Image.open(io.BytesIO(art_bytes)).convert("RGB")
            img.thumbnail((600,600))
            img.save(out, format="PNG")
            return send_file(out, mimetype="image/png")
    except Exception:
        pass

    return send_file("/opt/ai-radio/static/no-cover.png", mimetype="image/png")

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
        telnet_cmd("skip")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.post("/api/log_event")
def log_event():
    data = request.get_json(force=True)
    push_event(data)
    return {"ok": True}

# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host=HOST, port=PORT)