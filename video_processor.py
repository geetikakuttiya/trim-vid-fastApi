"""
Core video-processing helpers used by the Streamlit UI.

Pipeline:
  1. download_video / download_audio  -> pull media from a YouTube (or any
     yt-dlp supported) URL.
  2. split_video                      -> cut the source video into fixed
     length chunks with ffmpeg.
  3. convert_to_reel                  -> for every chunk:
        - if it's already 9:16 (portrait) -> just stamp "Part N" on it.
        - if it's 16:9 (landscape)        -> build a blurred 1080x1920
          background with Pillow, drop the original clip on top of it
          (letterboxed / "pillarboxed" into a 9:16 canvas) and stamp
          "Part N" on it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageFilter

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CANVAS_W, CANVAS_H = 1080, 1920  # target 9:16 output size
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

ProgressCB = Optional[Callable[[str], None]]


def _notify(cb: ProgressCB, msg: str) -> None:
    if cb:
        cb(msg)


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int

    @property
    def is_portrait(self) -> bool:
        """True if the clip is already ~9:16 (or taller than it is wide)."""
        return self.height >= self.width


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: list[str]) -> None:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(cmd)}):\n{result.stderr.decode(errors='ignore')[-2000:]}"
        )


def get_video_info(path: Path) -> VideoInfo:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.decode(errors='ignore')}")
    data = json.loads(result.stdout.decode())
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    return VideoInfo(duration=duration, width=int(stream["width"]), height=int(stream["height"]))


# ---------------------------------------------------------------------------
# Step 1: Download
# ---------------------------------------------------------------------------

def download_video(
    url: str,
    out_dir: Path,
    cookiefile: Optional[Path] = None,
    progress_cb: ProgressCB = None,
) -> Path:
    """Download the best available video (+audio) for the given URL.

    YouTube periodically tightens bot-detection and rolls out new codecs
    (e.g. the IAMF/Opus audio track that breaks overly strict format
    strings). To stay resilient we try a few strategies in order instead of
    a single rigid format string, and support an optional cookies.txt for
    URLs that require sign-in.
    """
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    out_template = str(out_dir / f"{uid}_%(title).60s.%(ext)s")

    def hook(d):
        if d.get("status") == "downloading":
            pct = d.get("_percent_str", "").strip()
            _notify(progress_cb, f"Downloading video... {pct}")
        elif d.get("status") == "finished":
            _notify(progress_cb, "Download finished, merging streams...")

    base_opts = {
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "noplaylist": True,
    }
    if cookiefile is not None:
        base_opts["cookiefile"] = str(cookiefile)

    # Each strategy = (label, extra opts). Tried in order until one works.
    # The android/tv "player_client" trick is the current common workaround
    # for YouTube's "Sign in to confirm you're not a bot" / format errors.
    strategies = [
        ("android client", {"format": "bv*+ba/b", "extractor_args": {"youtube": {"player_client": ["android"]}}}),
        ("tv client", {"format": "bv*+ba/b", "extractor_args": {"youtube": {"player_client": ["tv"]}}}),
        ("web client", {"format": "bv*+ba/b", "extractor_args": {"youtube": {"player_client": ["web"]}}}),
        ("generic best", {"format": "best"}),
    ]

    last_error: Optional[Exception] = None
    for label, extra in strategies:
        opts = {**base_opts, **extra}
        try:
            _notify(progress_cb, f"Trying download strategy: {label}...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
            final_path = Path(filename).with_suffix(".mp4")
            if not final_path.exists():
                candidates = sorted(out_dir.glob(f"{uid}_*"), key=lambda p: p.stat().st_mtime)
                if candidates:
                    final_path = candidates[-1]
            if final_path.exists():
                return final_path
        except Exception as e:  # noqa: BLE001 - we deliberately try the next strategy
            last_error = e
            # clean up any partial files from the failed attempt before retrying
            for leftover in out_dir.glob(f"{uid}_*"):
                leftover.unlink(missing_ok=True)
            continue

    hint = (
        "All download strategies failed. YouTube may be requiring sign-in for this "
        "video/IP. Try exporting a cookies.txt from your browser (while logged into "
        "YouTube) and uploading it in the UI, or update yt-dlp: `pip install -U yt-dlp`."
    )
    raise RuntimeError(f"{hint}\n\nLast error: {last_error}")


def download_audio(
    url: str,
    out_dir: Path,
    cookiefile: Optional[Path] = None,
    progress_cb: ProgressCB = None,
) -> Path:
    """Download audio only, extracted as mp3."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    out_template = str(out_dir / f"{uid}_%(title).60s.%(ext)s")

    def hook(d):
        if d.get("status") == "downloading":
            pct = d.get("_percent_str", "").strip()
            _notify(progress_cb, f"Downloading audio... {pct}")
        elif d.get("status") == "finished":
            _notify(progress_cb, "Extracting audio (mp3)...")

    base_opts = {
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "noplaylist": True,
    }
    if cookiefile is not None:
        base_opts["cookiefile"] = str(cookiefile)

    strategies = [
        ("android client", {"format": "bestaudio/best", "extractor_args": {"youtube": {"player_client": ["android"]}}}),
        ("tv client", {"format": "bestaudio/best", "extractor_args": {"youtube": {"player_client": ["tv"]}}}),
        ("web client", {"format": "bestaudio/best", "extractor_args": {"youtube": {"player_client": ["web"]}}}),
        ("generic best", {"format": "best"}),
    ]

    last_error: Optional[Exception] = None
    for label, extra in strategies:
        opts = {**base_opts, **extra}
        try:
            _notify(progress_cb, f"Trying download strategy: {label}...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            candidates = sorted(out_dir.glob(f"{uid}_*.mp3"), key=lambda p: p.stat().st_mtime)
            if candidates:
                return candidates[-1]
        except Exception as e:  # noqa: BLE001 - try the next strategy
            last_error = e
            for leftover in out_dir.glob(f"{uid}_*"):
                leftover.unlink(missing_ok=True)
            continue

    hint = (
        "All download strategies failed. YouTube may be requiring sign-in for this "
        "video/IP. Try exporting a cookies.txt from your browser (while logged into "
        "YouTube) and uploading it in the UI, or update yt-dlp: `pip install -U yt-dlp`."
    )
    raise RuntimeError(f"{hint}\n\nLast error: {last_error}")


# ---------------------------------------------------------------------------
# Step 2: Split
# ---------------------------------------------------------------------------

def split_video(path: Path, segment_seconds: int, out_dir: Path, progress_cb: ProgressCB = None) -> list[Path]:
    """Split a video into fixed-length chunks.

    Re-encodes each chunk (instead of `-c copy`) so cut points are frame
    accurate. Stream copy only cuts cleanly on keyframes, which produces
    wildly wrong chunk lengths on footage with sparse/irregular keyframes
    (common with some yt-dlp downloads).
    """
    info = get_video_info(path)
    out_dir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    start = 0.0
    index = 1
    total_parts = max(1, int(-(-info.duration // segment_seconds)))  # ceil

    while start < info.duration - 0.05:
        duration = min(segment_seconds, info.duration - start)
        part_path = out_dir / f"raw_part{index}.mp4"
        _notify(progress_cb, f"Splitting chunk {index}/{total_parts}...")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(path),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k",
            "-avoid_negative_ts", "1",
            str(part_path),
        ]
        run_cmd(cmd)
        parts.append(part_path)
        start += segment_seconds
        index += 1

    return parts


# ---------------------------------------------------------------------------
# Step 3: Convert each chunk into a 9:16 "reel" with a Part N label
# ---------------------------------------------------------------------------

def _make_blurred_background(source_video: Path, tmp_dir: Path) -> Path:
    """Grab a mid-clip frame, cover-crop + blur it to CANVAS_W x CANVAS_H with Pillow."""
    frame_path = tmp_dir / f"frame_{uuid.uuid4().hex[:8]}.jpg"
    info = get_video_info(source_video)
    mid = max(0.0, info.duration / 2)
    run_cmd([
        "ffmpeg", "-y",
        "-ss", str(mid),
        "-i", str(source_video),
        "-frames:v", "1",
        "-q:v", "2",
        str(frame_path),
    ])

    img = Image.open(frame_path).convert("RGB")
    src_w, src_h = img.size

    # cover-crop so the frame fills the 9:16 canvas, then blur it
    target_ratio = CANVAS_W / CANVAS_H
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        new_h = src_h
        new_w = int(src_h * target_ratio)
    else:
        new_w = src_w
        new_h = int(src_w / target_ratio)
    left = (src_w - new_w) // 2
    top = (src_h - new_h) // 2
    img = img.crop((left, top, left + new_w, top + new_h))
    img = img.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(radius=30))
    # darken slightly so the white "Part N" label stays legible
    img = Image.eval(img, lambda px: int(px * 0.55))

    bg_path = tmp_dir / f"bg_{uuid.uuid4().hex[:8]}.jpg"
    img.save(bg_path, quality=90)
    frame_path.unlink(missing_ok=True)
    return bg_path


def _drawtext_filter(label: str, position: str = "top") -> str:
    text = label.replace("'", r"\'").replace(":", r"\:")
    y_expr = "h*0.06" if position == "top" else "h*0.90-text_h"
    return (
        f"drawtext=fontfile={FONT_PATH}:text='{text}':"
        f"fontcolor=white:fontsize=64:borderw=4:bordercolor=black@0.8:"
        f"x=(w-text_w)/2:y={y_expr}"
    )


def convert_to_reel(
    part_path: Path,
    part_index: int,
    out_dir: Path,
    text_position: str = "top",
    progress_cb: ProgressCB = None,
) -> Path:
    """Turn one raw chunk into a labelled 9:16 reel, ready for download."""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = get_video_info(part_path)
    label = f"Part {part_index}"
    out_path = out_dir / f"reel_part{part_index}.mp4"

    if info.is_portrait:
        _notify(progress_cb, f"Part {part_index}: already 9:16, adding label...")
        # Already vertical -> just stamp the label directly, no background needed
        vf = (
            f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},"
            f"{_drawtext_filter(label, text_position)}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(part_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(out_path),
        ]
        run_cmd(cmd)
    else:
        _notify(progress_cb, f"Part {part_index}: 16:9 detected, building 9:16 canvas with Pillow...")
        tmp_dir = out_dir / "_tmp_frames"
        tmp_dir.mkdir(exist_ok=True)
        bg_path = _make_blurred_background(part_path, tmp_dir)

        _notify(progress_cb, f"Part {part_index}: compositing clip onto vertical canvas...")
        # background image looped as a video track, original clip scaled to fit the
        # canvas width and overlaid centered on top of it, label stamped last.
        filter_complex = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}[bg];"
            f"[1:v]scale={CANVAS_W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[merged];"
            f"[merged]{_drawtext_filter(label, text_position)}[outv]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(bg_path),
            "-i", str(part_path),
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "1:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(out_path),
        ]
        run_cmd(cmd)
        bg_path.unlink(missing_ok=True)

    return out_path


def cleanup_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
