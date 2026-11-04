"""Turn a video into what Claude can review: frame contact sheets and a timed transcript.

Uses two free command-line tools, installed with Homebrew on a Mac:
    brew install ffmpeg whisper-cpp
plus a whisper model file, downloaded by scripts/setup_video_analysis.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from ads_mcp.meta.creative_store import data_dir

# The Claude desktop app starts servers with a minimal PATH that misses Homebrew.
EXTRA_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"]
WHISPER_BINARIES = ["whisper-cli", "whisper-cpp"]
DEFAULT_WHISPER_MODEL = "small"  # multilingual: handles Hindi, English and Hinglish
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"

HOOK_SECONDS = 3.0
HOOK_FRAMES = 8  # frames spread over the first HOOK_SECONDS
BODY_FRAMES = 8  # frames spread over the rest of the video
FRAME_WIDTH = 360


class VideoToolsMissing(RuntimeError):
    pass


def find_binary(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for directory in EXTRA_BIN_DIRS:
            candidate = Path(directory) / name
            if candidate.exists():
                return str(candidate)
    return None


def whisper_model_path() -> Path:
    name = os.environ.get("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
    return data_dir() / "models" / f"ggml-{name}.bin"


def tool_status() -> dict:
    model = whisper_model_path()
    return {
        "ffmpeg": find_binary("ffmpeg"),
        "ffprobe": find_binary("ffprobe"),
        "whisper": find_binary(*WHISPER_BINARIES),
        "whisper_model": str(model) if model.exists() else None,
    }


def require_tools(need_whisper: bool = True) -> dict:
    status = tool_status()
    missing = [k for k in ("ffmpeg", "ffprobe") if not status[k]]
    if need_whisper:
        missing += [k for k in ("whisper", "whisper_model") if not status[k]]
    if missing:
        raise VideoToolsMissing(
            f"Video analysis tools missing: {', '.join(missing)}. On your Mac run "
            "`brew install ffmpeg whisper-cpp`, then `.venv/bin/python scripts/setup_video_analysis.py` "
            "in the Concept-1 folder, and restart Claude."
        )
    return status


@dataclass
class VideoReview:
    duration: float
    hook_sheet: Path  # HOOK_FRAMES frames from the first HOOK_SECONDS, left-to-right, top-to-bottom
    body_sheet: Path | None  # BODY_FRAMES frames across the rest of the video
    hook_times: list[float]
    body_times: list[float]
    transcript: list[dict] | None  # [{"start": s, "end": s, "text": "..."}]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr.strip()[-500:]}")
    return result


async def download(url: str, dest: Path) -> None:
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as http:
        async with http.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)


def probe_duration(ffprobe: str, video: Path) -> float:
    out = _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)])
    return float(json.loads(out.stdout)["format"]["duration"])


def frame_times(duration: float) -> tuple[list[float], list[float]]:
    hook_end = min(HOOK_SECONDS, duration)
    hook = [round(hook_end * i / HOOK_FRAMES, 2) for i in range(HOOK_FRAMES)]
    body: list[float] = []
    if duration > HOOK_SECONDS + 1:
        span = duration - HOOK_SECONDS
        body = [round(HOOK_SECONDS + span * (i + 0.5) / BODY_FRAMES, 2) for i in range(BODY_FRAMES)]
    return hook, body


def contact_sheet(ffmpeg: str, video: Path, times: list[float], out: Path, workdir: Path) -> None:
    """Grab one frame per timestamp and tile them 4 per row into a single JPEG."""
    frames = []
    for i, t in enumerate(times):
        frame = workdir / f"{out.stem}_{i:02d}.jpg"
        _run([ffmpeg, "-y", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
              "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "4", str(frame)])
        frames.append(frame)
    rows = (len(frames) + 3) // 4
    _run([ffmpeg, "-y", "-v", "error", "-framerate", "1", "-i", str(workdir / f"{out.stem}_%02d.jpg"),
          "-vf", f"tile=4x{rows}:padding=4:color=white", "-frames:v", "1", "-q:v", "4", str(out)])


def transcribe(ffmpeg: str, whisper: str, model: Path, video: Path, workdir: Path) -> list[dict]:
    audio = workdir / "audio.wav"
    try:
        _run([ffmpeg, "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    except RuntimeError:
        return []  # no audio track
    base = workdir / "transcript"
    _run([whisper, "-m", str(model), "-f", str(audio), "-l", "auto", "-oj", "-of", str(base), "-np"])
    data = json.loads((workdir / "transcript.json").read_text())
    return [
        {
            "start": seg["offsets"]["from"] / 1000,
            "end": seg["offsets"]["to"] / 1000,
            "text": seg["text"].strip(),
        }
        for seg in data.get("transcription", [])
        if seg.get("text", "").strip()
    ]


async def review_video(source_url: str, out_dir: Path, with_transcript: bool = True) -> VideoReview:
    """Download a video, build hook/body contact sheets and (optionally) a transcript."""
    tools = require_tools(need_whisper=with_transcript)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        video = workdir / "video.mp4"
        await download(source_url, video)
        duration = probe_duration(tools["ffprobe"], video)
        hook_times, body_times = frame_times(duration)
        hook_sheet = out_dir / "hook.jpg"
        contact_sheet(tools["ffmpeg"], video, hook_times, hook_sheet, workdir)
        body_sheet = None
        if body_times:
            body_sheet = out_dir / "body.jpg"
            contact_sheet(tools["ffmpeg"], video, body_times, body_sheet, workdir)
        transcript = None
        if with_transcript:
            transcript = transcribe(tools["ffmpeg"], tools["whisper"], Path(tools["whisper_model"]), video, workdir)
    return VideoReview(duration, hook_sheet, body_sheet, hook_times, body_times, transcript)
