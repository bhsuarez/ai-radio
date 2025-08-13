# /opt/ai-radio/ui/radio_admin.py
from flask import Blueprint, jsonify, render_template_string, send_file, redirect
import os, subprocess, requests
from requests.auth import HTTPBasicAuth

bp = Blueprint("radio_admin", __name__, url_prefix="/radio")

# --- Config via env ---
ICECAST_HOST = os.getenv("ICECAST_HOST", "http://127.0.0.1:8000").rstrip("/")
ICECAST_USER = os.getenv("ICECAST_USER", "")
ICECAST_PASS = os.getenv("ICECAST_PASS", "")
ICECAST_SERVICE = os.getenv("ICECAST_SERVICE", "icecast2")
LIQUIDSOAP_SERVICE = os.getenv("LIQUIDSOAP_SERVICE", "liquidsoap")
PLAYLIST_M3U = os.getenv("PLAYLIST_M3U", "/opt/ai-radio/library_all.m3u")

# --- Helpers ---
def _run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()

def svc_status(name):
    return {
        "active": _run(["systemctl", "is-active", name]),
        "enabled": _run(["systemctl", "is-enabled", name]),
    }

def icecast_stats():
    url = f"{ICECAST_HOST}/admin/stats?json=1"
    try:
        auth = HTTPBasicAuth(ICECAST_USER, ICECAST_PASS) if ICECAST_USER else None
        r = requests.get(url, auth=auth, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# --- APIs ---
@bp.get("/status")
def status():
    return jsonify({
        "services": {
            ICECAST_SERVICE: svc_status(ICECAST_SERVICE),
            LIQUIDSOAP_SERVICE: svc_status(LIQUIDSOAP_SERVICE),
        },
        "icecast": icecast_stats(),
    })

@bp.get("/playlist.m3u")
def playlist_file():
    return send_file(
        PLAYLIST_M3U,
        mimetype="audio/x-mpegurl",
        as_attachment=True,
        download_name="playlist.m3u"
    )

# --- Frontend ---
@bp.get("/")
def radio_home():
    return redirect("/radio/player", code=302)

@bp.get("/player")
def radio_player():
    return render_template_string("""
<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radio Admin — Player</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;margin:24px;max-width:1100px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .card{border:1px solid #e5e7eb;border-radius:12px;padding:16px}
  .full{grid-column:1 / -1}
  h3{margin:.2rem 0 .6rem}
  pre{background:#f6f8fa;padding:10px;border-radius:8px;white-space:pre-wrap}
  ul{margin:.4rem 0;padding-left:1.2rem}
  li{margin:.25rem 0}
  .k{opacity:.7}
  .ok{color:#0a7}
  .bad{color:#b00}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  code{background:#f6f8fa;padding:2px 6px;border-radius:6px}
  a.btn, button{padding:8px 12px;border:1px solid #ddd;border-radius:8px;text-decoration:none;display:inline-block}
</style>

<h2>Radio Admin</h2>
<div class="row k">
  <button id="refresh">Refresh</button>
  <div id="ts"></div>
  <a class="btn" href="/radio/playlist.m3u" download>Download M3U</a>
  <a class="btn" target="_blank" href="/radio/status">Raw Status JSON</a>
</div>

<div class="grid">
  <div class="card">
    <h3>Now Playing</h3>
    <div id="now"><span class="k">Loading…</span></div>
  </div>

  <div class="card">
    <h3>Service Status</h3>
    <div>Icecast: <b id="svc_ice">…</b></div>
    <div>Liquidsoap: <b id="svc_liq">…</b></div>
  </div>

  <div class="card full">
    <h3>Recently Generated DJ Clips</h3>
    <div id="tts"><span class="k">Loading…</span></div>
  </div>

  <div class="card full">
    <h3>Upcoming (from M3U)</h3>
    <div class="k">Source: <code>/radio/playlist.m3u</code></div>
    <div id="upnext"><span class="k">Loading…</span></div>
  </div>
</div>

<script>
async function fetchText(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(await r.text());
  return await r.text();
}
async function fetchJSON(url){
  const r = await fetch(url);
  return await r.json();
}

function parseM3U(text, limit=40){
  const lines = text.split(/\\r?\\n/).map(l=>l.trim()).filter(Boolean);
  const items = [];
  for(let i=0;i<lines.length;i++){
    const l = lines[i];
    if(l.startsWith('#EXTINF:')){
      const info = l.split(',', 2)[1] || '';
      const path = (i+1 < lines.length) ? lines[i+1] : '';
      items.push(info || path.split('/').pop());
      i++;
    } else if(!l.startsWith('#')){
      items.push(l.split('/').pop());
    }
    if(items.length >= limit) break;
  }
  return items;
}

async function load(){
  // status + service states
  const status = await fetchJSON('/radio/status');
  const svcIce = status.services ? (status.services[Object.keys(status.services)[0]] || {}) : {};
  const svcLiq = status.services ? (status.services[Object.keys(status.services)[1]] || {}) : {};
  const iceActive = svcIce.active || '(unknown)';
  const liqActive = svcLiq.active || '(unknown)';
  const iceEl = document.getElementById('svc_ice');
  const liqEl = document.getElementById('svc_liq');
  iceEl.textContent = iceActive; iceEl.className = (iceActive==='active')?'ok':'bad';
  liqEl.textContent = liqActive; liqEl.className = (liqActive==='active')?'ok':'bad';

  // now playing (from your existing API)
  const now = await fetchJSON('/api/now');
  const meta = (now.metadata_raw || '').trim();
  document.getElementById('now').innerHTML = meta ? '<pre>'+meta+'</pre>' : '<span class="k">(no metadata)</span>';

  // recent DJ wavs (from your existing API)
  const tts = await fetchJSON('/api/tts_queue');
  const list = (tts.tts_files||[]).slice(0,15).map(x => '<li><b>'+x.wav+'</b>' + (x.text?(' — <i>'+x.text+'</i>'):'') + '</li>').join('');
  document.getElementById('tts').innerHTML = list ? ('<ul>'+list+'</ul>') : '<span class="k">(none found)</span>';

  // upcoming from M3U
  try{
    const m3u = await fetchText('/radio/playlist.m3u');
    const items = parseM3U(m3u, 40);
    const uphtml = items.length ? ('<ol>'+items.map(x=>'<li>'+x+'</li>').join('')+'</ol>') : '<span class="k">(playlist empty)</span>';
    document.getElementById('upnext').innerHTML = uphtml;
  }catch(e){
    document.getElementById('upnext').innerHTML = '<span class="bad">Could not load M3U: '+e.message+'</span>';
  }

  document.getElementById('ts').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

function setImgOnce(id, url) {
  const el = document.getElementById(id);
  if (!el) return;
  // Only set if different, to avoid flicker + re-request storms
  if (el.src !== url) el.src = url;
  // Fallback if the image 404s
  el.onerror = function() {
    // point this to a real file you have available in your static folder
    this.onerror = null;
    this.src = '/static/img/placeholder-art.svg';
  }
}

document.getElementById('refresh').addEventListener('click', load);
load(); setInterval(load, 5000);
</script>
""")