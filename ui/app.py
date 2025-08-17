#!/usr/bin/env python3
import glob
import os, json, socket, time, hashlib, re, subprocess
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from urllib.parse import quote
import random
import threading
import subprocess
import tempfile
import requests

# ── Config ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5055

TELNET_HOST = "127.0.0.1"
TELNET_PORT = 1234

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = "/opt/ai-radio/play_history.json"
DJ_CONFIG_FILE = "/opt/ai-radio/dj_settings.json"
DJ_LINES_FILE = "/opt/ai-radio/dj_lines.txt"
MAX_HISTORY  = 500  # Increased to store more history
DEDUP_WINDOW_MS = 30_000  # Reduced to 30 seconds for better tracking

COVER_CACHE = Path("/opt/ai-radio/cache/covers"); COVER_CACHE.mkdir(parents=True, exist_ok=True)

# ADD THIS DJ CONFIGURATION SECTION after the COVER_CACHE line (around line 25)
DEFAULT_DJ_CONFIG = {
    "auto_dj_enabled": True,
    "dj_probability": 100,  # 100% chance after each track - AI DJ every time!
    "min_interval_minutes": 0,  # No minimum interval - DJ after every track
    "max_interval_minutes": 15, # Force DJ line after 15 minutes (backup)
    "use_ai_dj": True,  # Always use AI-generated lines
    "ai_dj_probability": 100,  # 100% chance to use AI (only fall back to templates on failure)
    "ai_script_path": "/opt/ai-radio/gen_ai_dj_line.sh",  # Path to your AI script
    "dj_templates": [
        "That was {title} by {artist}, keeping the vibes flowing here on AI Radio!",
        "You just heard {title} from {artist}, and we've got more great music coming up!",
        "{artist} with {title}, and this is AI Radio bringing you the best mix!",
        "Beautiful track there - {title} by {artist}. Stay tuned for more!",
        "That's {title} from {artist}, and you're listening to AI Radio!",
        "What a great song! {title} by {artist}. More awesome music ahead!",
        "AI Radio bringing you {title} from {artist}. We'll be right back with more hits!",
        "{artist}'s {title} there, and this is your AI DJ keeping the music flowing!",
    ],
    "save_to_file": True
}

TTS_PROVIDER = "piper"  # Change to "elevenlabs" when ready
PIPER_MODEL_PATH = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"
ELEVENLABS_API_KEY = ""  # Set this when you switch to ElevenLabs
ELEVENLABS_VOICE_ID = ""  # Set this when you switch to ElevenLabs
TTS_OUTPUT_DIR = "/opt/ai-radio/tts"

def load_dj_config():
    """Load DJ configuration from file or create default"""
    try:
        if os.path.exists(DJ_CONFIG_FILE):
            with open(DJ_CONFIG_FILE, 'r') as f:
                config = json.load(f)
            # Merge with defaults to handle new keys
            merged = DEFAULT_DJ_CONFIG.copy()
            merged.update(config)
            return merged
        else:
            save_dj_config(DEFAULT_DJ_CONFIG)
            return DEFAULT_DJ_CONFIG.copy()
    except Exception:
        return DEFAULT_DJ_CONFIG.copy()

