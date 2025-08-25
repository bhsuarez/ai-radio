"""
API routes for AI Radio
"""
import os
import json
import time
import subprocess
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file, abort
from urllib.parse import quote, unquote

from config import config
from services import MetadataService, HistoryService, TTSService
from utils.file import safe_json_read, safe_json_write

# Initialize services
metadata_service = MetadataService()
history_service = HistoryService() 
tts_service = TTSService()

api_bp = Blueprint('api', __name__)

@api_bp.route("/health", methods=["GET"])
def api_health():
    """Health check endpoint for monitoring"""
    try:
        # Basic health checks
        status = {
            "status": "healthy",
            "timestamp": int(time.time()),
            "services": {
                "metadata": "ok",
                "history": "ok",
                "tts": "ok"
            }
        }
        
        # Check if database is accessible
        try:
            history_count = len(history_service.get_history(limit=1))
            status["database"] = "ok"
        except Exception as e:
            status["database"] = f"error: {str(e)}"
            status["status"] = "degraded"
        
        # Check metadata cache
        try:
            metadata_service.get_current_track()
            status["services"]["metadata"] = "ok"
        except Exception as e:
            status["services"]["metadata"] = f"error: {str(e)}"
            status["status"] = "degraded"
        
        return jsonify(status)
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": int(time.time())
        }), 500

@api_bp.route("/event")
def api_event():
    """
    Ingest events from Liquidsoap (announce_song/after_song).
    
    Supported event types:
    - song: Track play events
    - dj: DJ commentary events
    - metadata_refresh: Metadata update events
    """
    from app import socketio, push_event  # Import to avoid circular dependency
    
    ev_type = request.args.get("type", "song")
    now_ms = int(time.time() * 1000)
    
    if ev_type == "song":
        filename = request.args.get("filename", "")
        title = request.args.get("title", "")[:512]
        artist = request.args.get("artist", "")[:512]
        album = request.args.get("album", "")[:512]
        
        # Generate artwork URL for history display
        artwork_url = ""
        if filename:
            artwork_url = f"/api/cover?file={quote(filename)}"
        elif artist and album:
            artwork_url = f"/api/cover/online?artist={quote(artist)}&album={quote(album)}"
        elif artist:
            artwork_url = f"/api/cover/online?artist={quote(artist)}&album={quote(artist)}"
        
        row = {
            "type": "song",
            "time": now_ms,
            "title": title,
            "artist": artist,
            "album": album,
            "filename": filename,
            "artwork_url": artwork_url,
        }
    elif ev_type == "dj":
        row = {
            "type": "dj", 
            "time": now_ms,
            "text": request.args.get("text", "")[:2000],
            "audio_url": request.args.get("audio_url"),
        }
    elif ev_type == "metadata_refresh":
        # Handle metadata refresh events from Liquidsoap track changes
        print(f"DEBUG: Metadata refresh - {request.args.get('artist', 'Unknown')} - {request.args.get('title', 'Unknown')}")
        
        # Emit real-time update to connected clients
        socketio.emit('metadata_update', {
            'artist': request.args.get("artist", ""),
            'title': request.args.get("title", ""),
            'album': request.args.get("album", ""),
            'time': now_ms
        })
        
        return jsonify({"ok": True, "type": "metadata_refresh"})
    else:
        return jsonify({"ok": False, "error": "unknown type"}), 400
    
    # Use the database system via push_event
    push_event(row)
    return jsonify({"ok": True})

