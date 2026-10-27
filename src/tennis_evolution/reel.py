"""Reel assembly.

Hard-won lesson
---------------
The obvious approach -- ``ffmpeg -f concat -c copy`` over the lossless clips -- is
broken for this workflow:

* every clip starts on a key frame but its audio starts on an audio packet
  boundary, so each join reintroduces a few hundred milliseconds of drift and
  ffmpeg ends up shifting DTS ("Non-monotonic DTS" spam);
* the resulting file can start with a ~1 s video offset, i.e. permanent A/V
  desync, and HEVC reference frames across segment borders break ("Could not find
  ref with POC"), which shows up as corrupted frames right after a cut.

So the reel is produced from the *source* video in a single pass: trim each
segment, optionally overlay its label, concatenate, encode once. Video is re-encoded
(x265 CRF 19 by default) but audio/video stay sample-accurate and there are no
broken GOPs. ``concat_lossless`` is kept as an escape hatch and reports the problems
it finds instead of hiding them.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from .cut import ClipPlan
from .media import MediaInfo, StreamInfo, ffmpeg_bin, first_audio_stream, probe, run, run_stream
from .util import note, warn

__all__ = ["render_reel", "concat_lossless", "ReelReport", "progress_sink"]

_COLOR_OK = {"bt709", "bt2020", "smpte2084", "arib-std-b67", "smpte170m", "reserved", "unknown"}


class ReelReport(dict):
    """Simple dict subclass so JSON output stays readable."""


def _format_for(info: MediaInfo, codec: str) -> str:
    if codec == "x264":
        return "yuv420p"
    return "yuv420p10le" if info.bit_depth >= 10 else "yuv420p"


def _colour_args(info: MediaInfo) -> list[str]:
    args: list[str] = []
    mapping = (
        ("-color_primaries", info.color_primaries),
        ("-color_trc", info.color_transfer),
        ("-colorspace", info.color_space),
    )
    for flag, value in mapping:
        if value and value not in {"unknown", "reserved"}:
            args += [flag, value]
    return args


def _encoder_args(codec: str, crf: float, preset: str, pix_fmt: str) -> list[str]:
    if codec == "x264":
        return [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            f"{crf:g}",
            "-tag:v",
            "avc1",
            "-pix_fmt",
            pix_fmt,
        ]
    return [
        "-c:v",
        "libx265",
        "-preset",
        preset,
        "-crf",
        f"{crf:g}",
        "-tag:v",
        "hvc1",
        "-x265-params",
        "log-level=error",
        "-pix_fmt",
        pix_fmt,
    ]


def _build_filter(
    plans: list[ClipPlan], overlays: list[Path | None], pix_fmt: str
) -> tuple[str, str, str]:
    """Return (filter_complex, video_label, audio_label)."""
    count = len(plans)
    # ffmpeg input order: [0..count-1] = source trims, then one input per overlay
    overlay_input_index: list[int | None] = []
    next_input = count
    for overlay in overlays:
        if overlay is None:
            overlay_input_index.append(None)
        else:
            overlay_input_index.append(next_input)
            next_input += 1
    chains: list[str] = []
    concat_inputs: list[str] = []
    for i in range(count):
        chains.append(f"[{i}:v]setpts=PTS-STARTPTS[s{i}]")
        overlay_index = overlay_input_index[i]
        if overlay_index is not None:
            chains.append(f"[s{i}][{overlay_index}:v]overlay=0:0:shortest=0,format={pix_fmt}[v{i}]")
        else:
            chains.append(f"[s{i}]format={pix_fmt}[v{i}]")
        chains.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
        concat_inputs.extend([f"[v{i}]", f"[a{i}]"])
    if count == 1:
        return ";".join(chains), "[v0]", "[a0]"
    chain = ";".join(chains)
    chain += ";" + "".join(concat_inputs) + f"concat=n={count}:v=1:a=1[v][a]"
    return chain, "[v]", "[a]"


def progress_sink(total_seconds: float, callback: Callable[[float], None]) -> Callable[[str], None]:
    """Turn ``ffmpeg -progress pipe:1`` key=value lines into a 0..1 callback.

    ffmpeg reports both ``out_time_us`` and ``out_time_ms``; the latter is in
    microseconds too (long standing ffmpeg quirk), so both are divided by 1e6.
    """
    total = total_seconds if total_seconds > 0 else 1.0

    def sink(line: str) -> None:
        if line.startswith("progress="):
            if line.endswith("end"):
                callback(1.0)
            return
        if not line.startswith(("out_time_us=", "out_time_ms=")):
            return
        raw = line.partition("=")[2].strip()
        if not raw.isdigit():
            return
        callback(min(1.0, (int(raw) / 1_000_000) / total))

    return sink


def render_reel(
    info: MediaInfo,
    plans: list[ClipPlan],
    out: str | Path,
    *,
    overlays: list[Path | None] | None = None,
    crf: float = 19,
    preset: str = "fast",
    codec: str = "hevc",
    audio: StreamInfo | None = None,
    keep_rotation: bool = False,
    dry_run: bool = False,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Render the reel from the source video in one pass.

    ``overlays`` is an optional list (same length as ``plans``) of transparent PNGs
    to burn onto each segment, which keeps labels scoped to their own rally.
    ``progress`` receives a 0..1 fraction driven by ``ffmpeg -progress`` (used by the
    web UI) and ``cancelled`` is polled between progress lines so a long encode can
    be aborted. When both are omitted the command is exactly the CLI's.
    """
    if not plans:
        raise SystemExit("nothing to render: empty plan list")
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    audio_stream = audio if audio is not None else first_audio_stream(info)
    pix_fmt = _format_for(info, codec)

    overlay_list: list[Path | None] = (
        list(overlays) if overlays is not None else [None] * len(plans)
    )
    if len(overlay_list) != len(plans):
        raise ValueError("overlays must have the same length as plans")

    cmd: list[str] = [ffmpeg_bin(), "-y", "-v", "warning"]
    if progress is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    for plan in plans:
        cmd += ["-ss", f"{plan.start:.6f}", "-t", f"{plan.duration:.6f}", "-i", str(info.path)]
    for overlay in overlay_list:
        if overlay is not None:
            cmd += ["-i", str(overlay)]

    filter_complex, v_label, a_label = _build_filter(plans, overlay_list, pix_fmt)
    cmd += ["-filter_complex", filter_complex, "-map", v_label]
    if audio_stream is not None:
        cmd += ["-map", a_label]
    cmd += _encoder_args(codec, crf, preset, pix_fmt)
    cmd += _colour_args(info)
    if info.hdr and not keep_rotation:
        note(f"source is HDR ({info.color_transfer}); writing HDR-tagged {codec} output")
    if keep_rotation:
        cmd += ["-noautorotate"]
    if audio_stream is not None:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(target)]

    if dry_run:
        note("dry run: " + " ".join(cmd[:12]) + " ...")
        return target
    if progress is None:
        run(cmd)
    else:
        total = sum(plan.duration for plan in plans) or 1.0
        run_stream(cmd, on_line=progress_sink(total, progress), cancelled=cancelled)
    return target


