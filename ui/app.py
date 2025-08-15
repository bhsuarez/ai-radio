#!/usr/bin/env python3
import glob
import os, json, socket, time, hashlib, re, subprocess
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from urllib.parse import quote
import threading

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = "/opt/ai-radio/play_history.json"
MAX_HISTORY  = 500  # Increased to store more history
DEDUP_WINDOW_MS = 30_000  # Reduced to 30 seconds for better tracking

COVER_CACHE = Path("/opt/ai-radio/cache/covers"); COVER_CACHE.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# ── State ───────────────────────────────────────────────────────
HISTORY  = []   # newest first
UPCOMING = []   # optional
current_track = None
last_poll_time = 0

# ── Utils ───────────────────────────────────────────────────────
def clean_for_search(text):
    """Clean text for filename searching."""
    if not text:
        return ""
    # Remove common problematic characters and normalize
    text = re.sub(r'[^\w\s\-\.]', '', text)  # Keep letters, numbers, spaces, hyphens, dots
    text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
    return text.strip()

def find_file_by_metadata_fast(title, artist, music_root="/mnt/music"):
    """Enhanced fast file search with track number handling."""
    search_start = time.time()
    print(f"DEBUG: Starting search for title='{title}' artist='{artist}'")
    
    if not title and not artist:
        print("DEBUG: No title or artist provided")
        return None
    
    # Clean the search terms
    clean_title = clean_for_search(title)
    clean_artist = clean_for_search(artist)
    print(f"DEBUG: Cleaned terms - title='{clean_title}' artist='{clean_artist}'")
    
    audio_extensions = ['.mp3', '.flac', '.m4a', '.mp4', '.ogg', '.opus']
    
    # Strategy 1: Quick artist directory search
    if clean_artist and len(clean_artist) > 2:
        print(f"DEBUG: Searching artist directories for '{clean_artist}'...")
        try:
            music_path = Path(music_root)
            artist_dirs = []
            
            # Check direct subdirectories first
            for item in music_path.iterdir():
                if item.is_dir() and clean_artist.lower() in item.name.lower():
                    artist_dirs.append(item)
                    print(f"DEBUG: Found potential artist dir: {item}")
                    if len(artist_dirs) >= 3:  # Check more artist dirs
                        break
            
            # Also check one level deeper
            if len(artist_dirs) < 2:
                print("DEBUG: Checking subdirectories...")
                count = 0
                for subdir in music_path.iterdir():
                    if subdir.is_dir() and count < 15:  # Check more subdirs
                        for item in subdir.iterdir():
                            if item.is_dir() and clean_artist.lower() in item.name.lower():
                                artist_dirs.append(item)
                                print(f"DEBUG: Found artist dir in subdir: {item}")
                                if len(artist_dirs) >= 3:
                                    break
                        count += 1
                    if len(artist_dirs) >= 3:
                        break
            
            # Search in found artist directories
            for artist_dir in artist_dirs:
                print(f"DEBUG: Searching in artist directory: {artist_dir}")
                if clean_title:
                    # Look for files matching the title
                    for audio_file in artist_dir.rglob("*"):
                        if (audio_file.is_file() and 
                            any(audio_file.name.lower().endswith(ext) for ext in audio_extensions)):
                            
                            filename_lower = audio_file.name.lower()
                            clean_title_lower = clean_title.lower()
                            
                            # Enhanced title matching
                            title_matches = (
                                clean_title_lower in filename_lower or
                                # Remove track numbers (like "08 So Dark.mp3")
                                clean_title_lower in re.sub(r'^\d+\s*[-\.\s]*', '', filename_lower) or
                                # Try without spaces
                                clean_title_lower.replace(' ', '') in filename_lower.replace(' ', '') or
                                # Try partial match (first few words)
                                any(word in filename_lower for word in clean_title_lower.split()[:2] if len(word) > 2)
                            )
                            
                            if title_matches:
                                elapsed = time.time() - search_start
                                print(f"DEBUG: Found match in {elapsed:.2f}s: {audio_file}")
                                return str(audio_file)
                            
                            # Early termination if taking too long
                            if time.time() - search_start > 3:
                                print("DEBUG: Artist directory search timeout")
                                break
                        
                        if time.time() - search_start > 3:
                            break
                    
                    if time.time() - search_start > 3:
                        break
                        
        except Exception as e:
            print(f"DEBUG: Artist directory search failed: {e}")
    
    # Strategy 2: Enhanced pattern search for difficult cases
    if time.time() - search_start < 2:
        print("DEBUG: Trying enhanced pattern search...")
        
        # Build more flexible search patterns
        search_patterns = []
        
        if clean_title and len(clean_title) > 3:
            # Original patterns
            if clean_artist:
                search_patterns.extend([
                    f"*{clean_artist}*{clean_title}*",
                    f"*{clean_title}*{clean_artist}*",
                ])
            
            # Patterns that handle track numbers
            title_words = clean_title.split()
            if len(title_words) >= 2:
                # Use just first two words
                short_title = " ".join(title_words[:2])
                search_patterns.extend([
                    f"*{short_title}*",
                    f"*[0-9]*{short_title}*",  # Match track numbers
                ])
            
            # Single word patterns for short titles
            if len(title_words) == 1 and len(clean_title) > 4:
                search_patterns.append(f"*{clean_title}*")
        
        for pattern in search_patterns[:5]:  # Limit patterns
            try:
                print(f"DEBUG: Trying pattern: {pattern}")
                pattern_start = time.time()
                
                # Search deeper for difficult tracks
                search_path = os.path.join(music_root, "**", pattern)
                matches = glob.glob(search_path, recursive=True)
                
                # Filter to audio files
                audio_matches = [m for m in matches[:15]  # Check more matches
                               if any(m.lower().endswith(ext) for ext in audio_extensions)]
                
                if audio_matches:
                    # Sort by how well the filename matches
                    def match_score(filepath):
                        filename = os.path.basename(filepath).lower()
                        score = 0
                        if clean_title.lower() in filename:
                            score += 10
                        if clean_artist and clean_artist.lower() in filepath.lower():
                            score += 10
                        # Bonus for exact title match after removing track numbers
                        cleaned_filename = re.sub(r'^\d+\s*[-\.\s]*', '', filename)
                        if clean_title.lower() in cleaned_filename:
                            score += 5
                        return score
                    
                    audio_matches.sort(key=match_score, reverse=True)
                    found_file = audio_matches[0]
                    elapsed = time.time() - search_start
                    print(f"DEBUG: Found by pattern in {elapsed:.2f}s: {found_file}")
                    return found_file
                
                # Timeout check
                if time.time() - pattern_start > 1.5:
                    print(f"DEBUG: Pattern '{pattern}' timeout")
                    break
                    
            except Exception as e:
                print(f"DEBUG: Pattern search failed: {e}")
                continue
    
    elapsed = time.time() - search_start
    print(f"DEBUG: Search completed in {elapsed:.2f}s - no file found for title='{title}' artist='{artist}'")
    return None

