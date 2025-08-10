#!/usr/bin/env python3
import os, json, glob, socket, subprocess, time, re, hashlib
from datetime import datetime
from urllib.parse import quote
from flask import Flask, jsonify, request, send_from_directory, render_template_string, send_file, abort

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
NOW_JSON   = "/opt/ai-radio/now.json"           # optional (preferred)
NOW_TXT    = "/opt/ai-radio/nowplaying.txt"     # fallback key=value
LIB_ALL    = "/opt/ai-radio/library_all.m3u"
TTS_DIR    = "/opt/ai-radio/tts_queue"
GEN_SCRIPT = "/opt/ai-radio/gen_dj_clip.sh"

LOG_DIR    = "/opt/ai-radio/logs"
DJ_LOG     = os.path.join(LOG_DIR, "dj-now.log")
COVER_CACHE = "/opt/ai-radio/ui/cache/covers"

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

# ── App ─────────────────────────────────────────────────────────
app = Flask(__name__)

# Optional: existing admin blueprint
try:
    from radio_admin import bp as radio_admin_bp
    app.register_blueprint(radio_admin_bp)
except Exception as e:
    print(f"[WARN] radio_admin blueprint not loaded: {e}")

# ── Utils ───────────────────────────────────────────────────────
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
    if not out and ";" in text:
        for part in text.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k].strip()
                out[k.strip()] = v.strip().strip('"')
    return out

def read_now() -> dict:
    data = {}

    # Preferred JSON writer (if your liquidsoap script writes it)
    if os.path.exists(NOW_JSON):
        try:
            with open(NOW_JSON, "r") as f:
                j = json.load(f)
            if isinstance(j, dict):
                data.update(j)
        except Exception as e:
            print(f"[WARN] failed to read {NOW_JSON}: {e}")

    # Fallback text file (key=value)
    if os.path.exists(NOW_TXT):
        try:
            with open(NOW_TXT, "r") as f:
                kv = parse_kv_text(f.read())
            for k in ("title","artist","album","artwork_url","started_at","duration","filename","path","file"):
                if k not in data and k in kv:
                    data[k] = kv[k]
        except Exception as e:
            print(f"[WARN] failed to read {NOW_TXT}: {e}")

    # Last resort: ask telnet for metadata
    if not data.get("title") or not data.get("artist"):
        try:
            raw = telnet_cmd("AI_Plex_DJ.metadata")
            kv = parse_kv_text(raw)
            mapping = {"name": "title", "song": "title", "cover": "artwork_url", "filename":"filename", "path":"filename", "file":"filename"}
            for src, dst in mapping.items():
                if src in kv and dst not in data:
                    data[dst] = kv[src]
            for k in ("title","artist","album","artwork_url","started_at","duration","filename"):
                if k in kv and k not in data:
                    data[k] = kv[k]
            data.setdefault("metadata_raw", raw)
        except Exception as e:
            print(f"[WARN] telnet metadata failed: {e}")

    # Normalize numbers / timestamps
    if "duration" in data:
        try:
            data["duration"] = int(float(data["duration"]))
        except Exception:
            data.pop("duration", None)

    if "started_at" in data:
        try:
            if re.fullmatch(r"\d{10}(\.\d+)?", str(data["started_at"])):
                data["started_at"] = datetime.utcfromtimestamp(float(data["started_at"])).isoformat() + "Z"
        except Exception:
            pass

    data.setdefault("title", "Unknown title")
    data.setdefault("artist", "Unknown artist")
    return data

def list_up_next(limit=10):
    items = []
    try:
        raw = telnet_cmd("library_all.m3u.next")
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        for l in lines[:limit]:
            items.append({"title": l})
    except Exception:
        pass

    if not items and os.path.exists(LIB_ALL):
        try:
            with open(LIB_ALL, "r", errors="ignore") as f:
                for path in f:
                    path = path.strip()
                    if not path or path.startswith("#"):
                        continue
                    items.append({"title": os.path.basename(path)})
                    if len(items) >= limit: break
        except Exception as e:
            print(f"[WARN] failed to read {LIB_ALL}: {e}")

    return items[:limit]

# ── Album art helpers ───────────────────────────────────────────
def _cover_from_tags(path: str):
    if not _MUTAGEN_OK:
        return None
    try:
        audio = MFile(path)
    except Exception:
        audio = None
    if audio is None:
        return None

    # MP3: ID3 APIC
    try:
        if getattr(audio, "tags", None):
            apics = [v for _, v in audio.tags.items() if isinstance(v, APIC)]
            if apics:
                return apics[0].data, (apics[0].mime or "image/jpeg")
    except Exception:
        pass

    # FLAC
    try:
        if isinstance(audio, FLAC) and audio.pictures:
            pic = audio.pictures[0]
            return pic.data, (pic.mime or "image/jpeg")
    except Exception:
        pass

    # MP4/M4A common tags
    try:
        covr = None
        if getattr(audio, "tags", None):
            covr = audio.tags.get("covr") or audio.tags.get("----:com.apple.iTunes:cover")
        if covr:
            data = covr[0] if isinstance(covr, list) else covr
            return bytes(data), "image/jpeg"
    except Exception:
        pass

    return None

