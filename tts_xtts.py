#!/usr/bin/env python3
import argparse, os
from pathlib import Path
from TTS.api import TTS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--voice", default="")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    Path(Path(args.out).parent).mkdir(parents=True, exist_ok=True)

    model = "tts_models/multilingual/multi-dataset/xtts_v2"
    tts = TTS(model)

    kw = dict(text=args.text, file_path=args.out, language=args.lang, split_sentences=True)
    if args.voice and os.path.exists(args.voice):
        kw["speaker_wav"] = args.voice

    tts.tts_to_file(**kw)
    print(args.out)

if __name__ == "__main__":
    main()