def telnet_cmd(cmd: str, timeout=1.5) -> str:
    """Send a single telnet command and close (we append 'quit')."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((TELNET_HOST, TELNET_PORT))
        s.sendall((cmd + "\nquit\n").encode())
        chunks = []
        try:
            while True:
                b = s.recv(65535)
                if not b:
                    break
                chunks.append(b)
        except socket.timeout:
            pass
        finally:
            try: s.close()
            except: pass
        return (b"".join(chunks).decode(errors="ignore") or "").strip()
    except Exception as e:
        print(f"Telnet error: {e}")
        return ""

KV = re.compile(r'^\s*([^=\s]+)\s*=\s*"(.*)"\s*$')

def parse_kv_block(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        m = KV.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out

def first_block(body: str) -> str:
    """
    Extract the first '--- n ---' .. 'END' block Liquidsoap prints:
      --- 1 ---
      key="val"
      ...
      END
    """
    lines = (body or "").splitlines()
    grab, buf = False, []
    for ln in lines:
        if ln.startswith('--- ') and ln.endswith(' ---'):
            grab, buf = True, []
            continue
        if grab:
            if ln.strip() == "END":
                break
            buf.append(ln)
    return "\n".join(buf)

def load_history():
    global HISTORY
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                HISTORY[:] = json.load(f)
        else:
            HISTORY[:] = []
    except Exception:
        HISTORY[:] = []

def save_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(HISTORY[:MAX_HISTORY], f, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")

def extract_cover_art(audio_file):
    """Enhanced cover art extraction with proper M4A/MP4 support."""
    print(f"DEBUG: extract_cover_art called with: {audio_file}")
    
    if not audio_file or not os.path.exists(audio_file):
        print(f"DEBUG: Audio file not found: {audio_file}")
        return None
    
    key = hashlib.sha1(audio_file.encode("utf-8")).hexdigest()
    cached = os.path.join(COVER_CACHE, key + ".jpg")
    print(f"DEBUG: Cache path: {cached}")
    
    # Return cached version if it exists
    if os.path.exists(cached):
        print(f"DEBUG: Found cached cover art: {cached}")
        return f"/api/cover?file={quote(audio_file)}"
    
    try:
        from mutagen import File as MFile
        from mutagen.id3 import APIC
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4, MP4Cover
        
        print(f"DEBUG: Reading audio file...")
        audio = MFile(audio_file)
        if not audio:
            print(f"DEBUG: Mutagen could not read audio file")
            return check_folder_art(audio_file, cached)
        
        print(f"DEBUG: Audio file type: {type(audio).__name__}")
        cover_data = None
        
        # MP4/M4A files (this is the important fix!)
        if isinstance(audio, MP4):
            print(f"DEBUG: Processing MP4/M4A file")
            print(f"DEBUG: Available tags: {list(audio.tags.keys()) if audio.tags else 'No tags'}")
            
            if audio.tags and 'covr' in audio.tags:
                cover_list = audio.tags['covr']
                print(f"DEBUG: Found 'covr' tag with {len(cover_list)} cover(s)")
                
                if cover_list:
                    # Get the first cover
                    cover_item = cover_list[0]
                    print(f"DEBUG: Cover item type: {type(cover_item)}")
                    
                    if isinstance(cover_item, MP4Cover):
                        cover_data = bytes(cover_item)
                        print(f"DEBUG: Extracted MP4Cover, size: {len(cover_data)} bytes")
                    else:
                        # Sometimes it's just bytes
                        cover_data = cover_item
                        print(f"DEBUG: Got raw cover data, size: {len(cover_data)} bytes")
            else:
                print("DEBUG: No 'covr' tag found in MP4 file")
        
        # MP3 ID3 tags
        elif hasattr(audio, 'tags') and audio.tags:
            print(f"DEBUG: Processing MP3 file with {len(audio.tags)} tags")
            
            # Look for APIC frames
            for tag_name, tag_value in audio.tags.items():
                if isinstance(tag_value, APIC):
                    print(f"DEBUG: Found APIC frame: {tag_name}, size: {len(tag_value.data)} bytes")
                    cover_data = tag_value.data
                    break
            
            if not cover_data:
                # Check common APIC tag names
                for apic_key in ['APIC:', 'APIC:Cover (front)', 'APIC:Cover', 'APIC:Front Cover']:
                    if apic_key in audio.tags:
                        tag_value = audio.tags[apic_key]
                        if hasattr(tag_value, 'data'):
                            cover_data = tag_value.data
                            print(f"DEBUG: Found cover in {apic_key}, size: {len(cover_data)} bytes")
                            break
        
        # FLAC files
        elif isinstance(audio, FLAC):
            print(f"DEBUG: Processing FLAC file")
            if audio.pictures:
                picture = audio.pictures[0]
                cover_data = picture.data
                print(f"DEBUG: FLAC picture found, size: {len(cover_data)} bytes")
        
        else:
            print(f"DEBUG: Unsupported audio type: {type(audio)}")
        
        # Save cover art if we found any
        if cover_data and len(cover_data) > 100:
            try:
                # Ensure cache directory exists
                os.makedirs(os.path.dirname(cached), exist_ok=True)
                
                print(f"DEBUG: Saving {len(cover_data)} bytes to {cached}")
                with open(cached, 'wb') as f:
                    f.write(cover_data)
                
                # Verify the file was saved
                if os.path.exists(cached):
                    cached_size = os.path.getsize(cached)
                    print(f"DEBUG: Successfully cached, file size: {cached_size} bytes")
                    return f"/api/cover?file={quote(audio_file)}"
                else:
                    print("DEBUG: ERROR - Cache file was not created!")
            except Exception as e:
                print(f"DEBUG: Error saving cover art: {e}")
                import traceback
                traceback.print_exc()
        else:
            if cover_data:
                print(f"DEBUG: Cover data too small: {len(cover_data)} bytes")
            else:
                print("DEBUG: No cover data found")
    
    except ImportError as e:
        print(f"DEBUG: Import error: {e}")
        return check_folder_art(audio_file, cached)
    except Exception as e:
        print(f"DEBUG: Extraction error: {e}")
        import traceback
        traceback.print_exc()
    
    # Fallback to folder art
    print("DEBUG: Falling back to folder art")
    return check_folder_art(audio_file, cached)

def check_folder_art(audio_file, cached_path):
    """Check for folder-based album art."""
    folder = os.path.dirname(audio_file)
    
    # Common album art filenames (including MusicBrainz Picard defaults)
    art_filenames = [
        "cover.jpg", "cover.jpeg", "cover.png",
        "folder.jpg", "folder.jpeg", "folder.png", 
        "front.jpg", "front.jpeg", "front.png",
        "album.jpg", "album.jpeg", "album.png",
        "AlbumArt.jpg", "AlbumArtSmall.jpg",
        "albumart.jpg", "albumartsmall.jpg",
        # MusicBrainz Picard sometimes uses these
        "cover-front.jpg", "cover-front.png"
    ]
    
    for art_name in art_filenames:
        art_path = os.path.join(folder, art_name)
        if os.path.exists(art_path):
            try:
                import shutil
                shutil.copy2(art_path, cached_path)
                print(f"Found folder art: {art_path}")
                return f"/api/cover?file={quote(audio_file)}"
            except Exception as e:
                print(f"Error copying folder art: {e}")
    
    print(f"No album art found for: {audio_file}")
    return None

# Add a diagnostic endpoint to check a specific file's metadata
@app.get("/api/debug_cover")
def debug_cover():
    """Debug endpoint to check cover art extraction for a specific file."""
    fpath = request.args.get("file", "")
    if not fpath:
        return jsonify({"error": "No file specified"}), 400
    
    if not os.path.exists(fpath):
        return jsonify({"error": "File not found"}), 404
    
    try:
        from mutagen import File as MFile
        
        audio = MFile(fpath)
        info = {
            "file": fpath,
            "exists": os.path.exists(fpath),
            "audio_info": str(type(audio)) if audio else "Could not read file",
            "has_tags": bool(getattr(audio, 'tags', None)),
            "tag_keys": list(audio.tags.keys()) if getattr(audio, 'tags', None) else [],
            "cover_extraction_result": extract_cover_art(fpath),
            "folder_contents": []
        }
        
        # List folder contents to check for art files
        folder = os.path.dirname(fpath)
        try:
            folder_files = [f for f in os.listdir(folder) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp'))]
            info["folder_contents"] = folder_files
        except:
            pass
        
        return jsonify(info)
        
    except ImportError:
        return jsonify({"error": "Mutagen not installed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def push_event(ev: dict):
    """Insert newest-first with light de-duplication and persist."""
    now_ms = int(time.time() * 1000)

    # normalize timestamp (s→ms if needed)
    t = ev.get("time")
    if isinstance(t, (int, float)):
        t = int(t)
        if t < 10_000_000_000:
            t *= 1000
        ev["time"] = t
    else:
        ev["time"] = now_ms

    # normalize song fields
    if ev.get("type") == "song":
        title  = (ev.get("title") or "").strip()
        artist = (ev.get("artist") or "").strip()
        album  = (ev.get("album") or "").strip()
        fn     = ev.get("filename") or ""
        
        if not title and fn:
            # Extract title/artist from filename
            basename = os.path.basename(fn)
            name_without_ext = os.path.splitext(basename)[0]
            
            # Try "Artist - Title" format
            if ' - ' in name_without_ext:
                parts = name_without_ext.split(' - ', 1)
                if not artist:
                    artist = parts[0].strip()
                if not title:
                    title = parts[1].strip()
            else:
                if not title:
                    title = name_without_ext
        
        ev["artist"] = artist or "Unknown Artist"
        ev["title"]  = title  or "Unknown"
        ev["album"]  = album  or ""
        
        # Add artwork URL if not present
        if not ev.get("artwork_url") and fn:
            artwork_url = extract_cover_art(fn)
            if artwork_url:
                ev["artwork_url"] = artwork_url

    # dedupe vs recent entries (check last 3 entries)
    for recent in HISTORY[:3]:
        if ev.get("type") == recent.get("type") == "song":
            same = (
                (ev.get("title") or "") == (recent.get("title") or "") and
                (ev.get("artist") or "") == (recent.get("artist") or "") and
                (ev.get("filename") or "") == (recent.get("filename") or "")
            )
            if same and (now_ms - int(recent.get("time", now_ms))) < DEDUP_WINDOW_MS:
                print(f"Skipping duplicate: {ev.get('title')} by {ev.get('artist')}")
                return

    print(f"Adding to history: {ev.get('title')} by {ev.get('artist')}")
    HISTORY.insert(0, ev)
    del HISTORY[MAX_HISTORY:]
    save_history()

def _build_art_url(path: str) -> str:
    if path and os.path.isabs(path) and os.path.exists(path):
        return request.url_root.rstrip("/") + "/api/cover?file=" + quote(path)
    return request.url_root.rstrip("/") + "/static/station-cover.jpg"

# ── Liquidsoap adapters ─────────────────────────────────────────
# Simplified read_now with timeout
def read_now() -> dict:
    """Ask Liquidsoap for metadata with timeout."""
    try:
        print("DEBUG: Getting metadata from Liquidsoap...")
        start_time = time.time()
        
        raw = telnet_cmd("output.icecast.metadata", timeout=2)
        blk = first_block(raw)
        m = parse_kv_block(blk)
        
        elapsed = time.time() - start_time
        print(f"DEBUG: Liquidsoap query took {elapsed:.2f}s, got: {m}")
        
        filename = m.get("filename") or m.get("file") or ""
        title = m.get("title", "")
        artist = m.get("artist", "")
        album = m.get("album", "")
        
        # Only search for file if we don't have one and have meaningful metadata
        if not filename and title and len(title) > 2:
            print("DEBUG: No filename, starting file search...")
            search_start = time.time()
            filename = find_file_by_metadata_fast(title, artist)
            search_elapsed = time.time() - search_start
            print(f"DEBUG: File search took {search_elapsed:.2f}s")
        
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "filename": filename,
        }
    except Exception as e:
        print(f"DEBUG: Error in read_now: {e}")
        return {}

def monitor_track_changes():
    """Background thread to monitor track changes and auto-log them."""
    global current_track, last_poll_time
    
    while True:
        try:
            now_data = read_now()
            current_time = time.time()
            
            if now_data.get("filename") or now_data.get("title"):
                # Create a signature for the current track
                track_sig = f"{now_data.get('filename', '')}:{now_data.get('title', '')}:{now_data.get('artist', '')}"
                
                # If this is a new track and enough time has passed
                if (current_track != track_sig and 
                    current_time - last_poll_time > 10):  # At least 10 seconds between tracks
                    
                    if current_track is not None:  # Not the first track
                        print(f"Track change detected: {now_data.get('title')} by {now_data.get('artist')}")
                        
                        # Auto-log the new track
                        event = {
                            "type": "song",
                            "time": int(current_time * 1000),
                            "title": now_data.get("title", ""),
                            "artist": now_data.get("artist", ""),
                            "album": now_data.get("album", ""),
                            "filename": now_data.get("filename", ""),
                        }
                        push_event(event)
                    
                    current_track = track_sig
                    last_poll_time = current_time
            
            time.sleep(5)  # Check every 5 seconds
            
        except Exception as e:
            print(f"Monitor error: {e}")
            time.sleep(10)

def read_next(max_items=5):
    """Simple, safe version that won't crash the app."""
    out = []
    try:
        # Get request list
        rids_text = telnet_cmd("request.all", timeout=2)
        if not rids_text:
            return out
        
        # Find numbers in the response
        rids = re.findall(r'\d+', rids_text)
        
        # Process first few requests
        for rid in rids[:max_items]:
            try:
                # Get metadata
                meta_raw = telnet_cmd(f"request.metadata {rid}", timeout=2)
                if not meta_raw:
                    continue
                
                # Simple parsing
                metadata = {}
                for line in meta_raw.split('\n'):
                    if '=' in line and '"' in line:
                        try:
                            key = line.split('=')[0].strip()
                            value = line.split('"')[1] if '"' in line else ""
                            metadata[key] = value
                        except:
                            continue
                
                # Extract basic info
                title = metadata.get("title", "")
                artist = metadata.get("artist", "")
                filename = metadata.get("filename", "") or metadata.get("initial_uri", "")
                
                # Extract from filename if needed
                if not title and filename:
                    basename = os.path.basename(filename)
                    title = os.path.splitext(basename)[0]
                    # Remove track numbers
                    title = re.sub(r'^\d+\s*[-\.\s]*', '', title)
                    if ' - ' in title:
                        parts = title.split(' - ', 1)
                        artist = parts[0].strip()
                        title = parts[1].strip()
                
                # Add if we have a title
                if title and title.strip():
                    track = {
                        "type": "song",
                        "time": int(time.time() * 1000),
                        "title": title,
                        "artist": artist or "Unknown Artist",
                        "album": metadata.get("album", ""),
                        "filename": filename
                    }
                    out.append(track)
                    
            except Exception:
                continue
                
    except Exception:
        pass
    
    return out

