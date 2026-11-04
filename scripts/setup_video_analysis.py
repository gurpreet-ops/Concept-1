"""One-time setup for Meta video creative analysis.

Usage (from the Concept-1 folder):
    brew install ffmpeg whisper-cpp
    .venv/bin/python scripts/setup_video_analysis.py

Checks that ffmpeg and whisper are installed and downloads the whisper speech model
(about 470 MB for the default "small" model) into Concept-1/data/models.
"""

from __future__ import annotations

import sys

import httpx

from ads_mcp.meta.video import MODEL_URL, WHISPER_BINARIES, find_binary, whisper_model_path


def main() -> None:
    missing = [name for name, found in (
        ("ffmpeg", find_binary("ffmpeg")),
        ("whisper-cpp", find_binary(*WHISPER_BINARIES)),
    ) if not found]
    if missing:
        sys.exit(f"Missing: {', '.join(missing)}. Install with:\n\n    brew install ffmpeg whisper-cpp\n\n"
                 "then run this script again. No Homebrew? Get it from https://brew.sh first.")
    print("  ✓ ffmpeg and whisper are installed")

    model = whisper_model_path()
    if model.exists():
        print(f"  ✓ Speech model already downloaded ({model.name})")
    else:
        model.parent.mkdir(parents=True, exist_ok=True)
        url = MODEL_URL.format(name=model.stem.removeprefix("ggml-"))
        print(f"  Downloading speech model {model.name} (a few hundred MB, one time only)...")
        partial = model.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with partial.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r  {done * 100 // total}%", end="", flush=True)
        partial.rename(model)
        print(f"\n  ✓ Speech model saved to {model}")

    print("\nDone. Quit Claude (Cmd+Q), reopen it, and ask:\n"
          "  \"Analyse my Meta video creatives for <account> by category and hook\"")


if __name__ == "__main__":
    main()
