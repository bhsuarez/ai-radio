# /opt/ai-radio/radio.liq
settings.init.allow_root.set(true)
set("log.file.path", "/var/log/liquidsoap/liquidsoap.log")

# Telnet control for pushing TTS clips
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")
set("server.telnet.port", 1234)

# One-shot request queue for AI-DJ clips
tts_q = request.queue(id="tts")

# Main music playlist (prebuilt M3U)
all_music = playlist(
  mode="random",
  reload=300,
  reload_mode="watch",
  "/opt/ai-radio/library_all.m3u"
)

# Prefer a queued DJ clip, then music, then sine tone as a fallback
radio = fallback(track_sensitive=false, [tts_q, all_music, sine()])

# Output to Icecast
output.icecast(
  %mp3,
  host="127.0.0.1", port=8000, user="source", password="orroz.123",
  mount="/stream.mp3",
  name="AI Plex DJ",
  url="http://192.168.1.243:8000/",
  genre="Mixed",
  description="24/7 auto-DJ",
  radio
)