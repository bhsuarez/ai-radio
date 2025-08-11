# Allow running as root
settings.init.allow_root.set(true)

settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("127.0.0.1")
settings.server.telnet.port.set(1234)

# Queue for TTS intros
tts_q = request.queue(id="tts")

# Music playlist
all_music = playlist(mode="random", reload=300, reload_mode="watch",
                     metadata_charset="UTF-8",
                     "/opt/ai-radio/library_all.m3u")

# Hook to create intro when a new song starts
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
    " http://127.0.0.1:5000/api/log_event >/dev/null 2>&1 &'"))
end

# Attach song announcement hook
all_music = source.on_metadata(all_music, announce_song)

# Sine fallback logging
def log_sine(_m)
  log("⚠️ Sine wave fallback active!")
end
sine_src = source.on_metadata(sine(), log_sine)

# Auto-DJ after every song
def after_song(m)
  artist = if list.mem("artist", list.map(fst, m)) then m["artist"] else "Unknown Artist" end
  title  = if list.mem("title", list.map(fst, m)) then m["title"] else "Unknown Title" end

  ts = string_of(time())
  filepath = "/opt/ai-radio/tts/intro_" ^ ts ^ ".mp3"

  # Run the script and get two lines: text, filepath
  lines = get_process_lines("python3 /opt/ai-radio/make_intro.py \"" ^ artist ^ "\" \"" ^ title ^ "\" \"" ^ filepath ^ "\"")
  text = list.nth(lines, 0)
  file = list.nth(lines, 1)

  # Push intro audio to TTS queue
  tts_q.push(request.create(file))

  # Log event to Flask backend
  ignore(process.run("curl -s -X POST -H 'Content-Type: application/json' \
    -d '{\"type\":\"dj\",\"text\":\"" ^ text ^ "\",\"audio_url\":\"" ^ file ^ "\"}' \
    http://127.0.0.1:5000/api/log_event"))
end
all_music = source.on_metadata(all_music, after_song)

# Build main fallback chain
radio = fallback(track_sensitive=false, [tts_q, all_music, sine_src])

# Ensure metadata always has title/artist
def update_metadata(m)
  if (not list.mem("title", list.map(fst, m))) or m["title"] == "" then
    m = list.append(m, [("title", "Unknown")])
  end

  if (not list.mem("artist", list.map(fst, m))) or m["artist"] == "" then
    m = list.append(m, [("artist", "Unknown Artist")])
  end

  m
end
radio = map_metadata(update_metadata, radio)

# Icecast output
output.icecast(
  %mp3,
  host="127.0.0.1",
  port=8000,
  user="source",
  password="orroz.123",  # must match /etc/icecast2/icecast.xml
  mount="/stream.mp3",
  name="AI Plex DJ",
  url="http://192.168.1.243:8000/",
  genre="Mixed",
  description="24/7 auto-DJ",
  radio
)