# Prefer ffmpeg over mad for decoding MP3/metadata with embedded images
set("decoder.mad", false)
set("decoder.ffmpeg", true)
set("decoder.faad", true)

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

# Load everything from the folder
all_music = playlist(
  mode="random",
  reload=300,
  reload_mode="watch",
  "/opt/ai-radio/library_clean.m3u"
)

# Announce every song to the Flask backend (non-blocking)
def announce_song(m)
  artist = if list.mem("artist", list.map(fst, m)) then m["artist"] else "Unknown Artist" end
  title  = if list.mem("title",  list.map(fst, m)) then m["title"]  else "Unknown"        end
  album  = if list.mem("album",  list.map(fst, m)) then m["album"]  else ""               end
  file   = if list.mem("filename", list.map(fst, m)) then m["filename"]
           else if list.mem("file", list.map(fst, m)) then m["file"] else "" end end
  ignore(process.run("python3 /opt/ai-radio/post_event.py \"" ^
                     artist ^ "\" \"" ^ title ^ "\" \"" ^ album ^ "\" \"" ^ file ^ "\""))
end

# Simple log when sine fallback is active (kept tiny)
def log_sine(_)
  log("Sine wave fallback active.")
end
sine_src = source.on_metadata(sine(), log_sine)

# --- Non-blocking Auto-DJ scheduled for NEXT transition ----------------------
def after_song(m)
  artist = if list.mem("artist", list.map(fst, m)) then m["artist"] else "Unknown Artist" end
  title  = if list.mem("title",  list.map(fst, m)) then m["title"]  else "Unknown Title"  end
  dur_s  = if list.mem("duration", list.map(fst, m)) then float_of_string(m["duration"]) else 0. end

  # fire ~3s before end (or 1s if duration missing)
  lead   = 3.0
  wait_s = if dur_s > lead then dur_s - lead else 1.0 end

  # DEBUG: write a log line every time the hook schedules
  log("AUTO-DJ scheduling in " ^ string_of(int_of_float(wait_s)) ^
      "s for next track intro: " ^ title ^ " — " ^ artist)

  # run enqueue script in the background and log its output
  ignore(process.run(
    "bash -lc \"(sleep " ^ string_of(int_of_float(wait_s)) ^
    "; /opt/ai-radio/dj_enqueue.sh \\\"" ^ artist ^ "\\\" \\\"" ^ title ^ "\\\" " ^
    ">>/var/tmp/dj_enqueue.log 2>&1) &\""
  ))
end

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

# --- Chain / processing -------------------------------------------------------
# Apply metadata updates to music source
all_music = metadata.map(update_metadata, all_music)

# Add metadata announcements
all_music = source.on_metadata(all_music, announce_song)

# Add DJ intro generator (commented out for now to test basic functionality)
# all_music = source.on_metadata(all_music, after_song)

# Smooth transitions so the mount never drops between items
smooth_music = crossfade(all_music)

# Primary source with DJ inserts
primary = fallback(track_sensitive=true, [tts_q, smooth_music])

# Final radio output with sine fallback
radio = fallback(track_sensitive=false, [primary, sine_src])

# --- Output -------------------------------------------------------------------
output.icecast(
  %mp3,
  host="127.0.0.1",
  port=8000,
  user="source",
  password="passw0rd",
  mount="/stream.mp3",
  name="AI Plex DJ",
  url="http://192.168.1.146:8000/",
  genre="Mixed",
  description="24/7 auto-DJ",
  radio
)
