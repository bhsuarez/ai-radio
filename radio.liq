# ----- Liquidsoap 2.3.x config for AI Radio with Auto DJ -----
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
# Queue for DJ/TTS lines - this will play intros
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

# Global variable to track last processed track (to avoid duplicates)
last_processed_track = ref("")

# Auto-generate DJ intro for upcoming track
def auto_generate_dj_intro(m)
  artist = meta_get(m, "artist", "Unknown Artist")
  title  = meta_get(m, "title",  "Unknown Title")
  
  # Create a unique key for this track
  track_key = artist ^ "|" ^ title
  
  # Only generate if this is a different track than last time
  if !last_processed_track != track_key then
    last_processed_track := track_key
    
    log("AUTO-DJ: Requesting intro for upcoming track: " ^ title ^ " by " ^ artist)
    
    # Call without parameters so endpoint gets actual next track from queue
    url = "http://127.0.0.1:5055/api/dj-now"
    # Use thread to avoid blocking liquidsoap
    thread.run(fun () -> ignore(http.post(url)))
  end
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

# Optional: Generate outro for completed track
def generate_outro(m)
  artist = meta_get(m, "artist", "Unknown Artist")
  title  = meta_get(m, "title",  "Unknown Title")
  
  log("AUTO-DJ: Generating outro for completed track: " ^ title ^ " by " ^ artist)
  
  # Generate outro in background (optional feature)
  cmd = "bash -c \"(/opt/ai-radio/dj_enqueue_xtts_ai.sh \\\"" ^ artist ^ "\\\" \\\"" ^ title ^ "\\\" en \\\"Damien Black\\\" outro >>/var/tmp/auto_dj.log 2>&1) &\""
  ignore(process.run(cmd))
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

# AUTO-DJ: Generate intros when tracks are selected/ready
music = source.on_metadata(music, auto_generate_dj_intro)

# Smooth crossfades on music
music = crossfade(music)

# Announce every real track start (attach once, on the smoothed music)
music = source.on_metadata(music, announce_song)

# Optional: Generate outros when tracks end
# Uncomment this line if you want outro generation too:
# music = source.on_track(music, generate_outro)

# Sine backup
sine_src = sine()

# Priority: DJ/TTS first, then music, then sine as emergency fallback
# This ensures intros play before songs
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
  description="24/7 auto-DJ with AI commentary",
  radio
)