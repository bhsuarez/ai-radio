"""
WebSocket event handlers for real-time communication
"""
import time
from flask import request
from flask_socketio import emit

from services import MetadataService

# Initialize services
metadata_service = MetadataService()

def register_websocket_handlers(socketio):
    """Register WebSocket event handlers with the SocketIO instance"""
    
    @socketio.on('connect')
    def handle_connect():
        """Handle client connection"""
        print(f"Client connected: {request.sid}")
        
        # Send initial track information
        try:
            current_track = metadata_service.get_current_track()
            if current_track and current_track.get('title') != 'Unknown title':
                emit('track_update', {
                    'title': current_track['title'],
                    'artist': current_track['artist'],
                    'album': current_track.get('album', ''),
                    'artwork_url': current_track.get('artwork_url', ''),
                    'type': 'song',
                    'source': 'metadata_service',
                    'timestamp': time.time()
                })
        except Exception as e:
            print(f"Error sending initial track: {e}")
            emit('error', {'message': f'Failed to get initial track: {e}'})
    
    @socketio.on('disconnect')
    def handle_disconnect():
        """Handle client disconnection"""
        print(f"Client disconnected: {request.sid}")
    
    @socketio.on('request_current_track')
    def handle_track_request():
        """Handle explicit request for current track"""
        try:
            current_track = metadata_service.get_current_track()
            if current_track:
                emit('track_update', {
                    'title': current_track['title'],
                    'artist': current_track['artist'],
                    'album': current_track.get('album', ''),
                    'artwork_url': current_track.get('artwork_url', ''),
                    'filename': current_track.get('filename', ''),
                    'type': 'song',
                    'source': 'metadata_service',
                    'timestamp': time.time()
                })
            else:
                emit('error', {'message': 'No track information available'})
        except Exception as e:
            print(f"Error handling track request: {e}")
            emit('error', {'message': f'Failed to get current track: {e}'})
    
    @socketio.on('request_history')
    def handle_history_request():
        """Handle request for play history"""
        try:
            from services import HistoryService
            history_service = HistoryService()
            
            limit = 20  # Default limit
            history = history_service.get_history(limit=limit)
            emit('history_update', {'history': history})
        except Exception as e:
            print(f"Error handling history request: {e}")
            emit('error', {'message': f'Failed to get history: {e}'})

def broadcast_track_change(socketio, track_info):
    """
    Broadcast track change to all connected clients
    
    Args:
        socketio: SocketIO instance
        track_info: Track information dictionary
    """
    try:
        socketio.emit('track_update', track_info)
    except Exception as e:
        print(f"Error broadcasting track change: {e}")

def broadcast_metadata_update(socketio, metadata):
    """
    Broadcast metadata update to all connected clients
    
    Args:
        socketio: SocketIO instance  
        metadata: Metadata dictionary
    """
    try:
        socketio.emit('metadata_update', metadata)
    except Exception as e:
        print(f"Error broadcasting metadata update: {e}")

def broadcast_history_update(socketio, history_item):
    """
    Broadcast history update to all connected clients
    
    Args:
        socketio: SocketIO instance
        history_item: History item dictionary
    """
    try:
        socketio.emit('history_update', history_item)
    except Exception as e:
        print(f"Error broadcasting history update: {e}")