@api_bp.route("/cover", methods=["GET"])
def api_cover():
    """
    Return embedded album art for a given audio file.
    Falls back to default station cover if no art found.
    """
    filename = request.args.get("file", "").strip()
    
    if not filename:
        return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200
    
    # Security: validate path
    from utils.security import is_allowed_path
    if not is_allowed_path(filename):
        abort(403)
    
    if not os.path.exists(filename):
        return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200
    
    try:
        # Try to extract album art using Mutagen
        from mutagen import File as MutaFile
        
        audio_file = MutaFile(filename)
        if not audio_file:
            return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200
        
        # Look for album art in various tag formats
        art_data = None
        if hasattr(audio_file, 'pictures') and audio_file.pictures:
            # FLAC
            art_data = audio_file.pictures[0].data
        elif 'APIC:' in audio_file:
            # MP3 ID3v2
            art_data = audio_file['APIC:'].data
        elif 'covr' in audio_file:
            # MP4/M4A
            art_data = audio_file['covr'][0]
        
        if art_data:
            # Determine content type
            content_type = "image/jpeg"  # Default
            if art_data.startswith(b'\x89PNG'):
                content_type = "image/png"
            elif art_data.startswith(b'GIF'):
                content_type = "image/gif"
            
            from io import BytesIO
            return send_file(BytesIO(art_data), mimetype=content_type)
        
    except Exception as e:
        print(f"Error extracting cover art: {e}")
    
    # If no embedded art found, try to extract artist/album from file and do online lookup
    if audio_file:
        try:
            file_artist = ""
            file_album = ""
            
            # Extract metadata for online lookup
            if 'TPE1' in audio_file:  # Artist
                file_artist = str(audio_file['TPE1'][0])
            elif 'ARTIST' in audio_file:
                file_artist = str(audio_file['ARTIST'][0])
                
            if 'TALB' in audio_file:  # Album
                file_album = str(audio_file['TALB'][0])
            elif 'ALBUM' in audio_file:
                file_album = str(audio_file['ALBUM'][0])
            
            # If we have artist info, redirect to online lookup
            if file_artist:
                from flask import redirect
                if file_album:
                    return redirect(f"/api/cover/online?artist={quote(file_artist)}&album={quote(file_album)}")
                else:
                    return redirect(f"/api/cover/online?artist={quote(file_artist)}&album={quote(file_artist)}")
                    
        except Exception as e:
            print(f"Error extracting metadata for online lookup: {e}")
    
    # Final fallback to default cover
    return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200

@api_bp.route("/cover/online", methods=["GET"])  
def api_cover_online():
    """
    Fetch album art for tracks by searching music library files.
    Falls back to default cover if no embedded art found.
    """
    artist = request.args.get("artist", "").strip()
    album = request.args.get("album", "").strip()
    
    if not artist:
        return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200
    
    try:
        # Use the same search mechanism as duration lookup to find audio files
        from mutagen import File as MutaFile
        import subprocess
        import os
        
        # Search for files matching the artist and optionally album
        music_dirs = ["/mnt/music/Music", "/mnt/music/media"]
        
        # Sanitize search terms
        import re
        clean_artist = re.sub(r'[^\w\s\-\.]', '', artist).strip()
        clean_album = re.sub(r'[^\w\s\-\.]', '', album).strip() if album else ""
        
        # Generate search patterns
        patterns = []
        if clean_album:
            patterns.extend([
                f"*{clean_artist}*{clean_album}*",
                f"*{clean_album}*{clean_artist}*",
                f"*{clean_album}*"
            ])
        patterns.extend([
            f"*{clean_artist}*",
        ])
        
        # Search in music directories
        for music_dir in music_dirs:
            if not os.path.exists(music_dir):
                continue
                
            for pattern in patterns:
                try:
                    cmd = ["find", music_dir, "-type", "f", 
                          "(", "-iname", "*.mp3", "-o", "-iname", "*.m4a", "-o", 
                          "-iname", "*.flac", "-o", "-iname", "*.wav", ")",
                          "-ipath", f"*{pattern}*"]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0 and result.stdout.strip():
                        files = result.stdout.strip().split('\n')
                        
                        # Try to extract album art from first matching file
                        for file_path in files[:3]:
                            file_path = file_path.strip()
                            if file_path and os.path.exists(file_path):
                                try:
                                    audio_file = MutaFile(file_path)
                                    if not audio_file:
                                        continue
                                    
                                    # Look for album art in various formats
                                    art_data = None
                                    if hasattr(audio_file, 'pictures') and audio_file.pictures:
                                        # FLAC
                                        art_data = audio_file.pictures[0].data
                                    elif 'APIC:' in audio_file:
                                        # MP3 ID3v2
                                        art_data = audio_file['APIC:'].data
                                    elif 'covr' in audio_file:
                                        # MP4/M4A
                                        art_data = audio_file['covr'][0]
                                    
                                    if art_data:
                                        # Determine content type
                                        content_type = "image/jpeg"  # Default
                                        if art_data.startswith(b'\x89PNG'):
                                            content_type = "image/png"
                                        elif art_data.startswith(b'GIF'):
                                            content_type = "image/gif"
                                        
                                        print(f"Found album art for {artist} - {album} in: {file_path}")
                                        from io import BytesIO
                                        return send_file(BytesIO(art_data), mimetype=content_type)
                                        
                                except Exception as e:
                                    print(f"Error reading album art from {file_path}: {e}")
                                    continue
                                    
                except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                    continue
    
    except Exception as e:
        print(f"Error in album art search: {e}")
    
    # Fallback to default cover
    return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200

