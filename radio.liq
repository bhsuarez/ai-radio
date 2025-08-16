# Decoder preferences (updated for Liquidsoap 2.3.x)
settings.decoder.priorities.ffmpeg := 10
settings.decoder.priorities.mad := 1

# Core / logging
settings.init.allow_root := true

# Reduce log noise - set to WARN level
settings.log.level := 4

# Telnet control (needed for tts queue pushes)
settings.server.telnet := true
settings.server.telnet.bind_addr := "127.0.0.1"
settings.server.telnet.port := 1234

# --- Sources -----------------------------------------------------------------
# TTS queue with proper command exposure
tts_q = request.queue(id="tts")

# Make sure the queue commands are available via telnet
# (This should happen automatically with id="tts" but let's be explicit)

# Load everything from the folder
all_music = playlist(
  mode="random",
  reload=300,
  reload_mode="watch",
  "/opt/ai-radio/library_clean.m3u"
)

def meta_get(m, k, d)
  if list.mem(k, list.map(fst, m)) then list.assoc(k, m) else d end
end

def announce_song(m)
  artist = meta_get(m, "artist", "Unknown artist")
  title  = meta_get(m, "title",  "Unknown title")
  album  = meta_get(m, "album",  "")
  file   = if list.mem("filename", list.map(fst, m)) then list.assoc("filename", m)
           else if list.mem("file", list.map(fst, m)) then list.assoc("file", m) else "" end end

  qs = "type=song"
       ^ "&artist=" ^ url.encode(artist)
       ^ "&title="  ^ url.encode(title)
       ^ "&album="  ^ url.encode(album)
       ^ "&filename=" ^ url.encode(file)

  # fire‑and‑forget so audio never blocks
  cmd = "bash -lc \"(curl -fsS 'http://127.0.0.1:5055/api/event?" ^ qs ^
        "' || wget -qO- 'http://127.0.0.1:5055/api/event?" ^ qs ^
        "') >/dev/null 2>&1 &\""
  ignore(process.run(cmd))
end

# (re)attach the hook
all_music = source.on_metadata(all_music, announce_song)

# Simple log when sine fallback is active (kept tiny)
def log_sine(_)
  log("Sine wave fallback active.")
end
sine_src = source.on_metadata(sine(), log_sine)

# --- Chain / processing -------------------------------------------------------
# Apply smooth crossfade to music
smooth_music = crossfade(all_music)

# Attach metadata handlers to the smooth music
smooth_music = source.on_metadata(smooth_music, announce_song)
#smooth_music = source.on_metadata(smooth_music, after_song)

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

# Apply metadata updates to music
music = metadata.map(update_metadata, smooth_music)

# DJ queue source - strip silence and make sure it's properly configured
dj = blank.strip(max_blank=0.1, tts_q)

# Create the main radio source with proper fallback hierarchy
# DJ fills have priority, then music, then sine as emergency fallback
radio = fallback(
  track_sensitive=false,
  [dj, music, sine_src]
)

# --- Output -------------------------------------------------------------------
output.icecast(
  %mp3(id3v2=true),
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