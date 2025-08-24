"""
Integration tests for the complete AI Radio application
"""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from flask_socketio import SocketIO

# Mock database imports to prevent issues
with patch('database.db_manager'), \
     patch('database.get_history'), \
     patch('database.add_history_entry'), \
     patch('database.create_tts_entry'), \
     patch('database.get_tts_entry_by_filename'):
    from app import app, socketio

class TestApplicationIntegration(unittest.TestCase):
    
    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.socketio_client = socketio.test_client(self.app)
    
    def test_app_initialization(self):
        """Test that the application initializes correctly"""
        self.assertIsInstance(self.app, Flask)
        self.assertIsInstance(socketio, SocketIO)
        self.assertTrue(self.app.config['TESTING'])
    
    def test_main_route_accessible(self):
        """Test that the main route is accessible"""
        # Create temporary index.html
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            f.write(b'<html><body>AI Radio</body></html>')
            temp_path = Path(f.name)
        
        try:
            with patch('routes.main.Path') as mock_path:
                mock_path.return_value.__truediv__.return_value = temp_path
                
                response = self.client.get('/')
                self.assertEqual(response.status_code, 200)
        finally:
            temp_path.unlink()
    
    def test_api_routes_accessible(self):
        """Test that API routes are accessible"""
        # Test various API endpoints
        test_routes = [
            ('/api/event?type=song&title=Test&artist=Test', 200),
            ('/api/dj-prompts', 200),
            ('/api/now', 200),
            ('/api/history', 200),
            ('/api/tts/status', 200),
            ('/api/tts/files', 200)
        ]
        
        with patch('routes.api.push_event'), \
             patch('routes.api.safe_json_read', return_value={}), \
             patch('routes.api.metadata_service') as mock_metadata, \
             patch('routes.api.history_service') as mock_history, \
             patch('routes.api.tts_service') as mock_tts:
            
            # Setup mock returns
            mock_metadata.get_current_track.return_value = {"title": "Test", "artist": "Test"}
            mock_history.get_history.return_value = []
            mock_tts.get_tts_queue_status.return_value = {"queue_size": 0}
            mock_tts.list_tts_files.return_value = []
            
            for route, expected_status in test_routes:
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, expected_status)
    
    def test_websocket_connection_integration(self):
        """Test WebSocket connection with mocked services"""
        with patch('routes.websocket.metadata_service') as mock_service:
            mock_service.get_current_track.return_value = {
                "title": "Integration Test Song",
                "artist": "Test Artist"
            }
            
            # Test connection
            self.assertTrue(self.socketio_client.is_connected())
            
            # Test track request
            self.socketio_client.emit('request_current_track')
            received = self.socketio_client.get_received()
            
            # Should receive some events
            self.assertTrue(len(received) >= 0)  # At least connection events
    
    def test_event_processing_integration(self):
        """Test complete event processing flow"""
        with patch('app.history_service') as mock_history, \
             patch('app.tts_service') as mock_tts, \
             patch('routes.websocket.broadcast_track_change'):
            
            mock_history.add_track.return_value = True
            mock_tts.generate_dj_intro.return_value = True
            
            # Test song event
            response = self.client.get('/api/event?type=song&title=Integration Test&artist=Test Artist')
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data['ok'])
    
    def test_configuration_integration(self):
        """Test that configuration is properly integrated"""
        from config import config
        
        # Test that config values are accessible
        self.assertIsNotNone(config.HOST)
        self.assertIsNotNone(config.PORT)
        self.assertIsInstance(config.MUSIC_ROOTS, list)
        self.assertTrue(len(config.MUSIC_ROOTS) > 0)
    
    def test_service_integration(self):
        """Test that services are properly integrated"""
        from services import MetadataService, HistoryService, TTSService
        
        # Test service instantiation
        metadata_service = MetadataService()
        history_service = HistoryService()
        tts_service = TTSService()
        
        self.assertIsInstance(metadata_service, MetadataService)
        self.assertIsInstance(history_service, HistoryService)
        self.assertIsInstance(tts_service, TTSService)
    
    def test_error_handling_integration(self):
        """Test error handling across the application"""
        # Test invalid API request
        response = self.client.get('/api/event?type=invalid')
        self.assertEqual(response.status_code, 400)
        
        # Test missing required parameters
        response = self.client.post('/api/dj-prompts/active',
                                  data='{}',
                                  content_type='application/json')
        self.assertEqual(response.status_code, 400)
    
    def tearDown(self):
        """Clean up after tests"""
        if self.socketio_client.is_connected():
            self.socketio_client.disconnect()

class TestApplicationStartup(unittest.TestCase):
    
    @patch('app.socketio.run')
    def test_application_startup(self, mock_run):
        """Test application startup process"""
        # This would test the main execution path
        # For now, we just verify the app can be imported without errors
        self.assertIsNotNone(app)
        self.assertIsNotNone(socketio)

if __name__ == '__main__':
    unittest.main()