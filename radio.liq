# Allow running as root
settings.init.allow_root.set(true)

settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("127.0.0.1")
settings.server.telnet.port.set(1234)

# Queue for TTS intros
tts_q = request.queue(id="tts")

# Music playlist
all_music = playlist(
  mode="random",
  reload=300,
  reload_mode="watch",
  "/opt/ai-radio/library_all.m3u"
)

# Hook to create intro when a new song starts
def announce_song(m)
  artist = if list.mem("artist", list.map(fst, m)) then m["artist"] else "Unknown Artist" end
  title  = if list.mem("title", list.map(fst, m)) then m["title"] else "Unknown Title" end
  log("🎵 Now playing: #{artist} - #{title}")

  # Call intro generator
  process.run("python3 /opt/ai-radio/make_intro.py #{artist} #{title}")

  # Queue TTS
  tts_q.push(request.create("/opt/ai-radio/tts/next_intro.mp3"))

  # Log how many tracks are queued
  log("📜 Tracks in playlist: unknown (request.queue pending not available)")
end

# Attach hook to playlist
all_music = source.on_metadata(all_music, announce_song)

# Log when sine starts
def log_sine(_m)
  log("⚠️ Sine wave fallback active!")
end
sine_src = source.on_metadata(sine(), log_sine)

# Main fallback chain: TTS → music → sine wave
radio = fallback(track_sensitive=false, [tts_q, all_music, sine_src])

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