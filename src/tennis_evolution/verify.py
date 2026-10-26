"""Self checks.

Every output of this pipeline is verified instead of assumed: decode integrity,
stream duration equality, zero start offsets (the A/V desync trap), sane audio level
and non-black first/last frames. ``tennis-evolution all`` returns a non-zero exit code
when a check fails, which makes the tool safe to run unattended or from an agent.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .media import FfmpegError, ffmpeg_bin, probe
from .util import format_duration

__all__ = ["CheckResult", "verify_file", "all_ok", "decode_error_lines"]

_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?[\d.]+) dB")
_YAVG = re.compile(r"YAVG=([\d.]+)")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        mark = "OK  " if self.ok else "FAIL"
        detail = f"  {self.detail}" if self.detail else ""
        return f"[{mark}] {self.name}{detail}"


def all_ok(results: list[CheckResult]) -> bool:
    return all(result.ok for result in results)


def _run_raw(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603


def decode_error_lines(path: str | Path) -> list[str]:
    """Decode the whole file and return ffmpeg error lines (empty means clean)."""
    result = _run_raw([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "null", "-"])
    return [line for line in (result.stderr or "").splitlines() if line.strip()]


def _mean_volume(path: str | Path) -> float | None:
    result = _run_raw(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-vn",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    match = _MEAN_VOLUME.search(result.stderr or "")
    return float(match.group(1)) if match else None


def _frame_yavg(path: str | Path, *, last: bool = False) -> float | None:
    seek = ["-sseof", "-0.2"] if last else []
    result = _run_raw(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-v",
            "error",
            *seek,
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ]
    )
    match = _YAVG.search(result.stdout or "")
    return float(match.group(1)) if match else None


def verify_file(
    path: str | Path,
    *,
    expected_duration: float | None = None,
    duration_tol: float = 0.8,
    require_audio: bool = True,
    deep_decode: bool = True,
    check_frames: bool = True,
    dark_threshold: float = 40.0,
    silence_threshold: float = -70.0,
) -> list[CheckResult]:
    """Run the standard battery of checks on one media file."""
    media_path = Path(path)
    results: list[CheckResult] = []
    if not media_path.exists():
        return [CheckResult("file exists", False, str(media_path))]

    try:
        info = probe(media_path)
    except (SystemExit, FfmpegError) as exc:  # pragma: no cover - corrupt input
        return [CheckResult("probe", False, str(exc))]

    results.append(
        CheckResult(
            "video stream",
            info.video is not None,
            info.video.codec_name if info.video else "missing",
        )
    )
    if require_audio:
        results.append(
            CheckResult(
                "audio stream",
                info.has_audio,
                ",".join(s.codec_name for s in info.audio_streams) or "missing",
            )
        )

    for stream in info.streams:
        if stream.codec_type not in {"video", "audio"}:
            continue
        start = stream.start_time or 0.0
        results.append(
            CheckResult(
                f"{stream.codec_type} start offset",
                abs(start) <= 0.05,
                f"{start:.3f}s" if abs(start) > 1e-6 else "0",
            )
        )

    if expected_duration is not None:
        delta = abs(info.duration - expected_duration)
        results.append(
            CheckResult(
                "duration",
                delta <= duration_tol,
                f"{format_duration(info.duration)} vs expected {format_duration(expected_duration)} (Δ{delta:.2f}s)",
            )
        )

    if info.video is not None and info.has_audio:
        video_duration = info.video.duration or info.duration
        audio_duration = info.audio_streams[0].duration or info.duration
        results.append(
            CheckResult(
                "A/V duration match",
                abs(video_duration - audio_duration) <= 0.3,
                f"video {video_duration:.2f}s / audio {audio_duration:.2f}s",
            )
        )

    if deep_decode:
        errors = decode_error_lines(media_path)
        detail = "" if not errors else (errors[0][:160])
        results.append(CheckResult("decode clean", not errors, detail))

    if check_frames:
        first = _frame_yavg(media_path)
        last = _frame_yavg(media_path, last=True)
        pairs = [("first", first), ("last", last)]
        for label, value in pairs:
            if value is None:
                results.append(CheckResult(f"{label} frame brightness", False, "no frame decoded"))
            else:
                results.append(
                    CheckResult(
                        f"{label} frame not black", value >= dark_threshold, f"YAVG={value:.1f}"
                    )
                )

    if info.has_audio and deep_decode:
        mean = _mean_volume(media_path)
        if mean is not None:
            results.append(
                CheckResult("audio not silent", mean > silence_threshold, f"mean {mean:.1f} dB")
            )

    return results