@api_bp.route("/dj-prompts", methods=["GET"])
def get_dj_prompts():
    """Get current DJ prompt configuration"""
    try:
        dj_settings = safe_json_read(config.ROOT_DIR / "dj_settings.json", {})
        return jsonify(dj_settings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/dj-prompts/active", methods=["POST"])
def set_active_prompts():
    """Set active prompt styles"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing data"}), 400
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Update active prompts
        if 'intro_prompt' in data:
            if 'ai_prompts' not in settings:
                settings['ai_prompts'] = {}
            settings['ai_prompts']['active_intro_prompt'] = data['intro_prompt']
        
        if 'outro_prompt' in data:
            if 'ai_prompts' not in settings:
                settings['ai_prompts'] = {}
            settings['ai_prompts']['active_outro_prompt'] = data['outro_prompt']
        
        # Backward compatibility
        if 'prompt_id' in data:
            settings['active_prompt'] = data['prompt_id']
        
        # Save back to file
        if safe_json_write(settings_file, settings):
            return jsonify({"ok": True})
        else:
            return jsonify({"error": "Failed to save settings"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/dj-prompts/custom", methods=["POST"])
def add_custom_prompt():
    """Add a custom prompt template"""
    try:
        data = request.get_json()
        if not data or 'prompt' not in data or 'type' not in data:
            return jsonify({"error": "Missing prompt or type"}), 400
        
        prompt_type = data['type']
        if prompt_type not in ['intro', 'outro']:
            return jsonify({"error": "Type must be 'intro' or 'outro'"}), 400
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Ensure AI prompts structure exists
        if 'ai_prompts' not in settings:
            settings['ai_prompts'] = {
                'intro_prompts': [],
                'outro_prompts': [],
                'active_intro_prompt': '',
                'active_outro_prompt': ''
            }
        
        # Create new prompt object
        new_prompt = {
            "name": data.get('name', f"Custom {prompt_type.title()} {len(settings['ai_prompts'][f'{prompt_type}_prompts']) + 1}"),
            "prompt": data['prompt']
        }
        
        # Add to appropriate prompt list
        settings['ai_prompts'][f'{prompt_type}_prompts'].append(new_prompt)
        
        # Save back to file
        if safe_json_write(settings_file, settings):
            return jsonify({"ok": True, "prompt": new_prompt})
        else:
            return jsonify({"error": "Failed to save settings"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/dj-prompts/openai-key", methods=["POST"])
def save_openai_key():
    """Save OpenAI API key"""
    try:
        data = request.get_json()
        if not data or 'api_key' not in data:
            return jsonify({"error": "Missing API key"}), 400
        
        api_key = data['api_key'].strip()
        if not api_key:
            return jsonify({"error": "API key cannot be empty"}), 400
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Ensure openai_config structure exists
        if 'openai_config' not in settings:
            settings['openai_config'] = {}
        
        # Save the API key (in production, this should be encrypted/secure storage)
        settings['openai_config']['api_key'] = api_key
        settings['openai_config']['enabled'] = True
        settings['openai_config']['last_updated'] = int(time.time())
        
        # Save back to file
        if safe_json_write(settings_file, settings):
            return jsonify({"ok": True, "message": "OpenAI API key saved successfully"})
        else:
            return jsonify({"error": "Failed to save API key"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/dj-prompts/config", methods=["POST"])
def update_dj_config():
    """Update DJ configuration settings"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing configuration data"}), 400
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Update configuration values
        valid_keys = ['auto_dj_enabled', 'ai_dj_probability', 'min_interval_minutes', 'max_interval_minutes']
        for key in valid_keys:
            if key in data:
                settings[key] = data[key]
        
        settings['last_config_update'] = int(time.time())
        
        # Save back to file
        if safe_json_write(settings_file, settings):
            return jsonify({"ok": True, "message": "Configuration updated successfully"})
        else:
            return jsonify({"error": "Failed to save configuration"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/now", methods=["GET"])
def api_now():
    """Get current track information"""
    try:
        current_track = metadata_service.get_current_track()
        return jsonify(current_track)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/history", methods=["GET"])
def api_history():
    """Get play history"""
    try:
        limit = request.args.get('limit', 50, type=int)
        history = history_service.get_history(limit=limit)
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/next", methods=["GET"])
def api_next():
    """Get upcoming tracks"""
    try:
        # Refresh next.json if the refresh parameter is provided
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        if refresh:
            try:
                result = subprocess.run(['/opt/ai-radio/refresh_next_from_requests.sh'], 
                                       capture_output=True, text=True, timeout=10)
                print(f"Next track refresh result: {result.returncode}")
            except Exception as e:
                print(f"Failed to refresh next tracks: {e}")
        
        next_track_data = metadata_service.get_next_track()
        next_tracks = []
        
        # Handle both single dict and array formats
        if isinstance(next_track_data, dict):
            next_tracks = [next_track_data]
        elif isinstance(next_track_data, list):
            next_tracks = next_track_data
        
        # Clean up and ensure artwork URLs are present
        for track in next_tracks:
            if not track.get("artwork_url"):
                filename = track.get("filename", "")
                artist = track.get("artist", "")
                album = track.get("album", "")
                
                artwork_url = ""
                if filename:
                    artwork_url = f"/api/cover?file={quote(filename)}"
                elif artist and album:
                    artwork_url = f"/api/cover/online?artist={quote(artist)}&album={quote(album)}"
                elif artist:
                    artwork_url = f"/api/cover/online?artist={quote(artist)}&album={quote(artist)}"
                
                track["artwork_url"] = artwork_url
        
        return jsonify(next_tracks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/skip", methods=["POST"])
def api_skip():
    """Skip current track using Harbor HTTP (replaces telnet)"""
    try:
        import subprocess
        import tempfile
        import os
        
        # Create a very short silence track to force skip
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
            temp_path = temp_file.name
        
        try:
            # Generate 0.1 second of silence using ffmpeg
            result = subprocess.run([
                'ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', 
                '-t', '0.1', '-acodec', 'mp3', '-y', temp_path
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                # Send silence to Harbor music input to force skip
                skip_result = subprocess.run([
                    'curl', '-f', '-X', 'PUT', 'http://127.0.0.1:8001/music',
                    '-H', 'Content-Type: audio/mpeg',
                    '--data-binary', f'@{temp_path}'
                ], capture_output=True, text=True, timeout=10)
                
                if skip_result.returncode == 0:
                    return jsonify({"ok": True, "message": "Track skipped via Harbor"})
                else:
                    return jsonify({"ok": False, "error": f"Harbor skip failed: {skip_result.stderr}"}), 503
            else:
                return jsonify({"ok": False, "error": "Failed to generate skip audio"}), 500
                
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/metadata", methods=["GET"])
def api_metadata():
    """Get current metadata (alias for /now for compatibility)"""
    try:
        current_track = metadata_service.get_current_track()
        return jsonify(current_track)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/dj-now", methods=["GET", "POST"])
def api_dj_now():
    """Handle DJ intro generation requests from Liquidsoap"""
    try:
        # Get track info from query parameters (sent by Liquidsoap)
        artist = request.args.get('artist', 'Unknown Artist')
        title = request.args.get('title', 'Unknown Title')
        
        # Log the DJ request
        print(f"DJ intro request: {title} by {artist}")
        
        # This endpoint is called by Liquidsoap's auto_generate_dj_intro function
        # The actual TTS generation is handled via the TTS service
        if artist and title:
            # Create event data for DJ generation
            track_data = {
                'title': title,
                'artist': artist,
                'time': int(time.time() * 1000)
            }
            
            # Trigger DJ generation
            success = tts_service.generate_dj_intro(track_data)
            
            return jsonify({
                "ok": True, 
                "message": f"DJ intro {'requested' if success else 'throttled'} for {title} by {artist}"
            })
        else:
            return jsonify({"ok": False, "error": "Missing artist or title"}), 400
            
    except Exception as e:
        print(f"Error in /api/dj-now: {e}")
        return jsonify({"error": str(e)}), 500

@api_bp.route("/tts/status", methods=["GET"])
def api_tts_status():
    """Get TTS queue status"""
    try:
        status = tts_service.get_tts_queue_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/tts/files", methods=["GET"])
def api_tts_files():
    """List recent TTS files"""
    try:
        limit = request.args.get('limit', 10, type=int)
        files = tts_service.list_tts_files(limit=limit)
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/tts/speakers", methods=["GET"])
def api_get_speakers():
    """Get available TTS speakers and current selection"""
    try:
        speakers_dir = Path("/opt/ai-radio/tts/speaker_samples")
        available_speakers = []
        
        if speakers_dir.exists():
            for audio_file in speakers_dir.glob("*.mp3"):
                speaker_name = audio_file.stem
                available_speakers.append({
                    "name": speaker_name,
                    "display_name": speaker_name.replace("_", " "),
                    "sample_url": f"/api/tts/speakers/{speaker_name}/sample"
                })
        
        # Sort speakers alphabetically
        available_speakers.sort(key=lambda x: x["display_name"])
        
        # Get current speaker from settings
        settings = safe_json_read(config.ROOT_DIR / "dj_settings.json", {})
        current_speaker = settings.get("tts_voice", "Damien Black")
        
        return jsonify({
            "speakers": available_speakers,
            "current_speaker": current_speaker
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/tts/speakers/current", methods=["POST"])
def api_set_current_speaker():
    """Set the current TTS speaker"""
    try:
        data = request.get_json()
        if not data or 'speaker' not in data:
            return jsonify({"error": "Missing speaker name"}), 400
        
        speaker_name = data['speaker']
        
        # Validate speaker exists
        speaker_file = Path(f"/opt/ai-radio/tts/speaker_samples/{speaker_name}.mp3")
        if not speaker_file.exists():
            return jsonify({"error": "Speaker not found"}), 404
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Update TTS voice
        settings['tts_voice'] = speaker_name
        
        # Save back to file
        if safe_json_write(settings_file, settings):
            return jsonify({"ok": True, "speaker": speaker_name})
        else:
            return jsonify({"error": "Failed to save settings"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/tts/speakers/<speaker_name>/sample", methods=["GET"])
def api_get_speaker_sample(speaker_name):
    """Get speaker sample audio file"""
    try:
        # Security: validate speaker name (no path traversal)
        if '.' in speaker_name or '/' in speaker_name or '\\' in speaker_name:
            abort(400)
        
        sample_file = Path(f"/opt/ai-radio/tts/speaker_samples/{speaker_name}.mp3")
        
        if not sample_file.exists():
            abort(404)
        
        return send_file(str(sample_file), mimetype="audio/mpeg")
    except Exception as e:
        abort(500)

@api_bp.route("/tts/speakers/<speaker_name>/preview", methods=["POST"])
def api_preview_speaker():
    """Generate a preview TTS with the selected speaker"""
    try:
        data = request.get_json()
        preview_text = data.get('text', 'Hello! This is a preview of my voice.')
        
        # Security: validate speaker name
        speaker_name = request.view_args['speaker_name']
        if '.' in speaker_name or '/' in speaker_name or '\\' in speaker_name:
            return jsonify({"error": "Invalid speaker name"}), 400
        
        # Validate speaker exists
        speaker_file = Path(f"/opt/ai-radio/tts/speaker_samples/{speaker_name}.mp3")
        if not speaker_file.exists():
            return jsonify({"error": "Speaker not found"}), 404
        
        # Generate preview using XTTS script
        preview_script = "/opt/ai-radio/dj_enqueue_xtts.sh"
        if not os.path.exists(preview_script):
            return jsonify({"error": "TTS system not available"}), 503
        
        # Execute preview generation
        try:
            result = subprocess.run([
                preview_script,
                preview_text,
                "en",  # language
                speaker_name
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return jsonify({"ok": True, "message": "Preview generated successfully"})
            else:
                return jsonify({"error": "Preview generation failed"}), 500
                
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Preview generation timed out"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500