"""ffmpeg / ffprobe wrappers and media probing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

__all__ = [
    "MediaInfo",
    "StreamInfo",
    "FfmpegError",
    "ffmpeg_bin",
    "ffprobe_bin",
    "run",
    "probe",
    "keyframes",
    "first_audio_stream",
    "ensure_tools",
]


class FfmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        super().__init__(f"command failed ({returncode}): {' '.join(cmd[:6])} ...\n{tail}")


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE", "ffprobe")


def ensure_tools() -> None:
    """Fail fast with an actionable message when ffmpeg is missing."""
    missing = [
        name
        for name, path in (("ffmpeg", ffmpeg_bin()), ("ffprobe", ffprobe_bin()))
        if not shutil.which(path)
    ]
    if missing:
        raise SystemExit(
            "missing required tools: " + ", ".join(missing) + "\n"
            "install them first, e.g.:\n"
            "  macOS        brew install ffmpeg\n"
            "  Ubuntu       sudo apt install ffmpeg\n"
            "  Windows      winget install Gyan.FFmpeg"
        )


def run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - command list is built from trusted argv
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FfmpegError(cmd, result.returncode, result.stderr or "")
    return result


@dataclass(frozen=True)
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    pix_fmt: str | None = None
    bit_rate: int | None = None
    channels: int | None = None
    sample_rate: int | None = None
    duration: float | None = None
    start_time: float | None = None


@dataclass
class MediaInfo:
    path: Path
    duration: float
    size: int
    format_name: str
    streams: list[StreamInfo] = field(default_factory=list)
    created_at: datetime | None = None
    created_at_raw: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    rotation: int = 0
    hdr: bool = False

    @property
    def video(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.codec_type == "video"), None)

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_streams)

    @property
    def display_width(self) -> int:
        """Width after the rotation metadata is applied."""
        video = self.video
        if video is None:
            return 0
        if abs(self.rotation) in (90, 270):
            return video.height or 0
        return video.width or 0

    @property
    def display_height(self) -> int:
        video = self.video
        if video is None:
            return 0
        if abs(self.rotation) in (90, 270):
            return video.width or 0
        return video.height or 0

    @property
    def bit_depth(self) -> int:
        pix_fmt = (self.video.pix_fmt if self.video else "") or ""
        if "10le" in pix_fmt or "10be" in pix_fmt:
            return 10
        if "12le" in pix_fmt or "12be" in pix_fmt:
            return 12
        return 8


def _parse_float(value: str | None) -> float | None:
    if not value or value in {"N/A", "unknown"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_fps(value: str | None) -> float | None:
    if not value:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else None
        except ValueError:
            return None
    return _parse_float(value)


def _iso_datetime(raw: str) -> datetime | None:
    """Parse QuickTime creation_time, which may be UTC (``...Z``) or offset-aware."""
    text = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def probe(path: str | Path) -> MediaInfo:
    """Probe a media file into a :class:`MediaInfo`."""
    media_path = Path(path)
    if not media_path.exists():
        raise SystemExit(f"file not found: {media_path}")
    result = run(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(media_path),
        ]
    )
    payload = json.loads(result.stdout or "{}")
    fmt = payload.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}

    streams: list[StreamInfo] = []
    rotation = 0
    for raw in payload.get("streams", []):
        for side in raw.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
        streams.append(
            StreamInfo(
                index=int(raw.get("index", -1)),
                codec_type=raw.get("codec_type", "unknown"),
                codec_name=raw.get("codec_name", "unknown"),
                width=raw.get("width"),
                height=raw.get("height"),
                fps=_parse_fps(raw.get("avg_frame_rate")) or _parse_fps(raw.get("r_frame_rate")),
                pix_fmt=raw.get("pix_fmt"),
                bit_rate=int(raw["bit_rate"]) if raw.get("bit_rate") else None,
                channels=raw.get("channels"),
                sample_rate=int(raw["sample_rate"]) if raw.get("sample_rate") else None,
                duration=_parse_float(raw.get("duration")),
                start_time=_parse_float(raw.get("start_time")),
            )
        )

    created_raw = tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate")
    created = _iso_datetime(created_raw) if created_raw else None
    if created is None:
        # fall back to the recording date written by some cameras: creationdate "2026-10-24T17:16:54+0800"
        alt = tags.get("com.apple.quicktime.creationdate")
        if alt and alt != created_raw:
            created = _iso_datetime(alt)

    video = next((s for s in streams if s.codec_type == "video"), None)
    transfer = None
    if video is not None:
        result_v = run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_primaries,color_transfer,color_space",
                "-of",
                "json",
                str(media_path),
            ]
        )
        colours = (json.loads(result_v.stdout or "{}").get("streams") or [{}])[0]
        color_primaries = colours.get("color_primaries")
        transfer = colours.get("color_transfer")
        color_space = colours.get("color_space")
    else:
        color_primaries = transfer = color_space = None

    return MediaInfo(
        path=media_path,
        duration=_parse_float(fmt.get("duration")) or 0.0,
        size=int(fmt.get("size") or media_path.stat().st_size),
        format_name=fmt.get("format_name", ""),
        streams=streams,
        created_at=created,
        created_at_raw=created_raw,
        color_primaries=color_primaries,
        color_transfer=transfer,
        color_space=color_space,
        rotation=rotation,
        hdr=transfer in {"arib-std-b67", "smpte2084", "smpte428", "linear"},
    )


def keyframes(path: str | Path, *, interval: tuple[float, float] | None = None) -> list[float]:
    """Return presentation timestamps of key frames (IRAP frames).

    ``interval`` limits the scan to ``(start, end)`` seconds which is much faster for
    long files, but callers that align many segments should scan once and pass the
    full list around.
    """
    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_entries",
        "frame=pts_time",
        "-of",
        "csv=p=0",
    ]
    if interval is not None:
        start, end = interval
        cmd += ["-read_intervals", f"{start:.3f}%+{max(end - start, 0.1):.3f}"]
    cmd.append(str(path))
    result = run(cmd)
    times: list[float] = []
    for line in result.stdout.splitlines():
        token = line.split(",")[0].strip()
        if not token:
            continue
        try:
            times.append(float(token))
        except ValueError:
            continue
    times.sort()
    return times


def first_audio_stream(info: MediaInfo) -> StreamInfo | None:
    """Pick the most useful audio track.

    iPhone/MOV files can carry an extra 4-channel spatial audio track that ffmpeg
    cannot even decode; prefer a plain stereo AAC/Opus track.
    """
    candidates = info.audio_streams
    if not candidates:
        return None
    for stream in candidates:
        if stream.channels == 2 and stream.codec_name in {
            "aac",
            "opus",
            "vorbis",
            "mp3",
            "flac",
            "pcm_s16le",
            "pcm_s24le",
        }:
            return stream
    for stream in candidates:
        if stream.codec_name != "unknown" and stream.codec_name != "none":
            return stream
    return candidates[0]
