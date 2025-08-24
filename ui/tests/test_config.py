"""
Tests for configuration management
"""
import unittest
import tempfile
from pathlib import Path

from config import Config

class TestConfig(unittest.TestCase):
    
    def test_config_initialization(self):
        """Test that Config initializes properly"""
        config = Config()
        
        # Test basic properties
        self.assertEqual(config.HOST, "0.0.0.0")
        self.assertEqual(config.PORT, 5055)
        self.assertEqual(config.TELNET_PORT, 1234)
        
        # Test path properties
        self.assertIsInstance(config.ROOT_DIR, Path)
        self.assertIsInstance(config.LOG_DIR, Path)
        self.assertIsInstance(config.COVER_CACHE, Path)
    
    def test_tts_root_property(self):
        """Test TTS root directory selection"""
        config = Config()
        
        # The property should return a Path object
        tts_root = config.tts_root
        self.assertIsInstance(tts_root, Path)
        
        # Should return either TTS_DIR or TTS_FALLBACK_DIR
        self.assertIn(tts_root, [config.TTS_DIR, config.TTS_FALLBACK_DIR])
    
    def test_music_roots_validation(self):
        """Test music roots are properly defined"""
        config = Config()
        
        self.assertIsInstance(config.MUSIC_ROOTS, list)
        self.assertTrue(len(config.MUSIC_ROOTS) > 0)
        self.assertIn("/mnt/music", config.MUSIC_ROOTS)

if __name__ == '__main__':
    unittest.main()