# --- Core / logging -----------------------------------------------------------
settings.init.allow_root.set(true)

# Reduce log noise (INFO=3). Set to WARN so server connect/disconnect msgs vanish.
settings.log.level.set(4)

# Telnet control (needed for tts queue pushes)
settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("127.0.0.1")
settings.server.telnet.port.set(1234)

# --- Sources -----------------------------------------------------------------
# Queue for TTS intros (id="tts" exposes telnet commands: tts.push / tts.skip / tts.queue)
tts_q = request.queue(id="tts")

# Music playlist
all_music = playlist(
  mode="random",
  reload=300,
  reload_mode="watch",
  "/opt/ai-radio/library_all.m3u"
)

# Announce every song to the Flask backend (non-blocking)
def announce_song(m)
  artist = if list.mem("artist", list.map(fst, m)) then m["artist"] else "Unknown Artist" end
  title  = if list.mem("title",  list.map(fst, m)) then m["title"]  else "Unknown Title"  end
  album  = if list.mem("album",  list.map(fst, m)) then m["album"]  else "" end
  file   = if list.mem("filename", list.map(fst, m)) then m["filename"] else "" end

  ignore(process.run(
    "sh -c 'curl -s -X POST -H \"Content-Type: application/json\" " ^
    " -d ''{\"type\":\"song\",\"time\":" ^ string_of(time()) ^
    ",\"title\":\"" ^ title ^ "\",\"artist\":\"" ^ artist ^
    "\",\"album\":\"" ^ album ^ "\",\"filename\":\"" ^ file ^ "\"}'' " ^
    " http://127.0.0.1:5000/api/log_event >/dev/null 2>&1 &'"
  ))
end
all_music = source.on_metadata(all_music, announce_song)

# Simple log when sine fallback is active (kept tiny)
def log_sine(_)
  log("Sine wave fallback active.")
end
sine_src = source.on_metadata(sine(), log_sine)

# --- Non-blocking Auto-DJ after every song -----------------------------------
# Use the external script to generate + enqueue without blocking playback.
def after_song(m)
  artist = if list.mem("artist", list.map(fst, m)) then m["artist"] else "Unknown Artist" end
  title  = if list.mem("title",  list.map(fst, m)) then m["title"]  else "Unknown Title"  end
  ts     = string_of(int_of_float(time()))
  file   = "/opt/ai-radio/tts/intro_" ^ ts ^ ".mp3"

  ignore(process.run(
    "nohup /opt/ai-radio/dj_enqueue.sh \"" ^ artist ^ "\" \"" ^ title ^ "\" \"" ^ file ^ "\" >/dev/null 2>&1 &"
  ))
end
all_music = source.on_metadata(all_music, after_song)

# --- Chain / processing -------------------------------------------------------
# Smooth transitions so the mount never drops between items
smooth_music = crossfade(all_music)

# Main fallback: play TTS inserts when present, else music, else sine
radio = fallback(track_sensitive=false, [tts_q, smooth_music, sine_src])

# Remove all entries for key k from association-list m
def rm_key(m, k)
  list.filter(fun (kv) -> fst(kv) != k, m)
end

# If key k is missing or empty, set it to v (ensuring only one copy)
def put_default(m, k, v)
  has = list.mem(k, list.map(fst, m))
  cur = if has then list.assoc(k, m) else "" end
  if (not has) or cur == "" then
    m2 = rm_key(m, k)
    list.append(m2, [(k, v)])
  else
    m
  end
end

def update_metadata(m)
  m = put_default(m, "title",  "Unknown")
  m = put_default(m, "artist", "Unknown Artist")
  m
end

radio = metadata.map(update_metadata, radio)

# --- Output -------------------------------------------------------------------
output.icecast(
  %mp3,
  host="127.0.0.1",
  port=8000,
  user="source",
  password="orroz.123",
  mount="/stream.mp3",
  name="AI Plex DJ",
  url="http://192.168.1.243:8000/",
  genre="Mixed",
  description="24/7 auto-DJ",
  radio
)