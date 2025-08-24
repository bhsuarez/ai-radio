"""
Tests for service layer
"""
import unittest
import tempfile
import json
import time
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock

from services.metadata import MetadataService
from services.history import HistoryService  
from services.tts import TTSService

class TestMetadataService(unittest.TestCase):
    
    def setUp(self):
        self.service = MetadataService()
    
    @patch('services.metadata.config')
    def test_get_current_track_from_json(self, mock_config):
        """Test getting current track from JSON file"""
        test_data = {
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album"
        }
        
        # Mock file existence and reading
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_data, f)
            mock_config.NOW_JSON = Path(f.name)
            mock_config.NOW_TXT = Path("/nonexistent")
        
        try:
            result = self.service.get_current_track()
            
            self.assertEqual(result["title"], "Test Song")
            self.assertEqual(result["artist"], "Test Artist")
            self.assertEqual(result["album"], "Test Album")
        finally:
            Path(f.name).unlink()
    
    @patch('services.metadata.config')
    def test_get_current_track_defaults(self, mock_config):
        """Test default values when no metadata available"""
        mock_config.NOW_JSON = Path("/nonexistent.json")
        mock_config.NOW_TXT = Path("/nonexistent.txt")
        
        with patch.object(self.service, '_get_telnet_metadata', return_value={}):
            result = self.service.get_current_track()
            
            self.assertEqual(result["title"], "Unknown title")
            self.assertEqual(result["artist"], "Unknown artist")
    
    def test_parse_telnet_metadata(self):
        """Test parsing telnet metadata response"""
        raw_response = """--- 0 ---
title="Previous Song"
artist="Previous Artist"
--- 1 ---
title="Current Song"
artist="Current Artist"
album="Current Album"
--- 2 ---
title="Next Song"
artist="Next Artist"
END"""
        
        result = self.service._parse_telnet_metadata(raw_response)
        
        self.assertEqual(result["title"], "Current Song")
        self.assertEqual(result["artist"], "Current Artist")
        self.assertEqual(result["album"], "Current Album")
    
    def test_parse_kv_metadata_text(self):
        """Test parsing key-value metadata from text file"""
        result = self.service._read_text_metadata()
        
        # Should return empty dict when file doesn't exist
        self.assertEqual(result, {})

class TestHistoryService(unittest.TestCase):
    
    def setUp(self):
        with patch('services.history.config.HISTORY_FILE', Path("/tmp/test_history.json")):
            self.service = HistoryService()
    
    def test_add_track_success(self):
        """Test successfully adding a track to history"""
        track_data = {
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album",
            "time": int(time.time() * 1000)
        }
        
        with patch.object(self.service, '_save_to_disk'):
            result = self.service.add_track(track_data)
            
            self.assertTrue(result)
            
            # Verify track was added to history
            history = self.service.get_history(limit=1)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["title"], "Test Song")
    
    def test_add_track_deduplication(self):
        """Test that duplicate tracks are filtered out"""
        track_data = {
            "title": "Same Song",
            "artist": "Same Artist",
            "time": int(time.time() * 1000)
        }
        
        with patch.object(self.service, '_save_to_disk'):
            # Add first time - should succeed
            result1 = self.service.add_track(track_data)
            self.assertTrue(result1)
            
            # Add same track immediately - should be filtered out
            result2 = self.service.add_track(track_data)
            self.assertFalse(result2)
            
            # History should only have one entry
            history = self.service.get_history()
            self.assertEqual(len(history), 1)
    
    def test_get_history_ordering(self):
        """Test that history is returned in correct order (most recent first)"""
        with patch.object(self.service, '_save_to_disk'):
            # Add multiple tracks
            for i in range(3):
                track_data = {
                    "title": f"Song {i}",
                    "artist": "Test Artist",
                    "time": int(time.time() * 1000) + i  # Different timestamps
                }
                self.service.add_track(track_data)
        
        history = self.service.get_history()
        
        # Should be in reverse chronological order
        self.assertEqual(history[0]["title"], "Song 2")  # Most recent first
        self.assertEqual(history[1]["title"], "Song 1")
        self.assertEqual(history[2]["title"], "Song 0")
    
    def test_generate_event_key(self):
        """Test event key generation for deduplication"""
        # Test filename-based key
        event_with_file = {"filename": "/path/to/song.mp3"}
        key1 = self.service._generate_event_key(event_with_file)
        self.assertTrue(key1.startswith("f|"))
        
        # Test metadata-based key
        event_with_metadata = {
            "title": "Test Song",
            "artist": "Test Artist", 
            "album": "Test Album"
        }
        key2 = self.service._generate_event_key(event_with_metadata)
        self.assertTrue(key2.startswith("t|"))
        
        # Keys should be consistent
        key3 = self.service._generate_event_key(event_with_metadata)
        self.assertEqual(key2, key3)

class TestTTSService(unittest.TestCase):
    
    def setUp(self):
        self.service = TTSService()
    
    def test_can_generate_dj_intro_throttling(self):
        """Test DJ intro generation throttling"""
        track_data = {"title": "Test Song", "artist": "Test Artist"}
        
        # Should be able to generate initially
        self.assertTrue(self.service.can_generate_dj_intro(track_data))
        
        # Simulate recent generation
        self.service._last_generation_time = time.time()
        self.service._last_track_key = self.service._get_track_key(track_data)
        
        # Should be throttled now
        self.assertFalse(self.service.can_generate_dj_intro(track_data))
    
    def test_can_generate_dj_intro_different_track(self):
        """Test DJ intro generation for different tracks"""
        track1 = {"title": "Song 1", "artist": "Artist 1"}
        track2 = {"title": "Song 2", "artist": "Artist 2"}
        
        # Generate for first track
        with patch.object(self.service, '_trigger_dj_generation', return_value=True):
            result1 = self.service.generate_dj_intro(track1)
            self.assertTrue(result1)
        
        # Should be able to generate for different track immediately
        self.assertTrue(self.service.can_generate_dj_intro(track2))
    
    def test_get_track_key(self):
        """Test track key generation"""
        # Test filename-based key
        track_with_file = {"filename": "/path/to/song.mp3"}
        key1 = self.service._get_track_key(track_with_file)
        self.assertTrue(key1.startswith("file:"))
        
        # Test metadata-based key
        track_with_metadata = {"title": "Song", "artist": "Artist"}
        key2 = self.service._get_track_key(track_with_metadata)
        self.assertTrue(key2.startswith("track:"))
    
    @patch('services.tts.config')
    def test_get_tts_queue_status(self, mock_config):
        """Test getting TTS queue status"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tts_dir = Path(temp_dir)
            mock_config.tts_root = tts_dir
            
            # Create some test MP3 files
            for i in range(3):
                (tts_dir / f"test_{i}.mp3").touch()
            
            status = self.service.get_tts_queue_status()
            
            self.assertEqual(status["queue_size"], 3)
            self.assertIsNotNone(status["latest_file"])
    
    @patch('services.tts.subprocess.Popen')
    @patch('services.tts.os.path.exists')
    def test_trigger_dj_generation(self, mock_exists, mock_popen):
        """Test triggering DJ generation script"""
        mock_exists.return_value = True
        mock_popen.return_value = MagicMock()
        
        track_data = {"title": "Test Song", "artist": "Test Artist"}
        
        result = self.service._trigger_dj_generation(track_data)
        
        self.assertTrue(result)
        mock_popen.assert_called_once()

if __name__ == '__main__':
    unittest.main()