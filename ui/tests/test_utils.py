"""
Tests for utility functions
"""
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from utils.security import is_allowed_path
from utils.text import parse_kv_text, strip_ansi
from utils.file import safe_json_read, safe_json_write, atomic_write

class TestSecurity(unittest.TestCase):
    
    @patch('utils.security.config')
    def test_is_allowed_path_valid(self, mock_config):
        """Test path validation with valid paths"""
        mock_config.MUSIC_ROOTS = ["/mnt/music", "/mnt/music/media"]
        
        self.assertTrue(is_allowed_path("/mnt/music/test.mp3"))
        self.assertTrue(is_allowed_path("/mnt/music/media/album/song.mp3"))
    
    @patch('utils.security.config')
    def test_is_allowed_path_invalid(self, mock_config):
        """Test path validation with invalid paths"""
        mock_config.MUSIC_ROOTS = ["/mnt/music", "/mnt/music/media"]
        
        self.assertFalse(is_allowed_path("/tmp/malicious.mp3"))
        self.assertFalse(is_allowed_path("/etc/passwd"))
        self.assertFalse(is_allowed_path("../../../etc/passwd"))
        self.assertFalse(is_allowed_path(""))
        self.assertFalse(is_allowed_path(None))
    
    @patch('utils.security.config')
    def test_is_allowed_path_edge_cases(self, mock_config):
        """Test edge cases for path validation"""
        mock_config.MUSIC_ROOTS = ["/mnt/music"]
        
        # Path traversal attempts
        self.assertFalse(is_allowed_path("/mnt/music/../../../etc/passwd"))
        self.assertFalse(is_allowed_path("/mnt/music/../../tmp/hack"))

class TestTextUtils(unittest.TestCase):
    
    def test_parse_kv_text_valid(self):
        """Test parsing valid key-value text"""
        text = "artist=Test Artist\ntitle=Test Song\nalbum=Test Album"
        result = parse_kv_text(text)
        
        expected = {
            "artist": "Test Artist",
            "title": "Test Song",
            "album": "Test Album"
        }
        self.assertEqual(result, expected)
    
    def test_parse_kv_text_with_quotes(self):
        """Test parsing key-value text with quotes"""
        text = 'artist="Quoted Artist"\ntitle="Song Title"'
        result = parse_kv_text(text)
        
        expected = {
            "artist": "Quoted Artist",
            "title": "Song Title"
        }
        self.assertEqual(result, expected)
    
    def test_parse_kv_text_malformed(self):
        """Test parsing malformed key-value text"""
        text = "invalid_line\nartist=Valid Artist\nanother_invalid"
        result = parse_kv_text(text)
        
        # Should skip invalid lines
        self.assertEqual(result, {"artist": "Valid Artist"})
    
    def test_parse_kv_text_empty(self):
        """Test parsing empty text"""
        self.assertEqual(parse_kv_text(""), {})
        self.assertEqual(parse_kv_text(None), {})
    
    def test_strip_ansi_sequences(self):
        """Test ANSI sequence removal"""
        text_with_ansi = "\x1B[31mRed text\x1B[0m normal text \x1B[1mbold\x1B[22m"
        result = strip_ansi(text_with_ansi)
        
        self.assertEqual(result, "Red text normal text bold")
    
    def test_strip_ansi_no_sequences(self):
        """Test stripping when no ANSI sequences present"""
        normal_text = "This is normal text"
        result = strip_ansi(normal_text)
        
        self.assertEqual(result, normal_text)
    
    def test_strip_ansi_empty(self):
        """Test stripping empty/None text"""
        self.assertEqual(strip_ansi(""), "")
        self.assertEqual(strip_ansi(None), None)

class TestFileUtils(unittest.TestCase):
    
    def test_safe_json_read_valid_file(self):
        """Test reading valid JSON file"""
        test_data = {"key": "value", "number": 42}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_data, f)
            temp_path = Path(f.name)
        
        try:
            result = safe_json_read(temp_path)
            self.assertEqual(result, test_data)
        finally:
            temp_path.unlink()
    
    def test_safe_json_read_nonexistent_file(self):
        """Test reading nonexistent file returns default"""
        nonexistent = Path("/tmp/nonexistent_file.json")
        result = safe_json_read(nonexistent, {"default": "value"})
        
        self.assertEqual(result, {"default": "value"})
    
    def test_safe_json_read_invalid_json(self):
        """Test reading invalid JSON returns default"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("invalid json content")
            temp_path = Path(f.name)
        
        try:
            result = safe_json_read(temp_path, {"default": True})
            self.assertEqual(result, {"default": True})
        finally:
            temp_path.unlink()
    
    def test_safe_json_write_success(self):
        """Test writing JSON file successfully"""
        test_data = {"test": "data", "numbers": [1, 2, 3]}
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "test.json"
            
            success = safe_json_write(temp_path, test_data)
            self.assertTrue(success)
            
            # Verify file was written correctly
            with open(temp_path, 'r') as f:
                written_data = json.load(f)
            
            self.assertEqual(written_data, test_data)
    
    def test_atomic_write_success(self):
        """Test atomic write operation"""
        test_data = {"atomic": "write", "test": True}
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "atomic_test.json"
            
            success = atomic_write(temp_path, test_data)
            self.assertTrue(success)
            self.assertTrue(temp_path.exists())
            
            # Verify no temporary file remains
            temp_files = list(Path(temp_dir).glob("*.tmp"))
            self.assertEqual(len(temp_files), 0)

if __name__ == '__main__':
    unittest.main()