# ── Routes ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename)

@app.get("/api/history")
def api_history():
    if not HISTORY and os.path.exists(HISTORY_FILE):
        load_history()
    return jsonify(HISTORY[:MAX_HISTORY])

@app.get("/api/event")
def api_event_compat():
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title": request.args.get("title", ""),
        "artist": request.args.get("artist", ""),
        "album": request.args.get("album", ""),
        "filename": request.args.get("filename", ""),
    }
    push_event(ev)
    return jsonify({"ok": True, "stored": ev})

# Updated api_now with debug
@app.get("/api/now")
def api_now():
    print("DEBUG: api_now called")
    start_time = time.time()
    
    now_ms = int(time.time() * 1000)
    
    # Get live data
    data = read_now() or {}
    if not (data.get("filename") or data.get("title")):
        print("DEBUG: No data from read_now")
        return jsonify({"title": "Nothing Playing", "artist": "", "type": "song"})
    
    filename = data.get("filename", "")
    title = data.get("title") or "Unknown"
    artist = data.get("artist") or "Unknown Artist"
    album = data.get("album") or ""
    
    print(f"DEBUG: Processing - title='{title}', filename='{filename}'")
    
    # Build artwork URL
    artwork_url = "/static/station-cover.jpg"  # Default
    if filename and os.path.exists(filename):
        print(f"DEBUG: Extracting artwork from: {filename}")
        artwork_start = time.time()
        extracted_url = extract_cover_art(filename)
        artwork_elapsed = time.time() - artwork_start
        print(f"DEBUG: Artwork extraction took {artwork_elapsed:.2f}s")
        
        if extracted_url:
            artwork_url = extracted_url
            print(f"DEBUG: Using extracted artwork: {artwork_url}")
        else:
            print(f"DEBUG: No artwork extracted from: {filename}")
    else:
        print(f"DEBUG: No valid filename for artwork: '{filename}'")
    
    ev = {
        "type": "song",
        "time": now_ms,
        "title": title,
        "artist": artist,
        "album": album,
        "filename": filename or "",
        "artwork_url": artwork_url,
    }
    
    total_elapsed = time.time() - start_time
    print(f"DEBUG: api_now completed in {total_elapsed:.2f}s")
    return jsonify(ev)

