#!/usr/bin/env python3
import argparse, sys, os
from TTS.api import TTS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="Text to speak")
    ap.add_argument("--out", required=True, help="Output audio file (.wav/.mp3)")
    ap.add_argument("--lang", default="en", help="Language code (e.g., en)")
    # Accept both --speaker and legacy --voice; also support cloning with --speaker_wav
    ap.add_argument("--speaker", default=None, help="Built-in speaker name (e.g., Damien Black)")
    ap.add_argument("--voice", default=None, help="Alias for --speaker")
    ap.add_argument("--speaker_wav", default=None, help="Path to reference WAV for cloning")
    args = ap.parse_args()

    model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
    print(f" > Using model: xtts_v2", file=sys.stderr)
    
    try:
        tts = TTS(model_name)
        print(f" > Model loaded successfully", file=sys.stderr)
    except Exception as e:
        print(f" > ERROR: Failed to load model: {e}", file=sys.stderr)
        sys.exit(1)

    # Build kwargs for TTS
    kw = {
        "text": args.text,
        "file_path": args.out,
        "language": args.lang
    }

    # Determine speaker
    speaker = args.speaker or args.voice or os.environ.get("XTTS_SPEAKER", "Damien Black")
    
    print(f" > Text: '{args.text}'", file=sys.stderr)
    print(f" > Output: '{args.out}'", file=sys.stderr)
    print(f" > Language: '{args.lang}'", file=sys.stderr)
    print(f" > Speaker: '{speaker}'", file=sys.stderr)

    # Prefer cloning if a reference wav was provided
    if args.speaker_wav:
        print(f" > Using speaker cloning with: {args.speaker_wav}", file=sys.stderr)
        kw["speaker_wav"] = args.speaker_wav
    else:
        print(f" > Using built-in speaker: {speaker}", file=sys.stderr)
        kw["speaker"] = speaker

    # Synthesize
    try:
        print(f" > Starting synthesis...", file=sys.stderr)
        tts.tts_to_file(**kw)
        print(f" > Synthesis completed", file=sys.stderr)
        
        # Verify the file was created
        if os.path.exists(args.out):
            file_size = os.path.getsize(args.out)
            print(f" > Output file created: {args.out} ({file_size} bytes)", file=sys.stderr)
            
            if file_size == 0:
                print(f" > ERROR: Output file is empty", file=sys.stderr)
                sys.exit(1)
            
            # Output the file path to stdout for the calling script
            print(args.out)
        else:
            print(f" > ERROR: Output file was not created: {args.out}", file=sys.stderr)
            sys.exit(1)
            
    except Exception as e:
        print(f" > ERROR: Synthesis failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()