"""
Tests for API routes and WebSocket handlers
"""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from flask import Flask
from flask_socketio import SocketIO

from routes.main import main_bp
from routes.api import api_bp
from routes.websocket import register_websocket_handlers

class TestMainRoutes(unittest.TestCase):
    
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()
    
    def test_index_route(self):
        """Test the home route"""
        # Create a temporary index.html file
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            f.write(b'<html><body>Test</body></html>')
            temp_path = Path(f.name)
        
        try:
            with patch.object(Path, '__truediv__', return_value=temp_path):
                response = self.client.get('/')
                
                self.assertEqual(response.status_code, 200)
                # Check cache headers are set
                self.assertEqual(response.headers.get('Cache-Control'), 
                               'no-cache, no-store, must-revalidate')
        finally:
            temp_path.unlink()

class TestAPIRoutes(unittest.TestCase):
    
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        
        # Mock the services to avoid dependencies
        self.metadata_service_mock = MagicMock()
        self.history_service_mock = MagicMock()
        self.tts_service_mock = MagicMock()
        
        with patch('routes.api.metadata_service', self.metadata_service_mock), \
             patch('routes.api.history_service', self.history_service_mock), \
             patch('routes.api.tts_service', self.tts_service_mock):
            self.app.register_blueprint(api_bp, url_prefix='/api')
        
        self.client = self.app.test_client()
    
    def test_api_event_song(self):
        """Test API event endpoint with song event"""
        with patch('routes.api.push_event') as mock_push:
            response = self.client.get('/api/event?type=song&title=Test&artist=Artist')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data['ok'])
            mock_push.assert_called_once()
    
    def test_api_event_invalid_type(self):
        """Test API event endpoint with invalid type"""
        response = self.client.get('/api/event?type=invalid')
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['ok'])
    
    def test_api_now(self):
        """Test current track API endpoint"""
        mock_track = {
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album"
        }
        
        with patch('routes.api.metadata_service') as mock_service:
            mock_service.get_current_track.return_value = mock_track
            
            response = self.client.get('/api/now')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertEqual(data['title'], 'Test Song')
    
    def test_api_history(self):
        """Test history API endpoint"""
        mock_history = [
            {"title": "Song 1", "artist": "Artist 1", "time": 1000},
            {"title": "Song 2", "artist": "Artist 2", "time": 2000}
        ]
        
        with patch('routes.api.history_service') as mock_service:
            mock_service.get_history.return_value = mock_history
            
            response = self.client.get('/api/history?limit=2')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertIn('history', data)
            self.assertEqual(len(data['history']), 2)
    
    def test_api_tts_status(self):
        """Test TTS status API endpoint"""
        mock_status = {
            "queue_size": 5,
            "latest_file": "test.mp3",
            "latest_time": 1234567890
        }
        
        with patch('routes.api.tts_service') as mock_service:
            mock_service.get_tts_queue_status.return_value = mock_status
            
            response = self.client.get('/api/tts/status')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertEqual(data['queue_size'], 5)
    
    def test_get_dj_prompts(self):
        """Test getting DJ prompts"""
        mock_settings = {"prompts": ["prompt1", "prompt2"]}
        
        with patch('routes.api.safe_json_read', return_value=mock_settings):
            response = self.client.get('/api/dj-prompts')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertEqual(data['prompts'], ["prompt1", "prompt2"])
    
    def test_set_active_prompts(self):
        """Test setting active DJ prompts"""
        with patch('routes.api.safe_json_read', return_value={}), \
             patch('routes.api.safe_json_write', return_value=True):
            
            response = self.client.post('/api/dj-prompts/active',
                                      data=json.dumps({"prompt_id": "test_prompt"}),
                                      content_type='application/json')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data['ok'])
    
    def test_set_active_prompts_missing_data(self):
        """Test setting active prompts with missing data"""
        response = self.client.post('/api/dj-prompts/active',
                                  data=json.dumps({}),
                                  content_type='application/json')
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)
    
    def test_add_custom_prompt(self):
        """Test adding custom DJ prompt"""
        existing_settings = {"custom_prompts": []}
        
        with patch('routes.api.safe_json_read', return_value=existing_settings), \
             patch('routes.api.safe_json_write', return_value=True):
            
            response = self.client.post('/api/dj-prompts/custom',
                                      data=json.dumps({"prompt": "Custom prompt text"}),
                                      content_type='application/json')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data['ok'])
            self.assertIn('prompt', data)

class TestWebSocketHandlers(unittest.TestCase):
    
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.socketio = SocketIO(self.app)
        
        # Register WebSocket handlers
        register_websocket_handlers(self.socketio)
        
        self.client = self.socketio.test_client(self.app)
    
    def test_websocket_connect(self):
        """Test WebSocket connection"""
        with patch('routes.websocket.metadata_service') as mock_service:
            mock_service.get_current_track.return_value = {
                "title": "Test Song",
                "artist": "Test Artist"
            }
            
            self.assertTrue(self.client.is_connected())
            
            # Should receive initial track update
            received = self.client.get_received()
            self.assertTrue(len(received) > 0)
    
    def test_websocket_request_current_track(self):
        """Test requesting current track via WebSocket"""
        with patch('routes.websocket.metadata_service') as mock_service:
            mock_service.get_current_track.return_value = {
                "title": "Current Song",
                "artist": "Current Artist",
                "album": "Current Album"
            }
            
            self.client.emit('request_current_track')
            received = self.client.get_received()
            
            # Should receive track_update event
            track_events = [r for r in received if r['name'] == 'track_update']
            self.assertTrue(len(track_events) > 0)
            
            track_data = track_events[0]['args'][0]
            self.assertEqual(track_data['title'], 'Current Song')
    
    def test_websocket_request_history(self):
        """Test requesting history via WebSocket"""
        mock_history = [
            {"title": "Song 1", "artist": "Artist 1"},
            {"title": "Song 2", "artist": "Artist 2"}
        ]
        
        with patch('routes.websocket.HistoryService') as mock_service_class:
            mock_service = MagicMock()
            mock_service.get_history.return_value = mock_history
            mock_service_class.return_value = mock_service
            
            self.client.emit('request_history')
            received = self.client.get_received()
            
            # Should receive history_update event
            history_events = [r for r in received if r['name'] == 'history_update']
            self.assertTrue(len(history_events) > 0)
    
    def test_websocket_disconnect(self):
        """Test WebSocket disconnection"""
        self.client.disconnect()
        self.assertFalse(self.client.is_connected())

if __name__ == '__main__':
    unittest.main()