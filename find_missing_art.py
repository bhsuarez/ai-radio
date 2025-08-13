#!/usr/bin/env python3
import os
from mutagen import File as MFile
from mutagen.id3 import APIC
from mutagen.flac import FLAC

MEDIA_ROOT = "/mnt/music"
missing_art = []

def has_embedded_cover(path):
    try:
        audio = MFile(path)
        if not audio:
            return False
        # MP3 ID3
        if getattr(audio, "tags", None):
            if any(isinstance(v, APIC) for v in audio.tags.values()):
                return True
        # FLAC
        if isinstance(audio, FLAC) and audio.pictures:
            return True
        # MP4/M4A (covr tag)
        if getattr(audio, "tags", None):
            covr = audio.tags.get("covr") or audio.tags.get("----:com.apple.iTunes:cover")
            if covr:
                return True
    except Exception:
        pass
    return False

def has_folder_art(path):
    folder = os.path.dirname(path)
    for name in ("cover.jpg", "folder.jpg", "front.jpg", "AlbumArtSmall.jpg"):
        if os.path.exists(os.path.join(folder, name)):
            return True
    return False

for root, dirs, files in os.walk(MEDIA_ROOT):
    for f in files:
        ext = f.lower().split(".")[-1]
        if ext in ("mp3", "flac", "m4a", "mp4", "ogg"):
            full_path = os.path.join(root, f)
            if not has_embedded_cover(full_path) and not has_folder_art(full_path):
                missing_art.append(full_path)

# Output missing art list
if missing_art:
    with open("missing_album_art.txt", "w") as out:
        for path in missing_art:
            out.write(path + "\n")
    print(f"Found {len(missing_art)} tracks missing artwork.")
    print("List saved to missing_album_art.txt")
else:
    print("All tracks have artwork.")