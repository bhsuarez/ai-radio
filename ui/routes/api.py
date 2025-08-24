"""
API routes for AI Radio
"""
import os
import json
import time
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
        row = {
            "type": "song",
            "time": now_ms,
            "title": request.args.get("title", "")[:512],
            "artist": request.args.get("artist", "")[:512],
            "album": request.args.get("album", "")[:512],
            "filename": request.args.get("filename", ""),
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
    
    # Fallback to default cover
    return send_file("/opt/ai-radio/ui/static/station-cover.jpg"), 200

@api_bp.route("/cover/online", methods=["GET"])  
def api_cover_online():
    """
    Fetch album art online for tracks missing embedded covers.
    This is a placeholder for online cover art lookup.
    """
    # For now, just return the default cover
    # TODO: Implement online cover art lookup (Last.fm, MusicBrainz, etc.)
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
        if not data or 'prompt_id' not in data:
            return jsonify({"error": "Missing prompt_id"}), 400
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Update active prompts
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
        if not data or 'prompt' not in data:
            return jsonify({"error": "Missing prompt"}), 400
        
        # Load current settings
        settings_file = config.ROOT_DIR / "dj_settings.json"
        settings = safe_json_read(settings_file, {})
        
        # Initialize custom prompts if needed
        if 'custom_prompts' not in settings:
            settings['custom_prompts'] = []
        
        # Add new custom prompt
        custom_prompt = {
            "id": f"custom_{len(settings['custom_prompts']) + 1}",
            "name": data.get('name', f"Custom Prompt {len(settings['custom_prompts']) + 1}"),
            "prompt": data['prompt'],
            "created_at": int(time.time())
        }
        
        settings['custom_prompts'].append(custom_prompt)
        
        # Save back to file
        if safe_json_write(settings_file, settings):
            return jsonify({"ok": True, "prompt": custom_prompt})
        else:
            return jsonify({"error": "Failed to save settings"}), 500
            
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