# ── Static / Index ──────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/tts/<path:filename>")
def serve_tts(filename):
    return send_from_directory(TTS_DIR, filename, conditional=True)

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "ts": int(time.time())})

# ── API: Now / Next / TTS ───────────────────────────────────────
@app.get("/api/now")
def api_now():
    return jsonify(read_now())

@app.get("/api/next")
def api_next():
    return jsonify(list_up_next(limit=10))

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
        items.append({
            "text": text,
            "audio_url": f"/tts/{quote(os.path.basename(wav))}",
            "file": os.path.basename(wav)
        })
    return jsonify(items)

@app.post("/api/tts_queue")
def api_tts_enqueue():
    try:
        data = request.get_json(force=True, silent=True) or {}
        # fix .trim(): Python uses .strip()
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "No text provided"}), 400
        os.makedirs(TTS_DIR, exist_ok=True)
        stamp = int(time.time())
        txt_path = os.path.join(TTS_DIR, f"dj_{stamp}.txt")
        with open(txt_path, "w") as f:
            f.write(text + "\n")
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

# ── API: Generate DJ now (async) ────────────────────────────────
import threading
def _bg_gen():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        env = dict(os.environ)
        env["PATH"] = env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        with open(DJ_LOG, "ab", buffering=0) as lf:
            lf.write(f"\n\n==== DJ NOW @ {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n".encode())
            subprocess.Popen(
                ["bash", "-lc", f"cd /opt/ai-radio && {GEN_SCRIPT}"],
                stdout=lf, stderr=lf, env=env
            )
    except Exception as e:
        with open(DJ_LOG, "ab", buffering=0) as lf:
            lf.write(f"[ERROR] {e}\n".encode())

@app.post("/api/dj-now")
def api_dj_now():
    t = threading.Thread(target=_bg_gen, daemon=True)
    t.start()
    return jsonify({"ok": True, "started": True, "ts": int(time.time()), "log": DJ_LOG})

# ── API: Album cover from embedded tags (with cache) ────────────
@app.get("/api/cover")
def api_cover():
    """
    GET /api/cover?file=/abs/path/to/song.ext
    Returns embedded cover art (cached) or 404 if none.
    """
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

    found = _cover_from_tags(fpath) if _MUTAGEN_OK else None
    if not found:
        return abort(404)

    data, mime = found
    ext = ".jpg" if "jpeg" in (mime or "").lower() else ".png"
    out = os.path.join(COVER_CACHE, key + ext)
    with open(out, "wb") as w:
        w.write(data)
    return send_file(out, mimetype="image/jpeg" if ext == ".jpg" else "image/png", conditional=True)

# ── Legacy debug page ───────────────────────────────────────────
@app.get("/old")
def old_page():
    html = """
    <html><head>
      <meta name=viewport content="width=device-width,initial-scale=1"/>
      <style>
        body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;max-width:900px;margin:24px auto;padding:0 12px}
        .row{display:flex;gap:16px;flex-wrap:wrap}
        .card{flex:1 1 280px;border:1px solid #e5e7eb;border-radius:10px;padding:16px}
        button{padding:8px 12px;border-radius:8px;border:1px solid #ddd;cursor:pointer}
        li{margin:8px 0}
        pre{white-space:pre-wrap;word-wrap:break-word;background:#f6f8fa;padding:8px;border-radius:6px}
      </style>
    </head><body>
      <h2>AI Plex DJ — Status</h2>
      <div class="row">
        <div class="card">
          <h3>Now Playing</h3>
          <div id="now">Loading…</div>
        </div>
        <div class="card">
          <h3>Up Next</h3>
          <div id="next">Loading…</div>
          <div style="margin-top:12px">
            <button onclick="act('skip')">Skip Track</button>
            <button onclick="act('dj-now')">Trigger DJ Now</button>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:16px">
        <h3>AI-DJ Queue (latest)</h3>
        <div id="tts">Loading…</div>
      </div>
      <script>
        async function load() {
          const now = await fetch('/api/now').then(r=>r.json());
          document.getElementById('now').innerHTML =
            '<pre>'+ (JSON.stringify(now, null, 2)) +'</pre>';
          const nxt = await fetch('/api/next').then(r=>r.json());
          document.getElementById('next').innerText = (Array.isArray(nxt)?nxt.map(x=>x.title).join('\\n'):JSON.stringify(nxt));
          const tts = await fetch('/api/tts_queue').then(r=>r.json());
          let html = '<ul>';
          for (const item of (tts||[])) {
            html += '<li><b>'+item.file+'</b><br/><i>'+item.text+'</i></li>';
          }
          html += '</ul>';
          document.getElementById('tts').innerHTML = html;
        }
        async function act(a){
          await fetch('/api/'+a, {method:'POST'});
          setTimeout(load, 1200);
        }
        load(); setInterval(load, 5000);
      </script>
    </body></html>
    """
    return render_template_string(html)

# ── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host=HOST, port=PORT)