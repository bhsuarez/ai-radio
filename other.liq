# /opt/ai-radio/radio.liq
settings.init.allow_root.set(true)
set("log.file.path", "/var/log/liquidsoap/liquidsoap.log")

# Telnet control for pushing TTS clips
set("server.telnet", true)
set("server.telnet.bind_addr", "127.0.0.1")
set("server.telnet.port", 1234)

# One-shot request queue for AI-DJ clips
tts_q = request.queue(id="tts")

# Main music playlist
all_music = playlist(
    mode="random",
    reload=300,
    reload_mode="watch",
    "/opt/ai-radio/library_all.m3u"
)

# Prefer a queued DJ clip, then music, then sine tone as a fallback
radio = fallback(track_sensitive=false, [tts_q, all_music, sine()])

# ---- Write one JSON line on each track start ----
def esc(s) =
  s = string.replace("\\", "\\\\", s)
  s = string.replace("\"", "\\\"", s)
  s
end

def notify(m) =
  artist = list.assoc_default("", "artist", m)
  title  = list.assoc_default("", "title",  m)
  album  = list.assoc_default("", "album",  m)
  fn     = list.assoc_default("", "filename", m)

  json = "{\"artist\":\"" ^ esc(artist) ^ "\","
       ^ "\"title\":\""  ^ esc(title)  ^ "\","
       ^ "\"album\":\""  ^ esc(album)  ^ "\","
       ^ "\"filename\":\"" ^ esc(fn)   ^ "\"}"

  file.write("/opt/ai-radio/now.json", json ^ "\n")
end

# ---- Write one JSON line on each track start ----
def notify(m) =
  # m is an association list: [(key,value), ...]
  def get(k) =
    if list.mem_assoc(k, m) then
      list.assoc(k, m)
    else
      ""
    end
  end

  artist = get("artist")
  title  = get("title")
  album  = get("album")
  fn     = get("filename")

  # quote(...) safely JSON-escapes strings in Liquidsoap 2.x
  json = "{\"artist\":"  ^ quote(artist)
       ^ ",\"title\":"   ^ quote(title)
       ^ ",\"album\":"   ^ quote(album)
       ^ ",\"filename\":"^ quote(fn)
       ^ "}"

  file.append("/opt/ai-radio/now.json", json ^ "\n")
end

# Attach notifier
radio = on_metadata(notify, radio)

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