@app.get("/api/next")
def api_next():
    return jsonify(read_next(max_items=10))

@app.get("/api/tts_queue")
def tts_queue_get():
    return jsonify([e for e in HISTORY if e.get("type") == "dj"][:5])

@app.post("/api/tts_queue")
def tts_queue_post():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    push_event({"type": "dj", "text": text, "audio_url": None, "time": int(time.time()*1000)})
    return jsonify({"ok": True})

@app.post("/api/skip")
def api_skip():
    try:
        result = telnet_cmd("output.icecast.skip", timeout=1.5)
        print(f"Skip result: {result}")
        return {"ok": True, "result": result}
    except Exception as e:
        print(f"Skip error: {e}")
        return {"ok": False, "error": str(e)}, 500

@app.post("/api/log_event")
def log_event():
    payload = request.get_json(silent=True) or request.form or request.args
    ev = {
        "type": "song",
        "time": int(time.time() * 1000),
        "title":  (payload.get("title")  or "").strip(),
        "artist": (payload.get("artist") or "").strip(),
        "album":  (payload.get("album")  or "").strip(),
        "filename": (payload.get("filename") or "").strip(),
    }
    if ev["title"] or ev["filename"]:
        push_event(ev)
        return jsonify({"ok": True, "event": ev})
    return jsonify({"ok": False, "error": "No title or filename provided"}), 400

