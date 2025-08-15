#!/usr/bin/env python3
import os, json, socket, time, hashlib, re, subprocess
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from urllib.parse import quote

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = "/opt/ai-radio/play_history.json"
MAX_HISTORY  = 200
DEDUP_WINDOW_MS = 60_000

COVER_CACHE = Path("/opt/ai-radio/cache/covers"); COVER_CACHE.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# ── State ───────────────────────────────────────────────────────
HISTORY  = []   # newest first
UPCOMING = []   # optional

# ── Utils ───────────────────────────────────────────────────────
def telnet_cmd(cmd: str, timeout=1.5) -> str:
    """Send a single telnet command and close (we append 'quit')."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((TELNET_HOST, TELNET_PORT))
    s.sendall((cmd + "\nquit\n").encode())
    chunks = []
    try:
        while True:
            b = s.recv(65535)
            if not b:
                break
            chunks.append(b)
    except socket.timeout:
        pass
    finally:
        try: s.close()
        except: pass
    return (b"".join(chunks).decode(errors="ignore") or "").strip()

KV = re.compile(r'^\s*([^=\s]+)\s*=\s*"(.*)"\s*$')

def parse_kv_block(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        m = KV.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out

def first_block(body: str) -> str:
    """
    Extract the first '--- n ---' .. 'END' block Liquidsoap prints:
      --- 1 ---
      key="val"
      ...
      END
    """
    lines = (body or "").splitlines()
    grab, buf = False, []
    for ln in lines:
        if ln.startswith('--- ') and ln.endswith(' ---'):
            grab, buf = True, []
            continue
        if grab:
            if ln.strip() == "END":
                break
            buf.append(ln)
    return "\n".join(buf)

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

def push_event(ev: dict):
    """Insert newest-first with light de-duplication and persist."""
    now_ms = int(time.time() * 1000)

    # normalize timestamp (s→ms if needed)
    t = ev.get("time")
    if isinstance(t, (int, float)):
        t = int(t)
        if t < 10_000_000_000:
            t *= 1000
        ev["time"] = t
    else:
        ev["time"] = now_ms

    # normalize song fields
    if ev.get("type") == "song":
        title  = (ev.get("title") or "").strip()
        artist = (ev.get("artist") or "").strip()
        fn     = ev.get("filename") or ""
        if not title and fn:
            m = re.search(r'([^/\\]+?)\s*-\s*([^/\\]+?)\.(mp3|flac|m4a|wav)$', fn, re.I)
            if m:
                artist = artist or m.group(1)
                title  = title  or m.group(2)
        ev["artist"] = artist or "Unknown Artist"
        ev["title"]  = title  or "Unknown"

    # dedupe vs last
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

    HISTORY.insert(0, ev)
    del HISTORY[MAX_HISTORY:]
    save_history()

def _build_art_url(path: str) -> str:
    if path and os.path.isabs(path) and os.path.exists(path):
        return request.url_root.rstrip("/") + "/api/cover?file=" + quote(path)
    return request.url_root.rstrip("/") + "/static/station-cover.jpg"

# ── Liquidsoap adapters ─────────────────────────────────────────
def read_now() -> dict:
    """
    Ask Liquidsoap for the live output metadata and parse the first block.
    """
    try:
        raw = telnet_cmd("output.icecast.metadata", timeout=2)
        blk = first_block(raw)
        m = parse_kv_block(blk)
        return {
            "title":    m.get("title",""),
            "artist":   m.get("artist",""),
            "album":    m.get("album",""),
            "filename": m.get("filename") or m.get("file") or "",
        }
    except Exception:
        return {}

def read_next(max_items=3):
    """
    Read request queue:
      1) request.all  -> space-separated rid(s), e.g. "4 5"
      2) request.metadata <rid> -> key="val"
    """
    out = []
    try:
        rids_text = telnet_cmd("request.all", timeout=2)
        # often looks like: "4 5\nEND"
        rid_line = rids_text.splitlines()[0] if rids_text else ""
        rids = [x for x in re.findall(r'\d+', rid_line)]
        # skip the first RID if it's the current track; we’ll filter by comparing to now
        now_fn = (read_now() or {}).get("filename","")
        for rid in rids:
            meta_raw = telnet_cmd(f"request.metadata {rid}", timeout=2)
            m = parse_kv_block(meta_raw)
            ev = {
                "type": "song",
                "time": int(time.time() * 1000) + 1,  # UI sorts by time desc; future-ish
                "title":  m.get("title",""),
                "artist": m.get("artist",""),
                "album":  m.get("album",""),
                "filename": m.get("filename") or m.get("file") or "",
            }
            # Drop the one that's actively playing (sometimes present in request list)
            if now_fn and ev["filename"] and ev["filename"] == now_fn:
                continue
            out.append(ev)
            if len(out) >= max_items:
                break
    except Exception:
        pass
    return out

# ── Routes ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/api/history")
def api_history():
    if not HISTORY and os.path.exists(HISTORY_FILE):
        load_history()
    return jsonify(HISTORY[:MAX_HISTORY])

@app.get("/api/event")
def api_event_compat():
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
    for ev in HISTORY:
        if ev.get("type") == "song" and now_ms - int(ev.get("time", 0)) < 15*60*1000:
            return jsonify({
                **ev,
                "artwork_url": ev.get("artwork_url") or _build_art_url(ev.get("filename",""))
            })
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
    # Prefer live queue from Liquidsoap; fall back to HISTORY after current.
    nxt = read_next(max_items=3)
    if nxt:
        return jsonify(nxt)

    seen_current = False
    out = []
    for ev in HISTORY:
        if ev.get("type") == "song":
            if not seen_current:
                seen_current = True
            else:
                out.append(ev)
        if len(out) >= 3:
            break
    return jsonify(out)

@app.get("/api/tts_queue")
def tts_queue_get():
    return jsonify([e for e in HISTORY if e.get("type") == "dj"][:5])

@app.post("/api/tts_queue")
def tts_queue_post():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    push_event({"type": "dj", "text": text, "audio_url": None, "time": int(time.time()*1000)})
    return jsonify({"ok": True})

@app.post("/api/skip")
def api_skip():
    try:
        telnet_cmd("output.icecast.skip", timeout=1.5)
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
        push_event(ev)
    return jsonify({"ok": True})

@app.get("/api/cover")
def api_cover():
    fpath = request.args.get("file", "")
    default_cover_path = os.path.join(BASE_DIR, "static", "station-cover.jpg")
    if not fpath or not os.path.isabs(fpath) or not os.path.exists(fpath):
        if os.path.exists(default_cover_path):
            return send_file(default_cover_path, mimetype="image/jpeg", conditional=True)
        return abort(404)
    # tiny cache: just hash & reuse existing art if we ever add extraction later
    key = hashlib.sha1(fpath.encode("utf-8")).hexdigest()
    cached = os.path.join(COVER_CACHE, key + ".jpg")
    if os.path.exists(cached):
        return send_file(cached, mimetype="image/jpeg", conditional=True)
    # for now, default (you can drop in mutagen extraction later)
    if os.path.exists(default_cover_path):
        return send_file(default_cover_path, mimetype="image/jpeg", conditional=True)
    return abort(404)

# ── Startup ─────────────────────────────────────────────────────
load_history()

if __name__ == "__main__":
    app.run(host=HOST, port=PORT)