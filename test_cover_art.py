#!/usr/bin/env python3
"""
Test script to check album art extraction from your music files.
Run this to see what's happening with your MusicBrainz tagged files.
"""

import os
import sys
from pathlib import Path

def test_file_artwork(file_path):
    """Test artwork extraction for a specific file."""
    print(f"\n=== Testing: {file_path} ===")
    
    if not os.path.exists(file_path):
        print("❌ File does not exist!")
        return
    
    try:
        from mutagen import File as MFile
        from mutagen.id3 import APIC
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4Cover
        
        audio = MFile(file_path)
        
        if not audio:
            print("❌ Could not read audio file")
            return
        
        print(f"✅ File type: {type(audio).__name__}")
        print(f"✅ File info: {audio.info}")
        
        if hasattr(audio, 'tags') and audio.tags:
            print(f"✅ Has tags: {len(audio.tags)} tag(s)")
            
            # Print all tag keys for debugging
            print("📋 Tag keys:", list(audio.tags.keys())[:10])  # First 10 keys
            
            # Look for artwork
            artwork_found = False
            
            # Check for APIC frames (MP3)
            for key, value in audio.tags.items():
                if isinstance(value, APIC):
                    print(f"🎨 Found APIC artwork: {key}")
                    print(f"   Type: {value.type}")
                    print(f"   Description: {value.desc}")
                    print(f"   MIME: {value.mime}")
                    print(f"   Size: {len(value.data)} bytes")
                    artwork_found = True
            
            # Check for MP4 cover art
            if 'covr' in audio.tags:
                covers = audio.tags['covr']
                print(f"🎨 Found MP4 cover art: {len(covers)} image(s)")
                for i, cover in enumerate(covers):
                    print(f"   Cover {i+1}: {len(bytes(cover))} bytes")
                artwork_found = True
        
        # Check FLAC pictures
        if isinstance(audio, FLAC) and audio.pictures:
            print(f"🎨 Found FLAC pictures: {len(audio.pictures)}")
            for i, pic in enumerate(audio.pictures):
                print(f"   Picture {i+1}: {pic.desc}, {len(pic.data)} bytes")
            artwork_found = True
        
        if not artwork_found:
            print("❌ No embedded artwork found")
            
            # Check for folder art
            folder = os.path.dirname(file_path)
            art_files = []
            for art_name in ["cover.jpg", "folder.jpg", "front.jpg", "album.jpg"]:
                art_path = os.path.join(folder, art_name)
                if os.path.exists(art_path):
                    art_files.append(art_name)
            
            if art_files:
                print(f"📁 Found folder art: {art_files}")
            else:
                print("❌ No folder art found either")
        
    except ImportError:
        print("❌ Mutagen library not installed!")
        print("Install with: pip install mutagen")
    except Exception as e:
        print(f"❌ Error: {e}")

def scan_music_directory(music_dir, max_files=5):
    """Scan a music directory and test the first few files."""
    print(f"\n🔍 Scanning music directory: {music_dir}")
    
    if not os.path.exists(music_dir):
        print("❌ Music directory does not exist!")
        return
    
    audio_extensions = {'.mp3', '.flac', '.m4a', '.mp4', '.ogg', '.opus'}
    files_tested = 0
    
    for root, dirs, files in os.walk(music_dir):
        for file in files:
            if Path(file).suffix.lower() in audio_extensions:
                if files_tested >= max_files:
                    break
                
                file_path = os.path.join(root, file)
                test_file_artwork(file_path)
                files_tested += 1
        
        if files_tested >= max_files:
            break
    
    if files_tested == 0:
        print("❌ No audio files found!")

if __name__ == "__main__":
    print("🎵 Album Art Test Script")
    print("=" * 50)
    
    # Test a specific file if provided
    if len(sys.argv) > 1:
        test_file_artwork(sys.argv[1])
    else:
        # Scan common music directories
        music_dirs = [
            "/mnt/music",  # Your music directory
            "/opt/ai-radio/music",
            "/home/music",
            "~/Music"
        ]
        
        for music_dir in music_dirs:
            expanded_dir = os.path.expanduser(music_dir)
            if os.path.exists(expanded_dir):
                scan_music_directory(expanded_dir, max_files=3)
                break
        else:
            print("❌ No music directories found!")
            print("Usage: python test_cover_art.py /path/to/music/file.mp3")
            print("   or: python test_cover_art.py")