#!/usr/bin/env python3
import argparse, sys, os
from TTS.api import TTS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="Text to speak")
    ap.add_argument("--out", required=True, help="Output audio file (.wav/.mp3)")
    ap.add_argument("--lang", default="en", help="Language code (e.g., en)")
    # Accept both --speaker and legacy --voice; also support cloning with --speaker_wav
    ap.add_argument("--speaker", default=None, help="Built-in speaker name (e.g., en_female_5)")
    ap.add_argument("--voice", default=None, help="Alias for --speaker")
    ap.add_argument("--speaker_wav", default=None, help="Path to reference WAV for cloning")
    args = ap.parse_args()

    model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
    print(f" > Using model: xtts", file=sys.stderr)
    tts = TTS(model_name)

    # Build kwargs for TTS
    kw = {
        "text": args.text,
        "file_path": args.out,
        "language": args.lang
    }

    # Prefer cloning if a reference wav was provided
    speaker = args.speaker or args.voice
    if args.speaker_wav:
        kw["speaker_wav"] = args.speaker_wav
    else:
        kw["speaker"] = speaker or os.environ.get("XTTS_SPEAKER", "Claribel Dervla")

    # Synthesize
    tts.tts_to_file(**kw)
    print(args.out)

if __name__ == "__main__":
    main()