@app.get("/api/cover")
def api_cover():
    fpath = request.args.get("file", "")
    default_cover_path = os.path.join(BASE_DIR, "static", "station-cover.jpg")
    
    if not fpath or not os.path.isabs(fpath) or not os.path.exists(fpath):
        if os.path.exists(default_cover_path):
            return send_file(default_cover_path, mimetype="image/jpeg", conditional=True)
        return abort(404)
    
    # Check cache
    key = hashlib.sha1(fpath.encode("utf-8")).hexdigest()
    cached = os.path.join(COVER_CACHE, key + ".jpg")
    
    if os.path.exists(cached):
        return send_file(cached, mimetype="image/jpeg", conditional=True)
    
    # Try to extract cover art
    artwork_url = extract_cover_art(fpath)
    if artwork_url and os.path.exists(cached):
        return send_file(cached, mimetype="image/jpeg", conditional=True)
    
    # Default fallback
    if os.path.exists(default_cover_path):
        return send_file(default_cover_path, mimetype="image/jpeg", conditional=True)
    
    return abort(404)

@app.get("/api/debug_liquidsoap")
def debug_liquidsoap():
    """Debug what Liquidsoap is actually sending us."""
    try:
        # Get raw telnet response
        raw = telnet_cmd("output.icecast.metadata", timeout=2)
        
        # Parse it
        blk = first_block(raw)
        parsed = parse_kv_block(blk)
        
        # Also try getting the current request info
        try:
            request_raw = telnet_cmd("request.metadata 0", timeout=2)
            request_parsed = parse_kv_block(request_raw)
        except:
            request_parsed = {}
        
        return jsonify({
            "raw_response": raw,
            "first_block": blk,
            "parsed_metadata": parsed,
            "request_metadata": request_parsed,
            "processed_now": read_now()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/test_find")
def test_find():
    """Test endpoint to search for a specific track with timing."""
    title = request.args.get("title", "")
    artist = request.args.get("artist", "")
    
    if not title and not artist:
        return jsonify({"error": "Provide title and/or artist parameters"}), 400
    
    print(f"DEBUG: test_find called with title='{title}' artist='{artist}'")
    start_time = time.time()
    
    filename = find_file_by_metadata_fast(title, artist)
    search_elapsed = time.time() - start_time
    
    result = {
        "search_title": title,
        "search_artist": artist,
        "found_file": filename,
        "file_exists": os.path.exists(filename) if filename else False,
        "search_time_seconds": round(search_elapsed, 2)
    }
    
    if filename and os.path.exists(filename):
        artwork_start = time.time()
        artwork_url = extract_cover_art(filename)
        artwork_elapsed = time.time() - artwork_start
        
        result["artwork_url"] = artwork_url
        result["has_artwork"] = bool(artwork_url and artwork_url != "/static/station-cover.jpg")
        result["artwork_extraction_time"] = round(artwork_elapsed, 2)
    
    total_elapsed = time.time() - start_time
    result["total_time_seconds"] = round(total_elapsed, 2)
    
    return jsonify(result)

@app.get("/api/debug_import")
def debug_import():
    """Debug endpoint to test imports and Python environment."""
    import sys
    import os
    
    result = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "python_path": sys.path,
        "current_working_directory": os.getcwd(),
        "user": os.getenv('USER', 'unknown'),
        "imports": {}
    }
    
    # Test mutagen import
    try:
        import mutagen
        result["imports"]["mutagen"] = {
            "success": True,
            "version": getattr(mutagen, '__version__', 'unknown'),
            "location": mutagen.__file__
        }
    except ImportError as e:
        result["imports"]["mutagen"] = {
            "success": False,
            "error": str(e)
        }
    except Exception as e:
        result["imports"]["mutagen"] = {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }
    
    # Test specific mutagen modules
    mutagen_modules = ['mutagen.id3', 'mutagen.flac', 'mutagen.mp4']
    for module_name in mutagen_modules:
        try:
            __import__(module_name)
            result["imports"][module_name] = {"success": True}
        except ImportError as e:
            result["imports"][module_name] = {"success": False, "error": str(e)}
    
    return jsonify(result)

# ── Startup ─────────────────────────────────────────────────────
load_history()

# Start background monitoring thread
monitor_thread = threading.Thread(target=monitor_track_changes, daemon=True)
monitor_thread.start()

if __name__ == "__main__":
    print(f"Starting AI Radio server on {HOST}:{PORT}")
    print(f"History file: {HISTORY_FILE}")
    print(f"Cover cache: {COVER_CACHE}")
    app.run(host=HOST, port=PORT, debug=False)