#!/usr/bin/env python3
"""
AI Radio Flask Application - Refactored
Modern, modular architecture with clean separation of concerns
"""
import sys
import time
sys.path.append('/opt/ai-radio')  # Add parent directory to path

from flask import Flask
from flask_socketio import SocketIO

from config import config
from routes import register_routes, register_websocket_handlers
from services import MetadataService, HistoryService, TTSService
from services.event_watcher import EventWatcher

# Initialize Flask application
app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY

# Initialize SocketIO with CORS support
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize services
metadata_service = MetadataService()
history_service = HistoryService()
tts_service = TTSService()

# Initialize event watcher for Liquidsoap fallback
event_watcher = EventWatcher(push_event_callback=lambda event_data: push_event(event_data))

# Register routes and WebSocket handlers
register_routes(app)
register_websocket_handlers(socketio)

# Legacy function imports for backward compatibility with existing Liquidsoap integration
# These will be gradually migrated to the new service layer
def push_event(event_data):
    """
    Push event to history service and trigger real-time updates.
    
    This function maintains compatibility with existing Liquidsoap integrations
    while using the new service architecture under the hood.
    """
    if event_data.get('type') == 'song':
        # Add to history
        success = history_service.add_track(event_data)
        
        # Trigger DJ generation if appropriate
        if success and event_data.get('title') and event_data.get('artist'):
            tts_service.generate_dj_intro(event_data)
        
        # Broadcast to connected clients
        from routes.websocket import broadcast_track_change
        track_started_at = event_data.get('time', int(time.time() * 1000)) / 1000
        broadcast_track_change(socketio, {
            'title': event_data.get('title', ''),
            'artist': event_data.get('artist', ''),
            'album': event_data.get('album', ''),
            'artwork_url': event_data.get('artwork_url', ''),
            'filename': event_data.get('filename', ''),
            'type': 'song',
            'timestamp': track_started_at,
            'track_started_at': track_started_at
        })
    
    elif event_data.get('type') == 'dj':
        # Add DJ commentary to history
        success = history_service.add_track(event_data)
        
        # Handle DJ commentary events
        socketio.emit('dj_update', {
            'text': event_data.get('text', ''),
            'audio_url': event_data.get('audio_url', ''),
            'timestamp': event_data.get('time', 0) / 1000
        })
        
        # Broadcast history update for DJ events
        if success:
            from routes.websocket import broadcast_history_update
            broadcast_history_update(socketio, event_data)

# Legacy function for database integration compatibility
def get_history():
    """Compatibility wrapper for database integration"""
    return history_service.get_history()

def add_history_entry(entry):
    """Compatibility wrapper for database integration"""
    return history_service.add_track(entry)

# Application startup
if __name__ == "__main__":
    import atexit
    
    print("🎵 AI Radio Flask Application Starting...")
    print(f"📡 Server: {config.HOST}:{config.PORT}")
    print(f"📁 Root Directory: {config.ROOT_DIR}")
    print(f"🎤 TTS Directory: {config.tts_root}")
    
    # Start the event file watcher for Liquidsoap fallback
    event_watcher.start()
    atexit.register(event_watcher.stop)
    
    print("📁 Event file watcher started")
    print("🚀 Ready to rock!")
    
    try:
        socketio.run(
            app, 
            host=config.HOST, 
            port=config.PORT, 
            debug=False,
            allow_unsafe_werkzeug=True  # Required for production deployment
        )
    finally:
        event_watcher.stop()