def save_dj_config(config):
    """Save DJ configuration to file"""
    try:
        Path(DJ_CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(DJ_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass

def generate_ai_dj_line(title, artist, timeout=20):
    """Generate AI DJ line using the external script"""
    try:
        script_path = dj_config.get("ai_script_path", "/opt/ai-radio/gen_ai_dj_line.sh")

        # Check if script exists and is executable
        if not os.path.exists(script_path):
            print(f"DJ: AI script not found at {script_path}")
            return None

        if not os.access(script_path, os.X_OK):
            print(f"DJ: AI script not executable: {script_path}")
            return None

        # Run the AI script with title and artist
        print(f"DJ: Calling AI script for '{title}' by '{artist}'")
        start_time = time.time()

        result = subprocess.run(
            [script_path, title, artist],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(os.environ, **{
                'PATH': '/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin',
                'HOME': '/root',
                'OLLAMA_MODELS': '/mnt/music/ai-dj/ollama'
            })
        )

        elapsed = time.time() - start_time
        print(f"DJ: AI script completed in {elapsed:.2f}s")
        print(f"DJ: AI script return code: {result.returncode}")
        print(f"DJ: AI script stdout: '{result.stdout.strip()}'")
        print(f"DJ: AI script stderr: '{result.stderr.strip()}'")

        if result.returncode == 0:
            ai_line = result.stdout.strip()
            # Remove quotes if the script returns quoted output
            if ai_line.startswith('"') and ai_line.endswith('"'):
                ai_line = ai_line[1:-1]

            if ai_line and len(ai_line) > 10:  # Basic validation
                print(f"DJ: AI generated: {ai_line}")
                return ai_line
            else:
                print(f"DJ: AI output too short or empty: '{ai_line}'")
                return None
        else:
            print(f"DJ: AI script failed with code {result.returncode}")
            if result.stderr:
                print(f"DJ: AI script stderr: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        print(f"DJ: AI script timed out after {timeout} seconds")
        return None
    except Exception as e:
        print(f"DJ: Unexpected error calling AI script: {e}")
        return None

def generate_dj_line_with_audio(title="Unknown", artist="Unknown Artist", album=""):
    """Generate a DJ line with both text and audio - AI first"""
    # Generate the text using AI directly (don't call generate_dj_line which has template fallback)
    dj_text = generate_ai_dj_line(title, artist)

    # If AI failed, then fall back to templates
    if not dj_text:
        print("DJ: AI generation failed, using template fallback")
        dj_text = generate_dj_line(title, artist, album)

    if not dj_text:
        print("DJ: No DJ line generated, skipping audio generation")
        return None, None

    # Generate audio file
    audio_file = generate_dj_audio(dj_text, provider=TTS_PROVIDER)

    audio_url = None
    if audio_file:
        # Queue in Liquidsoap
        if queue_audio_in_liquidsoap(audio_file):
            # Create URL for web access
            filename = os.path.basename(audio_file)
            audio_url = f"/api/tts-file/{filename}"

    return dj_text, audio_url

def queue_audio_in_liquidsoap(audio_file):
    """Queue audio file in Liquidsoap TTS queue"""
    try:
        if not audio_file or not os.path.exists(audio_file):
            print(f"Audio file not found: {audio_file}")
            return False

        # Create file URI with full absolute path
        file_uri = f"file://{os.path.abspath(audio_file)}"

        # Queue in Liquidsoap via telnet
        cmd = f"tts.push {file_uri}"
        result = telnet_cmd(cmd, timeout=2)

        print(f"Queued in Liquidsoap: {file_uri}")
        print(f"Liquidsoap response: {result}")

        # Check if it was actually queued
        queue_check = telnet_cmd("tts.queue", timeout=2)
        print(f"Queue contents after push: {queue_check}")

        return True

    except Exception as e:
        print(f"Error queuing audio in Liquidsoap: {e}")
        return False

def generate_dj_line(title="Unknown", artist="Unknown Artist", album=""):
    """Generate a DJ line using AI script with template fallback"""

    # Always try AI-generated line first
    ai_line = generate_ai_dj_line(title, artist)
    if ai_line:
        print(f"DJ: Using AI-generated line: {ai_line}")
        return ai_line
    else:
        print("DJ: AI generation failed, falling back to templates")

    # Fallback to template-based lines only if AI fails
    templates = dj_config.get("dj_templates", DEFAULT_DJ_CONFIG["dj_templates"])
    template = random.choice(templates)

    try:
        dj_text = template.format(title=title, artist=artist, album=album)
        print(f"DJ: Using template fallback: {dj_text}")
        return dj_text
    except Exception:
        # Ultimate fallback
        fallback = f"That was {title} by {artist}, and you're listening to AI Radio!"
        print(f"DJ: Using ultimate fallback: {fallback}")
        return fallback

# Add this function after your existing DJ functions
def generate_dj_audio(text, provider="piper"):
    """Generate audio file from DJ text using Piper or ElevenLabs"""
    try:
        # Create output directory
        Path(TTS_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

        # Generate unique filename
        timestamp = int(time.time())
        safe_text = re.sub(r'[^\w\s-]', '', text)[:50]  # Safe filename
        filename = f"dj_{timestamp}_{safe_text.replace(' ', '_')}.wav"
        output_path = os.path.join(TTS_OUTPUT_DIR, filename)

        if provider == "piper":
            return generate_piper_audio(text, output_path)
        elif provider == "elevenlabs":
            return generate_elevenlabs_audio(text, output_path)
        else:
            raise ValueError(f"Unknown TTS provider: {provider}")

    except Exception as e:
        print(f"Error generating DJ audio: {e}")
        return None

def generate_piper_audio(text, output_path):
    """Generate audio using Piper TTS"""
    try:
        # First generate WAV with Piper
        wav_path = output_path

        # Check if model exists
        if not os.path.exists(PIPER_MODEL_PATH):
            print(f"Piper model not found: {PIPER_MODEL_PATH}")
            return None

        # Run Piper
        cmd = [
            "python3", "-m", "piper",
            "--model", PIPER_MODEL_PATH,
            "--output_file", wav_path
        ]

        print(f"Running Piper TTS: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = process.communicate(input=text)

        if process.returncode != 0:
            print(f"Piper TTS failed: {stderr}")
            return None

        if not os.path.exists(wav_path):
            print(f"Piper output file not created: {wav_path}")
            return None

        # Convert WAV to MP3 for better compatibility
        mp3_path = wav_path.replace('.wav', '.mp3')
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", wav_path,
            "-ar", "44100", "-ac", "2",
            "-af", "volume=9dB,apad=pad_dur=0.5",
            "-codec:a", "libmp3lame", "-q:a", "3",
            mp3_path
        ]

        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"FFmpeg conversion failed: {result.stderr}")
            return wav_path  # Return WAV if MP3 conversion fails

        # Clean up WAV file
        try:
            os.remove(wav_path)
        except:
            pass

        print(f"Generated Piper audio: {mp3_path}")
        return mp3_path

    except Exception as e:
        print(f"Piper TTS error: {e}")
        return None

def generate_elevenlabs_audio(text, output_path):
    """Generate audio using ElevenLabs API"""
    try:
        if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
            print("ElevenLabs API key or voice ID not configured")
            return None

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }

        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }

        print(f"Calling ElevenLabs API for: {text[:50]}...")

        response = requests.post(url, json=data, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"ElevenLabs API error: {response.status_code} - {response.text}")
            return None

        # Save the audio
        mp3_path = output_path.replace('.wav', '.mp3')
        with open(mp3_path, 'wb') as f:
            f.write(response.content)

        print(f"Generated ElevenLabs audio: {mp3_path}")
        return mp3_path

    except Exception as e:
        print(f"ElevenLabs TTS error: {e}")
        return None

def queue_audio_in_liquidsoap(audio_file):
    """Queue audio file in Liquidsoap TTS queue"""
    try:
        if not audio_file or not os.path.exists(audio_file):
            print(f"Audio file not found: {audio_file}")
            return False

        # Create file URI
        file_uri = f"file://{os.path.abspath(audio_file)}"

        # Queue in Liquidsoap via telnet
        cmd = f"tts.push {file_uri}"
        result = telnet_cmd(cmd, timeout=2)

        print(f"Queued in Liquidsoap: {file_uri}")
        print(f"Liquidsoap response: {result}")

        return True

    except Exception as e:
        print(f"Error queuing audio in Liquidsoap: {e}")
        return False

# ADD THIS after your existing State section (around line 35)
last_dj_time = 0

app = Flask(__name__)

# Load DJ config at startup
dj_config = load_dj_config()

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

def push_event(ev: dict):
    """Insert newest-first with light de-duplication and persist."""
    now_ms = int(time.time() * 1000)

    # ... existing normalization code ...

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

    # Save DJ lines to file if enabled
    if ev.get("type") == "dj" and dj_config.get("save_to_file", True):
        try:
            dj_text = ev.get("text", "")
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            line = f"{timestamp} - {dj_text}\n"

            Path(DJ_LINES_FILE).parent.mkdir(parents=True, exist_ok=True)
            with open(DJ_LINES_FILE, "a", encoding="utf-8") as f:
                f.write(line)
            print(f"Saved DJ line to file: {dj_text}")
        except Exception as e:
            print(f"Error saving DJ line to file: {e}")

    save_history()

    # IMPROVED: Only generate DJ if no recent DJ lines and auto-DJ enabled
    if ev.get("type") == "song" and dj_config.get("auto_dj_enabled", False):
        
        # Check for recent DJ lines (within last 45 seconds)
        recent_dj_lines = [
            event for event in HISTORY[:5] 
            if event.get("type") == "dj" and 
            (now_ms - event.get("time", 0)) < 45000  # 45 seconds
        ]
        
        if recent_dj_lines:
            print(f"DJ: Skipping auto-generation, found recent DJ line: {recent_dj_lines[0].get('text', '')[:50]}...")
            return
        
        print(f"DJ: No recent DJ lines found, generating for: {ev.get('title')} by {ev.get('artist')}")
        
        # Generate DJ line immediately in a separate thread
        def generate_dj_now():
            try:
                print(f"DJ Thread: Starting generation for {ev.get('title')} by {ev.get('artist')}")
                
                dj_text, audio_url = generate_dj_line_with_audio(
                    title=ev.get("title", "Unknown"),
                    artist=ev.get("artist", "Unknown Artist"),
                    album=ev.get("album", "")
                )

                if dj_text:
                    dj_event = {
                        "type": "dj",
                        "text": dj_text,
                        "time": int(time.time() * 1000),
                        "audio_url": audio_url,
                        "auto_generated": True
                    }

                    # Add to history without triggering another DJ line
                    print(f"DJ Thread: Adding DJ event to history: {dj_text}")
                    HISTORY.insert(0, dj_event)
                    del HISTORY[MAX_HISTORY:]
                    save_history()
                    
                    print(f"DJ Thread: Successfully generated DJ line: {dj_text}")
                else:
                    print("DJ Thread: No DJ text generated")
                    
            except Exception as e:
                print(f"DJ Thread: Error generating DJ line: {e}")
                import traceback
                traceback.print_exc()

        # Start the DJ generation in a separate thread
        threading.Thread(target=generate_dj_now, daemon=True).start()

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

# ADD THESE FUNCTIONS after your existing utility functions (around line 300, before push_event)
def should_trigger_dj():
    """Determine if we should trigger a DJ line based on probability and timing"""
    global last_dj_time

    if not dj_config.get("auto_dj_enabled", True):
        return False

    current_time = time.time()
    time_since_last = current_time - last_dj_time
    min_interval = dj_config.get("min_interval_minutes", 5) * 60
    max_interval = dj_config.get("max_interval_minutes", 15) * 60
    probability = dj_config.get("dj_probability", 30)

    # Force DJ line if too much time has passed
    if time_since_last > max_interval:
        print(f"DJ: Forcing DJ line after {time_since_last/60:.1f} minutes")
        return True

    # Check probability if enough time has passed
    if time_since_last > min_interval:
        chance = random.randint(1, 100)
        if chance <= probability:
            print(f"DJ: Triggering DJ line (chance: {chance} <= {probability})")
            return True
        else:
            print(f"DJ: Not triggering DJ line (chance: {chance} > {probability})")

    return False

# Add this new function for manual pre-generation of intros:
@app.post("/api/dj-pregenerate")
def api_dj_pregenerate():
    """Pre-generate DJ intros for upcoming tracks"""
    try:
        # Get next few tracks
        next_tracks = read_next(max_items=3)

        if not next_tracks:
            return jsonify({
                "ok": False,
                "error": "No upcoming tracks found"
            })

        generated = []

        for track in next_tracks:
            title = track.get("title", "Unknown")
            artist = track.get("artist", "Unknown Artist")
            album = track.get("album", "")

            print(f"Pre-generating intro for: {title} by {artist}")

            # Generate DJ line and audio
            dj_text, audio_url = generate_dj_line_with_audio(
                title=title,
                artist=artist,
                album=album
            )

            if dj_text:
                generated.append({
                    "track": f"{title} by {artist}",
                    "dj_text": dj_text,
                    "audio_url": audio_url
                })

        return jsonify({
            "ok": True,
            "generated_count": len(generated),
            "intros": generated
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

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

# ADD configuration endpoint for TTS settings
@app.get("/api/dj/tts-config")
def get_tts_config():
    """Get TTS configuration"""
    return jsonify({
        "provider": TTS_PROVIDER,
        "piper_model_path": PIPER_MODEL_PATH,
        "elevenlabs_configured": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
        "output_directory": TTS_OUTPUT_DIR
    })

@app.post("/api/dj/tts-config")
def update_tts_config():
    """Update TTS configuration"""
    global TTS_PROVIDER, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

    try:
        data = request.get_json(force=True, silent=True) or {}

        if "provider" in data and data["provider"] in ["piper", "elevenlabs"]:
            TTS_PROVIDER = data["provider"]

        if "elevenlabs_api_key" in data:
            ELEVENLABS_API_KEY = data["elevenlabs_api_key"]

        if "elevenlabs_voice_id" in data:
            ELEVENLABS_VOICE_ID = data["elevenlabs_voice_id"]

        return jsonify({"ok": True, "provider": TTS_PROVIDER})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

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

# Update your api_dj_now function in app.py to check for recent DJ lines:

@app.post("/api/dj-now")
def api_dj_now():
    """Generate a DJ line with audio for the current track and save it to timeline"""
    try:
        # Check if there's already a recent DJ line (within last 30 seconds)
        current_time = int(time.time() * 1000)
        recent_dj_lines = [
            event for event in HISTORY[:5]
            if event.get("type") == "dj" and
            (current_time - event.get("time", 0)) < 30000  # 30 seconds
        ]

        if recent_dj_lines:
            recent_dj = recent_dj_lines[0]
            time_diff = (current_time - recent_dj.get("time", 0)) // 1000
            return jsonify({
                "ok": True,
                "message": f"DJ line already generated {time_diff} seconds ago",
                "recent_dj": recent_dj.get("text", ""),
                "skipped": True
            })

        # Get current track info
        now_data = read_now() or {}

        # Generate DJ text and audio
        artist = now_data.get("artist", "Unknown Artist")
        title = now_data.get("title", "Unknown Track")
        album = now_data.get("album", "")

        dj_text, audio_url = generate_dj_line_with_audio(title=title, artist=artist, album=album)

        # Save to timeline
        dj_event = {
            "type": "dj",
            "text": dj_text,
            "time": current_time,
            "audio_url": audio_url,
            "manual_generated": True
        }

        push_event(dj_event)

        return jsonify({
            "ok": True,
            "text": dj_text,
            "audio_url": audio_url,
            "event": dj_event
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

@app.get("/api/dj/config")
def get_dj_config():
    """Get current DJ configuration"""
    return jsonify(dj_config)

@app.post("/api/dj/config")
def update_dj_config():
    """Update DJ configuration"""
    global dj_config
    try:
        data = request.get_json(force=True, silent=True) or {}
        for key, value in data.items():
            if key in DEFAULT_DJ_CONFIG:
                dj_config[key] = value

        save_dj_config(dj_config)
        return jsonify({"ok": True, "config": dj_config})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/dj/test-template")
def test_dj_template():
    """Test a DJ template with sample data"""
    try:
        template = request.json.get('template', '')
        sample_data = {
            'artist': 'Test Artist',
            'title': 'Sample Song',
            'album': 'Test Album'
        }
        result = template.format(**sample_data)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

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

# ADD this new route to serve TTS files
@app.route("/api/tts-file/<filename>")
def serve_tts_file(filename):
    """Serve generated TTS files"""
    try:
        # Security: only allow files from TTS directory
        safe_path = os.path.join(TTS_OUTPUT_DIR, os.path.basename(filename))
        if os.path.exists(safe_path) and safe_path.startswith(TTS_OUTPUT_DIR):
            return send_file(safe_path, mimetype="audio/mpeg")
        else:
            return abort(404)
    except Exception:
        return abort(404)

@app.route("/api/debug-dj")
def debug_dj():
    """Debug DJ generation step by step"""
    debug_log = "/tmp/dj_debug.log"

    def log_debug(msg):
        with open(debug_log, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
        print(msg)  # Also print to console

    log_debug("=== DEBUG DJ GENERATION ===")

    # Test data
    title = "Bohemian Rhapsody"
    artist = "Queen"

    try:
        log_debug(f"1. Testing generate_ai_dj_line with title='{title}', artist='{artist}'")

        # Test the AI function directly
        ai_result = generate_ai_dj_line(title, artist)
        log_debug(f"2. AI result: {ai_result}")

        # Test the full DJ line function
        log_debug(f"3. Testing generate_dj_line...")
        dj_result = generate_dj_line(title, artist)
        log_debug(f"4. DJ line result: {dj_result}")

        # Test the full audio generation
        log_debug(f"5. Testing generate_dj_line_with_audio...")
        text, audio_url = generate_dj_line_with_audio(title, artist)
        log_debug(f"6. Full result - text: {text}, audio: {audio_url}")

        return jsonify({
            "ai_result": ai_result,
            "dj_result": dj_result,
            "full_text": text,
            "audio_url": audio_url,
            "config": dj_config,
            "debug_log_file": debug_log
        })

    except Exception as e:
        log_debug(f"ERROR in debug: {e}")
        import traceback
        log_debug(traceback.format_exc())
        return jsonify({"error": str(e), "debug_log_file": debug_log}), 500

@app.route("/api/debug-dj-status")
def debug_dj_status():
    """Debug current DJ system status"""
    try:
        # Check recent DJ events in history
        recent_dj_events = [
            {
                "type": event.get("type"),
                "text": event.get("text", "")[:100] + "..." if len(event.get("text", "")) > 100 else event.get("text", ""),
                "time": event.get("time"),
                "audio_url": event.get("audio_url"),
                "auto_generated": event.get("auto_generated", False),
            }
            for event in HISTORY[:10] if event.get("type") == "dj"
        ]

        # Check TTS directory
        tts_files = []
        if os.path.exists(TTS_OUTPUT_DIR):
            tts_files = [f for f in os.listdir(TTS_OUTPUT_DIR) if f.endswith(('.mp3', '.wav'))]

        # Test AI generation
        test_result = None
        try:
            test_result = generate_ai_dj_line("Test Song", "Test Artist")
        except Exception as e:
            test_result = f"Error: {str(e)}"

        # Test Liquidsoap queue
        queue_status = None
        try:
            queue_status = telnet_cmd("tts.queue", timeout=2)
        except Exception as e:
            queue_status = f"Error: {str(e)}"

        return jsonify({
            "config": {
                "auto_dj_enabled": dj_config.get("auto_dj_enabled", False),
                "script_path": dj_config.get("ai_script_path", ""),
                "script_exists": os.path.exists(dj_config.get("ai_script_path", "")),
                "script_executable": os.access(dj_config.get("ai_script_path", ""), os.X_OK) if os.path.exists(dj_config.get("ai_script_path", "")) else False,
                "tts_provider": TTS_PROVIDER,
                "piper_model_exists": os.path.exists(PIPER_MODEL_PATH)
            },
            "recent_dj_events": recent_dj_events,
            "ai_test_result": test_result,
            "tts_files": tts_files[:10],  # Last 10 files
            "liquidsoap_queue_length": queue_status,
            "history_count": len(HISTORY),
            "song_events_count": len([e for e in HISTORY if e.get("type") == "song"])
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/test-full-dj")
def test_full_dj():
    """Test the complete DJ generation and queueing process"""
    try:
        # Get current track
        now_data = read_now() or {}
        title = now_data.get("title", "Test Song")
        artist = now_data.get("artist", "Test Artist")

        print(f"=== TESTING FULL DJ PROCESS ===")
        print(f"Title: {title}")
        print(f"Artist: {artist}")

        # Step 1: Test AI generation
        print("Step 1: Testing AI generation...")
        ai_text = generate_ai_dj_line(title, artist)
        print(f"AI Result: {ai_text}")

        # Step 2: Test audio generation
        print("Step 2: Testing audio generation...")
        if ai_text:
            audio_file = generate_dj_audio(ai_text, provider=TTS_PROVIDER)
            print(f"Audio file: {audio_file}")
            print(f"Audio exists: {os.path.exists(audio_file) if audio_file else False}")

            # Step 3: Test Liquidsoap queueing
            print("Step 3: Testing Liquidsoap queueing...")
            if audio_file and os.path.exists(audio_file):
                queue_result = queue_audio_in_liquidsoap(audio_file)
                print(f"Queue result: {queue_result}")

                # Check queue length
                queue_length = telnet_cmd("tts.length", timeout=2)
                print(f"Queue length after: {queue_length}")

                return jsonify({
                    "ok": True,
                    "steps": {
                        "ai_generation": ai_text,
                        "audio_file": audio_file,
                        "audio_exists": os.path.exists(audio_file),
                        "queue_result": queue_result,
                        "queue_length": queue_length
                    }
                })
            else:
                return jsonify({
                    "ok": False,
                    "error": "Audio file not created",
                    "ai_text": ai_text,
                    "audio_file": audio_file
                })
        else:
            return jsonify({
                "ok": False,
                "error": "AI generation failed",
                "ai_text": ai_text
            })

    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500
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