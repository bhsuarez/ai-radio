# Allow running as root
settings.init.allow_root.set(true)

# Telnet control
settings.server.telnet.set(true)
settings.server.telnet.bind_addr.set("127.0.0.1")
settings.server.telnet.port.set(1234)

# Queues
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
  artist = m["artist"]
  title  = m["title"]
  log("Generating intro for: #{artist} - #{title}")

  system("python3 /opt/ai-radio/make_intro.py \"#{artist}\" \"#{title}\"")
  tts_q.push(request.create("/opt/ai-radio/tts/next_intro.mp3"))
end

# Attach hook
all_music = source.on_metadata(all_music, announce_song)

# Play intro first, then music
radio = fallback(track_sensitive=false, [tts_q, all_music, sine()])

# Icecast output
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