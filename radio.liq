# ----- Liquidsoap 2.3.x config for AI Radio -----

# Decoder prefs
settings.decoder.priorities.ffmpeg := 10
settings.decoder.priorities.mad    := 1

# Core / logging
settings.init.allow_root := true
settings.log.level       := 4  # warn

# Telnet control (used by enqueue scripts to push TTS intros)
settings.server.telnet := true
settings.server.telnet.bind_addr := "127.0.0.1"
settings.server.telnet.port := 1234

# ---------- Sources ----------
# Queue for DJ/TTS lines
tts_q = request.queue(id="tts")

# Main music (m3u that points to your library files)
all_music = playlist(
  mode="random",
  reload=300,
  reload_mode="watch",
  "/opt/ai-radio/library_clean.m3u"
)

# Helpers
def meta_get(m, k, d)
  if list.mem(k, list.map(fst, m)) then list.assoc(k, m) else d end
end

# Log each song start to Flask (non-blocking)
def announce_song(m)
  artist = meta_get(m, "artist", "Unknown artist")
  title  = meta_get(m, "title",  "Unknown title")
  album  = meta_get(m, "album",  "")
  file   = if list.mem("filename", list.map(fst, m)) then list.assoc("filename", m)
           else if list.mem("file", list.map(fst, m)) then list.assoc("file", m) else "" end end

  qs = "type=song"
       ^ "&artist="   ^ url.encode(artist)
       ^ "&title="    ^ url.encode(title)
       ^ "&album="    ^ url.encode(album)
       ^ "&filename=" ^ url.encode(file)

  # Fire-and-forget so audio never blocks
  cmd = "bash -lc \"(curl -fsS 'http://127.0.0.1:5055/api/event?" ^ qs ^
        "' || wget -qO- 'http://127.0.0.1:5055/api/event?" ^ qs ^
        "') >/dev/null 2>&1 &\""
  ignore(process.run(cmd))
end

# Optional: schedule a DJ intro near the end of the current track (disabled by default)
def after_song(m)
  title  = meta_get(m, "title",  "Unknown Title")
  artist = meta_get(m, "artist", "Unknown Artist")
  dur_s  = if list.mem("duration", list.map(fst, m))
              then float_of_string(m["duration"]) else 0. end

  lead   = 3.0
  wait_s = if dur_s > lead then dur_s - lead else 1.0 end

  log("AUTO-DJ: scheduling intro in " ^ string_of(int_of_float(wait_s)) ^
      "s for: " ^ title ^ " — " ^ artist)

  ignore(process.run(
    "bash -lc \"(sleep " ^ string_of(int_of_float(wait_s)) ^
    "; /opt/ai-radio/dj_enqueue.sh \\\"" ^ artist ^ "\\\" \\\"" ^ title ^ "\\\" " ^
    ">>/var/tmp/dj_enqueue.log 2>&1) &\""
  ))
end

# Ensure sane metadata defaults
def rm_key(m, k)  list.filter(fun (kv) -> fst(kv) != k, m) end
def put_default(m, k, v)
  has = list.mem(k, list.map(fst, m))
  cur = if has then list.assoc(k, m) else "" end
  if (not has) or cur == "" then
    m2 = rm_key(m, k)
    list.append(m2, [(k, v)])
  else m end
end

def update_metadata(m)
  m = put_default(m, "title",  "Unknown")
  m = put_default(m, "artist", "Unknown Artist")
  m
end

# ---------- Chain / processing ----------
# Normalize metadata
music = metadata.map(update_metadata, all_music)

# Smooth crossfades on music
music = crossfade(music)

# Announce every real track start (attach once, on the smoothed music)
music = source.on_metadata(music, announce_song)
# Enable this if you want near-end DJ intros:
# music = source.on_metadata(music, after_song)

# Sine backup
sine_src = sine()

# Priority: DJ/TTS first, then music, then sine as emergency fallback
primary = fallback(track_sensitive=true,  [tts_q, music])
radio   = fallback(track_sensitive=false, [primary, sine_src])

# ---------- Output ----------
output.icecast(
  %mp3(id3v2=true),
  host="127.0.0.1",
  port=8000,
  user="source",
  password="passw0rd",
  mount="/stream.mp3",
  name="AI Plex DJ",
  url="http://127.0.0.1:8000/",
  genre="Mixed",
  description="24/7 auto-DJ",
  radio
)