def concat_lossless(clips: list[str | Path], out: str | Path) -> tuple[Path, list[str]]:
    """Concat clips with stream copy (fast, bit exact) and report what breaks.

    Returns ``(output, warnings)``. A non-empty warning list means the result has
    timestamp problems -- prefer :func:`render_reel` for reels you care about.
    """
    if not clips:
        raise SystemExit("nothing to concat: empty clip list")
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tennis-evolution-") as tmp:
        listing = Path(tmp) / "concat.txt"
        listing.write_text(
            "".join(f"file '{Path(clip).resolve().as_posix()}'\n" for clip in clips),
            encoding="utf-8",
        )
        result = run(
            [
                ffmpeg_bin(),
                "-y",
                "-v",
                "warning",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(target),
            ]
        )
    warnings: list[str] = []
    stderr = result.stderr or ""
    non_monotonic = stderr.count("Non-monotonic DTS")
    if non_monotonic:
        warnings.append(
            f"{non_monotonic} non-monotonic DTS events at joins (audio timestamps were rewritten)"
        )

    rendered = probe(target)
    video = rendered.video
    if video is not None and (video.start_time or 0) > 0.05:
        warnings.append(
            f"video starts at {video.start_time:.3f}s while audio starts at 0 "
            f"-> permanent {video.start_time:.2f}s A/V offset"
        )
    if video is not None and rendered.has_audio:
        audio_stream = rendered.audio_streams[0]
        if abs((video.duration or 0) - (audio_stream.duration or 0)) > 0.3:
            warnings.append(
                f"stream duration mismatch: video {video.duration:.2f}s vs audio {audio_stream.duration:.2f}s"
            )
    for message in warnings:
        warn(message)
    return target, warnings
