"""Keyframe-accurate, lossless clip extraction.

Why keyframes matter
--------------------
``-c copy`` cannot re-encode, so a cut can only *start* on a key frame (IRAP/IDR)
and can only *end* cleanly on one: ending mid-GOP leaves the decoder without the
reference frames for the tail and players show broken/green frames. GOPs on an
iPhone are ~1 s, so snapping to keyframes costs well under a second of padding and
keeps the video bit-exact (HDR, 10-bit, rotation metadata and all).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .highlights import Segment
from .media import MediaInfo, StreamInfo, ffmpeg_bin, first_audio_stream, keyframes, run
from .util import format_duration, format_timecode, warn

__all__ = ["ClipPlan", "plan_clips", "align", "cut_clip", "cut_clips", "cache_path"]


@dataclass
class ClipPlan:
    """A highlight plus the keyframe-aligned range that will actually be cut."""

    index: int
    segment: Segment
    start: float
    end: float
    notes: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def lead(self) -> float:
        return self.segment.start - self.start

    @property
    def tail(self) -> float:
        return self.end - self.segment.end

    @property
    def filename(self) -> str:
        hits = (
            f"{self.segment.hits}hits"
            if self.segment.hits is not None
            else _slug(self.segment.kind)
        )
        start = format_timecode(self.segment.start, compact=True)
        end = format_timecode(self.segment.end, compact=True)
        return f"{self.index:02d}_{hits}_{start}-{end}.mp4"

    def describe(self) -> str:
        return (
            f"{self.filename:<34} source {format_timecode(self.start)}-{format_timecode(self.end)}"
            f" | pad -{self.lead:.2f}s/+{self.tail:.2f}s | {format_duration(self.duration)}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "segment": self.segment.to_dict(),
            "cut_start": round(self.start, 3),
            "cut_end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "lead": round(self.lead, 3),
            "tail": round(self.tail, 3),
            "path": str(self.path) if self.path else None,
            "notes": self.notes,
        }


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "rally"


def align(
    segment: Segment,
    kfs: list[float],
    *,
    lead: float = 1.0,
    tail: float = 1.5,
    duration: float | None = None,
) -> tuple[float, float, list[str]]:
    """Snap ``segment`` outward to key frames so a lossless copy cut is clean.

    ``lead`` seconds are added before the detected start and ``tail`` seconds after
    the detected end, then the range is widened to the nearest key frames.
    Returns ``(start, end, notes)``.
    """
    if not kfs:
        start = max(segment.start - lead, 0.0)
        end = segment.end + tail
        if duration is not None:
            end = min(end, duration)
        return start, end, ["no keyframe list available, cut is not keyframe aligned"]

    notes: list[str] = []
    start_target = max(segment.start - lead, 0.0)
    before = [k for k in kfs if k <= start_target + 1e-3]
    if before:
        start = max(before)
    else:
        start = max(start_target, min(kfs))
        notes.append("no earlier keyframe, started at the first keyframe")

    end_target = segment.end + tail
    after = [k for k in kfs if k >= end_target - 1e-3]
    if after:
        end = min(after)
    else:
        end = end_target
        if duration is not None:
            end = min(end, duration)
        notes.append("no later keyframe, kept the requested end (tail may not decode cleanly)")

    if duration is not None:
        end = min(end, duration)
    if end <= start:
        raise ValueError(f"segment {segment.describe()} collapsed after alignment")
    return start, end, notes


def plan_clips(
    info: MediaInfo,
    segments: list[Segment],
    *,
    lead: float = 1.0,
    tail: float = 1.5,
    keyframe_list: list[float] | None = None,
    first_index: int = 1,
) -> list[ClipPlan]:
    """Build cut plans for the given segments (ordered as supplied)."""
    kfs = keyframe_list if keyframe_list is not None else keyframes(info.path)
    plans: list[ClipPlan] = []
    for offset, segment in enumerate(segments):
        start, end, notes = align(segment, kfs, lead=lead, tail=tail, duration=info.duration)
        plans.append(
            ClipPlan(index=first_index + offset, segment=segment, start=start, end=end, notes=notes)
        )
    return plans


def _map_args(info: MediaInfo, audio: StreamInfo | None) -> list[str]:
    video = info.video
    if video is None:
        raise SystemExit(f"no video stream in {info.path}")
    args = ["-map", f"0:{video.index}"]
    if audio is not None:
        args += ["-map", f"0:{audio.index}"]
    return args


def cut_clip(
    info: MediaInfo,
    plan: ClipPlan,
    outdir: str | Path,
    *,
    audio: StreamInfo | None = None,
    overwrite: bool = True,
) -> ClipPlan:
    """Cut one plan with stream copy (bit exact) and return it with ``path`` set."""
    directory = Path(outdir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / plan.filename
    if target.exists() and not overwrite:
        plan.path = target
        return plan

    audio_stream = audio if audio is not None else first_audio_stream(info)
    cmd = [
        ffmpeg_bin(),
        "-y" if overwrite else "-n",
        "-v",
        "error",
        "-ss",
        f"{plan.start:.6f}",
        "-i",
        str(info.path),
        "-t",
        f"{plan.duration:.6f}",
        *_map_args(info, audio_stream),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(target),
    ]
    run(cmd)
    plan.path = target
    return plan


def cut_clips(
    info: MediaInfo,
    plans: list[ClipPlan],
    outdir: str | Path,
    *,
    audio: StreamInfo | None = None,
    overwrite: bool = True,
) -> list[ClipPlan]:
    audio_stream = audio if audio is not None else first_audio_stream(info)
    if info.audio_streams and audio_stream is None:
        warn("source has audio streams but none was selectable; clips will be silent")
    for plan in plans:
        cut_clip(info, plan, outdir, audio=audio_stream, overwrite=overwrite)
    return plans


def cache_path(clips_dir: str | Path) -> Path:
    """Where the keyframe list cache lives for a clips directory."""
    return Path(clips_dir) / "keyframes.json"
