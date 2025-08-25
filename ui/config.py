"""
Configuration management for AI Radio Flask application
"""
import os
from pathlib import Path

class Config:
    # Server Configuration
    HOST = "0.0.0.0"
    PORT = 5055
    SECRET_KEY = 'ai-radio-socketio-secret'
    
    # Liquidsoap Configuration
    LS_HOST = os.environ.get("QUEUE_HOST", "127.0.0.1")
    LS_PORT = int(os.environ.get("QUEUE_PORT", "1234"))
    TELNET_HOST = "127.0.0.1"
    TELNET_PORT = 1234
    
    # Paths
    BASE_DIR = Path(__file__).parent.absolute()
    ROOT_DIR = Path("/opt/ai-radio")
    
    # Music and Media
    MUSIC_ROOTS = ["/mnt/music", "/mnt/music/media", "/mnt/music/Music"]
    
    # File Paths
    NOW_JSON = ROOT_DIR / "cache" / "now_metadata.json"
    NOW_TXT = ROOT_DIR / "nowplaying.txt"
    NEXT_JSON = ROOT_DIR / "next.json"
    HISTORY_FILE = ROOT_DIR / "play_history.json"
    
    # TTS Configuration
    TTS_DIR = ROOT_DIR / "tts_queue"
    TTS_FALLBACK_DIR = ROOT_DIR / "tts"
    VOICE_PATH = "/mnt/music/ai-dj/piper_voices/en/en_US/norman/medium/en_US-norman-medium.onnx"
    
    @property
    def tts_root(self):
        return self.TTS_DIR if self.TTS_DIR.exists() else self.TTS_FALLBACK_DIR
    
    # Logging
    LOG_DIR = ROOT_DIR / "logs"
    DJ_LOG = LOG_DIR / "dj-now.log"
    
    # Cache
    COVER_CACHE = ROOT_DIR / "cache" / "covers"
    
    # History Settings
    MAX_HISTORY = 300
    DEDUP_WINDOW_MS = 60_000
    
    # DJ Generation
    DJ_GENERATION_COOLDOWN = 60
    
    # Icecast
    ICECAST_STATUS = "http://icecast.zorro.network:8000/status-json.xsl"
    MOUNT = "/stream.mp3"
    
    def __init__(self):
        # Create necessary directories
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.COVER_CACHE.mkdir(parents=True, exist_ok=True)

# Global config instance
config = Config()