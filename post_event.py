#!/usr/bin/env python3
import sys, time, json, urllib.request
artist = sys.argv[1] if len(sys.argv)>1 else ""
title  = sys.argv[2] if len(sys.argv)>2 else ""
album  = sys.argv[3] if len(sys.argv)>3 else ""
fname  = sys.argv[4] if len(sys.argv)>4 else ""
payload = {
  "type":"song","time":int(time.time()),
  "artist": artist or "Unknown Artist",
  "title":  title  or "Unknown",
  "album":  album  or "",
  "filename": fname or ""
}
req = urllib.request.Request(
  "http://127.0.0.1:5055/api/log_event",
  data=json.dumps(payload).encode(),
  headers={"Content-Type":"application/json"})
urllib.request.urlopen(req, timeout=2).read()