"""Task layer behind the web UI: run the pipeline as a background job.

The web app is a thin wrapper around the same pipeline the CLI uses:
probe -> parse highlights -> keyframe-aligned lossless cuts -> one-pass
label-burned reel -> browser preview -> self checks -> manifest. Nothing here
re-implements the ffmpeg rules; it only orchestrates them, tracks progress, keeps
a small on-disk record and exposes JSON views for the HTTP layer.

Design notes:

* one job runs at a time (two concurrent x265 encodes on one laptop is a lose);
  extra jobs stay ``queued`` and start as soon as the worker frees up;
* every job lives in ``<runs_root>/<job_id>/`` next to its artifacts, so the HTTP
  layer can serve files with a single safe ``resolve_file`` call;
* progress is stage weighted with an intra-stage fraction driven by
  ``ffmpeg -progress`` while encoding (the slow step).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import __version__
from ..cut import ClipPlan, cut_clip, plan_clips
from ..highlights import Segment, parse_json, parse_text, ranked, select_top
from ..labels import discover_fonts, specs_for_plans, write_overlays
from ..media import (
    MediaInfo,
    ffmpeg_bin,
    first_audio_stream,
    keyframes,
    run_stream,
)
from ..media import (
    probe as probe_media,
)
from ..reel import progress_sink, render_reel
from ..util import format_duration, format_timecode
from ..verify import verify_file

__all__ = [
    "JobManager",
    "JobOptions",
    "MAX_UPLOAD_BYTES",
    "QUALITY_PRESETS",
    "SORT_CHOICES",
    "STAGE_LABELS",
    "STAGE_ORDER",
    "default_runs_root",
    "default_demo_dir",
]

log = logging.getLogger("tennis_evolution.webapp")

MAX_UPLOAD_BYTES = 5 * 1024**3

#: Browser-safe siblings (SDR h264) live under ``preview/`` inside a run or the demo dir.
PREVIEW_DIRNAME = "preview"
PREVIEW_WIDTH = 720

QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "preview": {"codec": "x264", "preset": "veryfast", "crf": 24.0},
    "high": {"codec": "hevc", "preset": "fast", "crf": 19.0},
}

SORT_CHOICES = ("hits", "duration", "start")

STAGE_ORDER = (
    "probe",
    "keyframes",
    "cut",
    "labels",
    "render",
    "render_plain",
    "preview",
    "verify",
)

STAGE_LABELS = {
    "queued": "排队中",
    "probe": "解析片源",
    "keyframes": "扫描关键帧",
    "cut": "无损切片",
    "labels": "生成角标",
    "render": "编码角标合集",
    "render_plain": "编码无角标合集",
    "preview": "转码网页预览",
    "verify": "自检",
    "done": "完成",
    "failed": "失败",
    "cancelled": "已取消",
}

_HIGHLIGHT_HELP = (
    "没有解析到任何回合。每行需要「起-止 + 可选拍数」，例如：\n"
    "27:14 - 28:08  Long rally (40 hits)\n"
    '也可以粘贴 JSON：{"segments": [{"start": "27:14", "end": "28:08", "hits": 40}]}'
)

_DEFAULT_PLACE = "宁波 ECHO Tennis"


# --------------------------------------------------------------------------- #
# options
# --------------------------------------------------------------------------- #


@dataclass
class JobOptions:
    """Everything the UI can control for one render."""

    source: str
    highlights: str
    top: int = 5
    sort: str = "hits"
    lead: float = 1.0
    tail: float = 1.5
    place: str | None = _DEFAULT_PLACE
    timezone: str = "Asia/Shanghai"
    unit: str = "拍"
    source_label: str = "片源"
    labels: bool = True
    keep_clips: bool = True
    plain_reel: bool = False
    browser_preview: bool = True
    quality: str = "preview"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> JobOptions:
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known})


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


class _Cancelled(Exception):
    """Raised between stages when the user cancels a job."""


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


def _human_size(size: int) -> str:
    for unit, divisor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= divisor:
            return f"{size / divisor:.2f} {unit}"
    return f"{size} B"


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"未知时区：{name}（示例：Asia/Shanghai）") from exc


def _which(binary: str) -> str | None:
    found = shutil.which(binary)
    if found:
        return found
    candidate = Path(binary)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def default_runs_root() -> Path:
    override = os.environ.get("TENNIS_EVOLUTION_WEB_RUNS")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "webapp-runs"


def default_demo_dir() -> Path | None:
    """``<workspace>/demo_run`` next to the repo, when it exists."""
    candidate = Path(__file__).resolve().parents[3].parent / "demo_run"
    return candidate if candidate.is_dir() else None


def default_defaults() -> dict[str, Any]:
    source = Path.home() / "Downloads" / "IMG_5155.MOV"
    return {
        "source": str(source) if source.is_file() else None,
        "place": _DEFAULT_PLACE,
        "timezone": "Asia/Shanghai",
        "top": 5,
        "quality": "preview",
    }


def _needs_preview(options: JobOptions) -> bool:
    """A h264 reel already plays in every browser; only hevc needs a transcode."""
    if not options.browser_preview:
        return False
    return QUALITY_PRESETS[options.quality]["codec"] != "x264"


def _needs_clip_preview(options: JobOptions) -> bool:
    """Lossless clips keep the *source* codec (hevc/HDR on iPhone) no matter what
    quality preset the reel uses -- ``cut`` is always ``-c copy`` -- so every clip
    the page offers to play needs an SDR h264 sibling.
    """
    return options.browser_preview and options.keep_clips


def _preview_filters(info: MediaInfo) -> list[str]:
    """Filters for a browser-safe sibling: SDR h264, <=720 px wide, 8 bit."""
    filters: list[str] = []
    if info.color_transfer in {"arib-std-b67", "smpte2084"}:
        # ffmpeg without zimg still has the colorspace filter, which is enough here.
        filters.append("colorspace=all=bt709:iall=bt2020:fast=1")
    filters.append(f"scale=w={PREVIEW_WIDTH}:h=-2:force_original_aspect_ratio=decrease")
    filters.append("format=yuv420p")
    return filters


def _stage_plan(options: JobOptions) -> list[tuple[str, float]]:
    stages: list[tuple[str, float]] = [("probe", 0.04), ("keyframes", 0.06)]
    if options.keep_clips:
        stages.append(("cut", 0.12))
    if options.labels:
        stages.append(("labels", 0.02))
    renders = int(bool(options.labels)) + int(bool(options.plain_reel))
    render_weight = 0.55 / renders if renders else 0.0
    if options.labels:
        stages.append(("render", render_weight))
    if options.plain_reel:
        stages.append(("render_plain", render_weight))
    if _needs_preview(options) or _needs_clip_preview(options):
        stages.append(("preview", 0.08))
    stages.append(("verify", 0.05))
    return stages


class _Tracker:
    """Map (stage, fraction) to one monotonic overall progress value."""

    def __init__(self, stages: list[tuple[str, float]]) -> None:
        self.total = sum(weight for _, weight in stages) or 1.0
        self.weights = dict(stages)
        self.offsets: dict[str, float] = {}
        accumulated = 0.0
        for name, weight in stages:
            self.offsets[name] = accumulated
            accumulated += weight / self.total

    def value(self, stage: str, fraction: float) -> float:
        base = self.offsets.get(stage, 0.0)
        weight = self.weights.get(stage, 0.0) / self.total
        clamped = max(0.0, min(1.0, fraction))
        return round(min(0.999, base + weight * clamped), 4)


def _elapsed(job: dict[str, Any]) -> float:
    started = job.get("started_at")
    if not started:
        return 0.0
    try:
        began = datetime.fromisoformat(started)
    except ValueError:  # pragma: no cover - defensive
        return 0.0
    finished = job.get("finished_at")
    end = datetime.fromisoformat(finished) if finished else _now()
    return round(max(0.0, (end - began).total_seconds()), 1)


def _segment_view(index: int, segment: Segment) -> dict[str, Any]:
    return {
        "index": index,
        "start": round(segment.start, 3),
        "end": round(segment.end, 3),
        "start_hms": format_timecode(segment.start),
        "end_hms": format_timecode(segment.end),
        "hits": segment.hits,
        "duration": round(segment.duration, 3),
        "duration_hms": format_duration(segment.duration),
        "label": segment.label,
    }


def source_view(info: MediaInfo, timezone: str = "Asia/Shanghai") -> dict[str, Any]:
    """JSON view of a probed recording (shared by /api/probe and job views)."""
    tz = _zone(timezone)
    recorded = None
    if info.created_at is not None:
        moment = info.created_at
        if moment.tzinfo is None:  # pragma: no cover - ffprobe returns aware times
            moment = moment.replace(tzinfo=UTC)
        if tz is not None:
            recorded = moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        else:
            recorded = moment.isoformat(timespec="seconds")
    video = info.video
    return {
        "path": str(info.path),
        "name": info.path.name,
        "duration": round(info.duration, 3),
        "duration_hms": format_duration(info.duration),
        "size": info.size,
        "size_human": _human_size(info.size),
        "width": info.display_width,
        "height": info.display_height,
        "rotation": info.rotation,
        "hdr": info.hdr,
        "color_transfer": info.color_transfer,
        "created_at": info.created_at.isoformat() if info.created_at else None,
        "recorded_at_local": recorded,
        "format": info.format_name,
        "video_codec": video.codec_name if video else None,
        "fps": round(video.fps, 2) if video and video.fps else None,
        "bit_depth": info.bit_depth,
        "has_audio": info.has_audio,
        "audio_streams": [
            {
                "index": stream.index,
                "codec": stream.codec_name,
                "channels": stream.channels,
                "sample_rate": stream.sample_rate,
            }
            for stream in info.audio_streams
        ],
    }


def _source_warnings(info: MediaInfo) -> list[str]:
    warnings: list[str] = []
    if any(stream.codec_name in {"apac", "unknown", "none"} for stream in info.audio_streams):
        warnings.append("源片含 ffmpeg 无法解码的空间音频轨（apac），将自动只用 AAC 立体声")
    if info.created_at is None:
        warnings.append("源片缺少录制时间，角标钟点改用文件修改时间")
    if not info.has_audio:
        warnings.append("源片没有音轨，将输出无声视频")
    return warnings


# --------------------------------------------------------------------------- #
# manager
# --------------------------------------------------------------------------- #


class JobManager:
    """Owns the job queue, the worker thread and the on-disk run records."""

    def __init__(
        self,
        runs_root: Path | str | None = None,
        demo_dir: Path | str | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        self.runs_root = Path(runs_root or default_runs_root()).expanduser().resolve()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        resolved_demo = demo_dir if demo_dir is not None else default_demo_dir()
        self.demo_dir = Path(resolved_demo).expanduser().resolve() if resolved_demo else None
        self.defaults = defaults if defaults is not None else default_defaults()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._preview_lock = threading.Lock()
        self._demo_preview_started = False
        self._load()
        self._worker = threading.Thread(
            target=self._work_loop, name="tennis-web-worker", daemon=True
        )
        self._worker.start()
        log.info("runs root: %s (demo: %s)", self.runs_root, self.demo_dir or "none")

    # -- queue ------------------------------------------------------------- #

    def create(self, options: JobOptions) -> dict[str, Any]:
        """Validate, queue and immediately return the job summary."""
        self._validate(options)
        job_id = _now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        directory = self.runs_root / job_id
        directory.mkdir(parents=True, exist_ok=False)
        job: dict[str, Any] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "stage_label": STAGE_LABELS["queued"],
            "progress": 0.0,
            "created_at": _iso(),
            "started_at": None,
            "finished_at": None,
            "options": options.to_dict(),
            "source": None,
            "selection": [],
            "logs": [],
            "error": None,
            "result": None,
            "directory": str(directory),
            "_cancel": threading.Event(),
            "_persist_at": 0.0,
            "_tracker": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._log(
            job,
            f"任务已创建：{Path(options.source).name} · 保留 {options.top or '全部'} 段 · "
            f"画质 {options.quality} · 排队中",
        )
        self._persist(job, force=True)
        self._queue.put(job_id)
        return self._summary(job)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job["status"] not in {"queued", "running"}:
            return False
        job["_cancel"].set()
        if job["status"] == "queued":
            job.update(
                status="cancelled",
                stage="cancelled",
                stage_label=STAGE_LABELS["cancelled"],
                error="任务已取消",
                finished_at=_iso(),
            )
            self._log(job, "排队中的任务已取消", "warn")
        else:
            self._log(job, "收到取消请求，正在停止…", "warn")
        self._persist(job, force=True)
        return True

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job["status"] in {"queued", "running"}:
                raise RuntimeError("任务还在运行，请先取消再删除")
            self._jobs.pop(job_id, None)
        directory = Path(job["directory"]).resolve()
        if directory.is_relative_to(self.runs_root):
            shutil.rmtree(directory, ignore_errors=True)
        return True

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return [self._summary(job) for job in jobs[:50]]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._view(job) if job is not None else None

    # -- worker ------------------------------------------------------------ #

    def _work_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:  # pragma: no cover - shutdown path
                return
            with self._lock:
                job = self._jobs.get(job_id)
            if job is None or job["status"] != "queued":
                continue
            self._execute(job)

    def _execute(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        job["started_at"] = _iso()
        self._log(job, "开始执行")
        self._persist(job, force=True)
        try:
            self._run_pipeline(job)
        except _Cancelled:
            job.update(
                status="cancelled",
                stage="cancelled",
                stage_label=STAGE_LABELS["cancelled"],
                error="任务已取消",
            )
            self._log(job, "任务已取消", "warn")
        except (Exception, SystemExit) as exc:
            message = str(exc) or exc.__class__.__name__
            job.update(
                status="failed",
                stage="failed",
                stage_label=STAGE_LABELS["failed"],
                error=message,
            )
            self._log(job, message, "error")
            log.error("job %s failed: %s", job["id"], message)
        else:
            job.update(status="done", stage="done", stage_label=STAGE_LABELS["done"], progress=1.0)
            self._log(job, "全部完成")
        finally:
            job["finished_at"] = _iso()
            self._persist(job, force=True)

    def _run_pipeline(self, job: dict[str, Any]) -> None:
        options = JobOptions.from_dict(job["options"])
        directory = Path(job["directory"])
        tracker = _Tracker(_stage_plan(options))
        job["_tracker"] = tracker

        # 1) probe -----------------------------------------------------------
        self._stage(job, "probe")
        self._check_cancel(job)
        info = probe_media(options.source)
        if info.video is None:
            raise ValueError("这个文件里没有视频流，无法剪辑")
        job["source"] = source_view(info, options.timezone)
        for warning in _source_warnings(info):
            self._log(job, warning, "warn")
        self._log(
            job,
            f"片源 {info.path.name} · {format_duration(info.duration)} · "
            f"{info.display_width}×{info.display_height}" + (" · HDR" if info.hdr else ""),
        )

        # 2) highlights + keyframe alignment ---------------------------------
        segments = self._parse_highlights(options.highlights)
        chosen = select_top(segments, options.top, options.sort)
        if not chosen:
            raise ValueError(_HIGHLIGHT_HELP)
        self._check_cancel(job)
        self._stage(job, "keyframes")
        self._log(job, f"扫描关键帧（{len(chosen)} 个回合窗口；全片扫描约 1 分钟，这里只扫窗口）")
        keyframe_times = self._keyframes_for(chosen, info, options)
        plans = plan_clips(
            info, chosen, lead=options.lead, tail=options.tail, keyframe_list=keyframe_times
        )
        job["selection"] = [
            {
                **_segment_view(plan.index, plan.segment),
                "cut_start": round(plan.start, 3),
                "cut_end": round(plan.end, 3),
                "cut_duration": round(plan.duration, 3),
                "notes": list(plan.notes),
            }
            for plan in plans
        ]
        gaps = [
            b - a
            for a, b in zip(keyframe_times, keyframe_times[1:], strict=False)
            if 0 < b - a < 30
        ]
        self._log(
            job,
            f"高光 {len(segments)} 段，选中 {len(plans)} 段（排序 {options.sort}）"
            + (
                f"，关键帧 {len(keyframe_times)} 个，平均 GOP {sum(gaps) / len(gaps):.2f}s"
                if gaps
                else ""
            ),
        )

        audio = first_audio_stream(info)

        # 3) lossless cuts ---------------------------------------------------
        if options.keep_clips:
            self._stage(job, "cut")
            for index, plan in enumerate(plans, 1):
                self._check_cancel(job)
                cut_clip(info, plan, directory / "clips", audio=audio)
                self._stage(job, "cut", index / len(plans), f"无损切片 {index}/{len(plans)}")
                self._log(
                    job,
                    f"切片 {plan.filename}（源 {format_timecode(plan.start)}–"
                    f"{format_timecode(plan.end)}）",
                )

        # 4) corner labels ---------------------------------------------------
        overlays: list[Path | None] | None = None
        if options.labels:
            self._stage(job, "labels")
            overlays = self._write_overlays(job, info, plans, options)
            self._log(job, f"角标 {len(overlays)} 张（Pillow PNG overlay）")

        # 5) reels -----------------------------------------------------------
        preset = QUALITY_PRESETS[options.quality]
        reels: dict[str, Path] = {}
        if options.labels:
            self._stage(job, "render")
            target = directory / "reel_labeled.mp4"
            self._render(
                job,
                info,
                plans,
                target,
                overlays=overlays,
                audio=audio,
                preset=preset,
                stage="render",
            )
            reels["labeled"] = target
            self._log(
                job,
                f"角标合集 {format_duration(probe_media(target).duration)} · "
                f"{_human_size(target.stat().st_size)}",
            )
        if options.plain_reel:
            self._stage(job, "render_plain")
            target = directory / "reel_plain.mp4"
            self._render(
                job,
                info,
                plans,
                target,
                overlays=None,
                audio=audio,
                preset=preset,
                stage="render_plain",
            )
            reels["plain"] = target

        # 6) browser previews -------------------------------------------------
        # Chrome cannot decode HEVC, and an HDR-tagged h264 file looks washed, so
        # every file the page pretends to play needs an SDR h264 sibling.
        if _needs_preview(options) or _needs_clip_preview(options):
            self._stage(job, "preview")
            self._run_previews(job, options, plans, reels, directory)
            self._log(job, "网页预览已生成（h264 8bit / SDR，正式交付仍用角标合集）")

        # 7) self checks -----------------------------------------------------
        self._stage(job, "verify")
        verification: dict[str, list[dict[str, Any]]] = {}
        checked: list[tuple[str, Path]] = []
        if options.keep_clips:
            checked += [(plan.path.name, plan.path) for plan in plans if plan.path]
        checked += [
            (reels[kind].name, reels[kind]) for kind in ("labeled", "plain") if kind in reels
        ]
        for name, path in checked:
            self._check_cancel(job)
            results = verify_file(path)
            verification[name] = [
                {"name": result.name, "ok": result.ok, "detail": result.detail}
                for result in results
            ]
            failed = [result.name for result in results if not result.ok]
            self._log(
                job,
                f"自检 {name}: {'PASS' if not failed else 'FAIL ' + '、'.join(failed)}",
                "info" if not failed else "error",
            )
        ok = all(all(item["ok"] for item in items) for items in verification.values())
        self._write_manifest(job, info, options, plans, reels, verification, ok)
        job["result"] = self._result(job, plans, reels, verification, ok)
        self._log(
            job,
            "自检全部通过，可以交付" if ok else "自检存在失败项，请看明细",
            "info" if ok else "error",
        )

    # -- pipeline pieces --------------------------------------------------- #

    def _keyframes_for(
        self, segments: list[Segment], info: MediaInfo, options: JobOptions
    ) -> list[float]:
        """Scan key frames only around the chosen rallies, not the whole recording.

        A full scan of a 47 minute 3 GB HEVC file takes ~60 s (ffprobe has to walk
        every packet), which reads as a stalled progress bar in the browser. Scanning
        the needed windows keeps the alignment identical and takes a couple of seconds.
        """
        margin = 5.0
        merged: list[float] = []
        for segment in segments:
            start = max(0.0, segment.start - options.lead - margin)
            end = min(info.duration, segment.end + options.tail + margin)
            try:
                merged.extend(keyframes(info.path, interval=(start, end)))
            except (SystemExit, RuntimeError, OSError):
                merged = []
                break
        if merged:
            return sorted(set(round(moment, 6) for moment in merged))
        # Fall back to the exact code path the CLI uses.
        log.warning("windowed keyframe scan failed for %s, scanning the whole file", info.path.name)
        return keyframes(info.path)

    def _render(
        self,
        job: dict[str, Any],
        info: MediaInfo,
        plans: list[ClipPlan],
        target: Path,
        *,
        overlays: list[Path | None] | None,
        audio: Any,
        preset: dict[str, Any],
        stage: str,
    ) -> None:
        def on_progress(fraction: float) -> None:
            if job["_cancel"].is_set():
                return
            self._stage(job, stage, fraction, f"{STAGE_LABELS[stage]} {int(fraction * 100)}%")

        self._log(job, f"{STAGE_LABELS[stage]}：一次性重编（{len(plans)} 段）")
        try:
            render_reel(
                info,
                plans,
                target,
                overlays=overlays,
                crf=preset["crf"],
                preset=preset["preset"],
                codec=preset["codec"],
                audio=audio,
                progress=on_progress,
                cancelled=job["_cancel"].is_set,
            )
        except Exception:
            if job["_cancel"].is_set():
                raise _Cancelled() from None
            raise
        self._stage(job, stage, 1.0, f"{STAGE_LABELS[stage]} 100%")

    def _run_previews(
        self,
        job: dict[str, Any],
        options: JobOptions,
        plans: list[ClipPlan],
        reels: dict[str, Path],
        directory: Path,
    ) -> None:
        tasks: list[tuple[str, Path, Path]] = []
        if _needs_preview(options):
            for kind in ("labeled", "plain"):
                if kind in reels:
                    tasks.append(
                        (
                            f"合集预览 {reels[kind].name}",
                            reels[kind],
                            directory / PREVIEW_DIRNAME / f"reel_{kind}.mp4",
                        )
                    )
        if _needs_clip_preview(options):
            for plan in plans:
                if plan.path is None:
                    continue
                tasks.append(
                    (
                        f"片段预览 {plan.path.name}",
                        plan.path,
                        directory / PREVIEW_DIRNAME / "clips" / plan.path.name,
                    )
                )
        total = len(tasks) or 1
        for index, (label, source, target) in enumerate(tasks, 1):
            self._check_cancel(job)

            def on_progress(fraction: float, index: int = index) -> None:
                if job["_cancel"].is_set():
                    return
                overall = (index - 1 + fraction) / total
                self._stage(
                    job,
                    "preview",
                    overall,
                    f"转码网页预览 {index}/{total}（{int(fraction * 100)}%）",
                )

            self._transcode(job, source, target, on_progress)
            self._log(job, f"{label} → {target.name}")
        self._stage(job, "preview", 1.0, "转码网页预览 100%")

    def _transcode(
        self,
        job: dict[str, Any] | None,
        source: Path,
        target: Path,
        progress: Any = None,
    ) -> None:
        """Transcode one file to a 720p SDR h264 sibling (atomic, resumable)."""
        info = probe_media(source)
        duration = info.duration or 1.0
        filters = _preview_filters(info)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.stem}.tmp.mp4")
        cmd = [
            ffmpeg_bin(),
            "-y",
            "-v",
            "warning",
            "-progress",
            "pipe:1",
            "-nostats",
            "-i",
            str(source),
            "-vf",
            ",".join(filters),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(temp),
        ]
        on_line = progress_sink(duration, progress) if progress is not None else None
        cancelled = job["_cancel"].is_set if job is not None else None
        succeeded = False
        try:
            run_stream(cmd, on_line=on_line, cancelled=cancelled)
            succeeded = True
        except Exception:
            if job is not None and job["_cancel"].is_set():
                raise _Cancelled() from None
            raise
        finally:
            if succeeded:
                temp.replace(target)
            elif temp.exists():
                temp.unlink(missing_ok=True)

    def _write_overlays(
        self,
        job: dict[str, Any],
        info: MediaInfo,
        plans: list[ClipPlan],
        options: JobOptions,
    ) -> list[Path]:
        tz = _zone(options.timezone)
        created = info.created_at
        if created is None:
            created = datetime.fromtimestamp(Path(options.source).stat().st_mtime, tz=UTC)
            self._log(job, "源片没有 creation_time，角标钟点用文件修改时间", "warn")
        fonts = discover_fonts()
        specs = specs_for_plans(
            plans,
            created_at=created,
            tz=tz,
            place=options.place,
            unit=options.unit,
            source_label=options.source_label,
        )
        return write_overlays(
            (info.display_width, info.display_height),
            specs,
            Path(job["directory"]) / "labels",
            fonts,
        )

    def _write_manifest(
        self,
        job: dict[str, Any],
        info: MediaInfo,
        options: JobOptions,
        plans: list[ClipPlan],
        reels: dict[str, Path],
        verification: dict[str, list[dict[str, Any]]],
        ok: bool,
    ) -> None:
        preset = QUALITY_PRESETS[options.quality]
        payload = {
            "tool": f"tennis-evolution {__version__}",
            "source": {
                "path": str(info.path),
                "duration": round(info.duration, 3),
                "size": info.size,
                "rotation": info.rotation,
                "hdr": info.hdr,
                "color_transfer": info.color_transfer,
                "created_at": info.created_at.isoformat() if info.created_at else None,
            },
            "settings": {
                "top": options.top,
                "sort": options.sort,
                "lead": options.lead,
                "tail": options.tail,
                "crf": preset["crf"],
                "preset": preset["preset"],
                "codec": preset["codec"],
                "place": options.place,
                "timezone": options.timezone,
                "labels": options.labels,
            },
            "clips": [plan.to_dict() for plan in plans],
            "reels": {kind: str(path) for kind, path in reels.items()},
            "verification": verification,
            "ok": ok,
            "web": {
                "job_id": job["id"],
                "quality": options.quality,
                "keep_clips": options.keep_clips,
                "plain_reel": options.plain_reel,
                "browser_preview": options.browser_preview,
                "note": options.note,
                "generated_at": _iso(),
            },
        }
        target = Path(job["directory"]) / "manifest.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # -- views ------------------------------------------------------------- #

    def _result(
        self,
        job: dict[str, Any],
        plans: list[ClipPlan],
        reels: dict[str, Path],
        verification: dict[str, list[dict[str, Any]]],
        ok: bool,
    ) -> dict[str, Any]:
        job_id = job["id"]
        preview_dir = Path(job["directory"]) / PREVIEW_DIRNAME

        def url(relative: str) -> str:
            return f"/files/{job_id}/{relative}"

        clips: list[dict[str, Any]] = []
        for plan in plans:
            path = plan.path
            if path is None:
                continue
            preview = preview_dir / "clips" / path.name
            clips.append(
                {
                    "index": plan.index,
                    "name": path.name,
                    "url": url(f"clips/{path.name}"),
                    "preview_url": url(f"{PREVIEW_DIRNAME}/clips/{path.name}")
                    if preview.is_file()
                    else None,
                    "duration": round(plan.duration, 3),
                    "duration_hms": format_duration(plan.duration),
                    "hits": plan.segment.hits,
                    "start": round(plan.start, 3),
                    "end": round(plan.end, 3),
                    "start_hms": format_timecode(plan.start),
                    "end_hms": format_timecode(plan.end),
                    "size": path.stat().st_size if path.exists() else 0,
                }
            )
        reel_views: dict[str, Any] = {}
        for kind, path in reels.items():
            if not path.exists():
                continue
            preview = preview_dir / f"reel_{kind}.mp4"
            rendered = probe_media(path)
            codec = rendered.video.codec_name if rendered.video else None
            reel_views[kind] = {
                "name": path.name,
                "url": url(path.name),
                "preview_url": url(f"{PREVIEW_DIRNAME}/reel_{kind}.mp4")
                if preview.is_file()
                else None,
                "codec": codec,
                # h264/vp9/av1 play in Chrome; hevc only in Safari (or with a preview row)
                "browser_playable": codec in {"h264", "vp9", "av1"},
                "duration": round(rendered.duration, 3),
                "size": path.stat().st_size,
            }
        label_dir = Path(job["directory"]) / "labels"
        labels = (
            [
                {"index": index, "url": url(f"labels/{path.name}")}
                for index, path in enumerate(sorted(label_dir.glob("label_*.png")), 1)
            ]
            if label_dir.is_dir()
            else []
        )
        outputs: list[dict[str, Any]] = []
        for clip in clips:
            outputs.append(
                {
                    "name": clip["name"],
                    "url": clip["url"],
                    "kind": "clip",
                    "size": clip["size"],
                    "duration": clip["duration"],
                }
            )
        for view in reel_views.values():
            outputs.append(
                {
                    "name": view["name"],
                    "url": view["url"],
                    "kind": "reel",
                    "size": view["size"],
                    "duration": view["duration"],
                }
            )
        for clip in clips:
            if clip.get("preview_url"):
                preview_path = preview_dir / "clips" / clip["name"]
                outputs.append(
                    {
                        "name": f"preview_{clip['name']}",
                        "url": clip["preview_url"],
                        "kind": "preview",
                        "size": preview_path.stat().st_size if preview_path.is_file() else 0,
                    }
                )
        for kind, view in reel_views.items():
            if view.get("preview_url"):
                preview_path = preview_dir / f"reel_{kind}.mp4"
                outputs.append(
                    {
                        "name": f"preview_reel_{kind}.mp4",
                        "url": view["preview_url"],
                        "kind": "preview",
                        "size": preview_path.stat().st_size if preview_path.is_file() else 0,
                    }
                )
        for label in labels:
            outputs.append(
                {"name": f"label_{label['index']:02d}.png", "url": label["url"], "kind": "label"}
            )
        outputs.append({"name": "manifest.json", "url": url("manifest.json"), "kind": "manifest"})
        return {
            "ok": ok,
            "clips": clips,
            "reels": reel_views,
            "labels": labels,
            "verification": verification,
            "manifest_url": url("manifest.json"),
            "outputs": outputs,
        }

    def _summary(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job["id"],
            "status": job["status"],
            "stage": job["stage"],
            "stage_label": job["stage_label"],
            "progress": job["progress"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "elapsed": _elapsed(job),
            "source_name": (job["source"] or {}).get("name")
            or Path(job["options"].get("source", "")).name,
            "top": job["options"].get("top"),
            "quality": job["options"].get("quality"),
            "ok": (job["result"] or {}).get("ok"),
            "error": job["error"],
            "queue_position": self._queue_position(job),
        }

    def _queue_position(self, job: dict[str, Any]) -> int:
        if job["status"] != "queued":
            return 0
        with self._lock:
            waiting = [
                item
                for item in self._jobs.values()
                if item["status"] == "queued" and item["created_at"] <= job["created_at"]
            ]
        return len(waiting)

    def _view(self, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {
                "id": job["id"],
                "status": job["status"],
                "stage": job["stage"],
                "stage_label": job["stage_label"],
                "progress": job["progress"],
                "created_at": job["created_at"],
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
                "elapsed": _elapsed(job),
                "options": dict(job["options"]),
                "source": job["source"],
                "selection": list(job["selection"]),
                "logs": list(job["logs"]),
                "error": job["error"],
                "result": job["result"],
                "queue_position": self._queue_position(job),
                "stages": list(STAGE_ORDER),
            }

    # -- progress / logging / persistence ---------------------------------- #

    def _stage(
        self,
        job: dict[str, Any],
        stage: str,
        fraction: float = 0.0,
        label: str | None = None,
    ) -> None:
        if job["_cancel"].is_set():
            return
        tracker = job["_tracker"]
        job["stage"] = stage
        if label is not None:
            job["stage_label"] = label
        elif fraction:
            job["stage_label"] = f"{STAGE_LABELS.get(stage, stage)} {int(fraction * 100)}%"
        else:
            job["stage_label"] = STAGE_LABELS.get(stage, stage)
        if tracker is not None:
            job["progress"] = tracker.value(stage, fraction)
        self._persist(job)

    def _log(self, job: dict[str, Any], text: str, level: str = "info") -> None:
        entry = {"t": _iso(), "level": level, "text": text}
        with self._lock:
            job["logs"].append(entry)
            if len(job["logs"]) > 300:
                del job["logs"][:-300]
        log.debug("job %s: %s", job["id"], text)

    def _persist(self, job: dict[str, Any], *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - job["_persist_at"] < 1.0:
            return
        job["_persist_at"] = now
        target = Path(job["directory"]) / "job.json"
        try:
            target.write_text(
                json.dumps(self._view(job), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
        except OSError as exc:  # pragma: no cover - disk full / permission
            log.warning("cannot persist job %s: %s", job["id"], exc)

    def _check_cancel(self, job: dict[str, Any]) -> None:
        if job["_cancel"].is_set():
            raise _Cancelled()

    def _load(self) -> None:
        for record in sorted(self.runs_root.glob("*/job.json")):
            try:
                payload = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or "id" not in payload:
                continue
            job = {
                "id": payload["id"],
                "status": payload.get("status", "failed"),
                "stage": payload.get("stage", "failed"),
                "stage_label": payload.get("stage_label", ""),
                "progress": float(payload.get("progress") or 0.0),
                "created_at": payload.get("created_at") or _iso(),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "options": payload.get("options") or {},
                "source": payload.get("source"),
                "selection": payload.get("selection") or [],
                "logs": payload.get("logs") or [],
                "error": payload.get("error"),
                "result": payload.get("result"),
                "directory": str(record.parent),
                "_cancel": threading.Event(),
                "_persist_at": 0.0,
                "_tracker": None,
            }
            if job["status"] in {"queued", "running"}:
                # The process that owned this job is gone; be honest about it.
                job.update(
                    status="failed",
                    stage="failed",
                    stage_label=STAGE_LABELS["failed"],
                    error="服务重启，任务中断",
                    finished_at=job["finished_at"] or _iso(),
                )
            self._jobs[job["id"]] = job

    # -- validation -------------------------------------------------------- #

    def _validate(self, options: JobOptions) -> None:
        if not str(options.source).strip():
            raise ValueError("请填写片源路径")
        source = Path(options.source).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"片源不存在：{source}")
        if not source.is_file():
            raise ValueError("片源路径必须是一个文件")
        options.source = str(source.resolve())
        if options.top < 0:
            raise ValueError("保留条数必须 ≥ 0（0 表示全部）")
        if options.sort not in SORT_CHOICES:
            raise ValueError("排序只支持 hits / duration / start")
        if options.quality not in QUALITY_PRESETS:
            raise ValueError("画质档只支持 preview / high")
        if not 0 <= options.lead <= 30 or not 0 <= options.tail <= 30:
            raise ValueError("前后留白必须在 0–30 秒之间")
        if not options.labels and not options.plain_reel:
            raise ValueError("至少要生成一条合集：勾选「角标合集」或「无角标合集」")
        _zone(options.timezone)
        if not self._parse_highlights(options.highlights):
            raise ValueError(_HIGHLIGHT_HELP)

    # -- shared parses ----------------------------------------------------- #

    @staticmethod
    def _parse_highlights(text: str) -> list[Segment]:
        stripped = (text or "").strip()
        if not stripped:
            return []
        if stripped.startswith(("{", "[")):
            try:
                return parse_json(json.loads(stripped))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        segments = parse_text(stripped)
        if segments:
            return segments
        try:
            return parse_json(json.loads(stripped))
        except (json.JSONDecodeError, ValueError, TypeError):
            return []

    def probe_path(self, path: str) -> dict[str, Any]:
        if not str(path).strip():
            raise ValueError("请填写片源路径")
        target = Path(path).expanduser()
        if not target.exists():
            raise FileNotFoundError(f"片源不存在：{target}")
        if not target.is_file():
            raise ValueError("片源路径必须是一个文件")
        info = probe_media(target)
        if info.video is None:
            raise ValueError("这个文件里没有视频流")
        return {
            "ok": True,
            "source": source_view(info, self.defaults.get("timezone", "Asia/Shanghai")),
            "warnings": _source_warnings(info),
        }

    # Contract alias: docs/webapp.md §3.2 names this `probe`.
    probe = probe_path

    def parse_preview(
        self, text: str, top: int = 5, sort: str = "hits", lead: float = 1.0, tail: float = 1.5
    ) -> dict[str, Any]:
        segments = self._parse_highlights(text)
        if not segments:
            raise ValueError(_HIGHLIGHT_HELP)
        if sort not in SORT_CHOICES:
            raise ValueError("排序只支持 hits / duration / start")
        ordered = ranked(segments, sort)
        top = int(top)
        chosen = ordered[:top] if top > 0 else ordered
        dropped = ordered[len(chosen) :]
        candidate_duration = sum(segment.duration for segment in ordered)
        padded = sum(segment.duration + lead + tail for segment in chosen)
        return {
            "ok": True,
            "total": len(ordered),
            "sort": sort,
            "top": top,
            "selected": [_segment_view(index, segment) for index, segment in enumerate(chosen, 1)],
            "dropped": [
                {
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "start_hms": format_timecode(segment.start),
                    "end_hms": format_timecode(segment.end),
                    "hits": segment.hits,
                    "duration": round(segment.duration, 3),
                    "duration_hms": format_duration(segment.duration),
                    "label": segment.label,
                }
                for segment in dropped
            ],
            "summary": {
                "candidate_duration": round(candidate_duration, 1),
                "candidate_duration_hms": format_duration(candidate_duration),
                "with_padding_hms": format_duration(padded),
                "estimated_output_hms": format_duration(padded),
            },
        }

    # -- files ------------------------------------------------------------- #

    def resolve_file(self, job_id: str, relpath: str) -> Path | None:
        # Defence in depth: the HTTP layer validates the id too, but never let a
        # crafted ``..`` walk out of runs_root if this is called directly.
        if job_id in {".", ".."} or "/" in job_id or "\\" in job_id:
            return None
        base = (self.runs_root / job_id).resolve()
        if not base.is_relative_to(self.runs_root) or not base.is_dir():
            return None
        candidate = (base / relpath).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            return None
        return candidate

    def demo_file(self, relpath: str) -> Path | None:
        if self.demo_dir is None:
            return None
        base = self.demo_dir.resolve()
        candidate = (base / relpath).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            return None
        return candidate

    # -- demo / health ----------------------------------------------------- #

    def _start_demo_previews(self) -> None:
        """Kick off a one-off background transcode of the pre-rendered demo."""
        if self.demo_dir is None:
            return
        with self._preview_lock:
            if self._demo_preview_started:
                return
            self._demo_preview_started = True
        threading.Thread(target=self._demo_preview_worker, name="demo-preview", daemon=True).start()

    def _demo_preview_worker(self) -> None:
        """Create SDR h264 siblings for the demo reel and clips (Chrome can't do HEVC)."""
        assert self.demo_dir is not None
        try:
            manifest = json.loads((self.demo_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        preview_dir = self.demo_dir / PREVIEW_DIRNAME
        reels = manifest.get("reels") or {}
        reel_sources: list[tuple[str, Path]] = []
        for kind in ("labeled", "plain"):
            raw = reels.get(kind)
            if not raw:
                continue
            candidate = self.demo_dir / Path(raw).name
            if candidate.is_file():
                reel_sources.append((kind, candidate))
        try:
            for kind, source in reel_sources:
                target = preview_dir / f"reel_{kind}.mp4"
                if not target.is_file():
                    log.info("demo preview: transcoding %s", source.name)
                    self._transcode(None, source, target)
            for entry in manifest.get("clips") or []:
                raw = entry.get("path")
                if not raw:
                    continue
                source = self.demo_dir / "clips" / Path(raw).name
                target = preview_dir / "clips" / source.name
                if source.is_file() and not target.is_file():
                    log.info("demo preview: transcoding %s", source.name)
                    self._transcode(None, source, target)
        except Exception as exc:  # noqa: BLE001 - demo preview is best effort
            log.warning("demo preview generation failed: %s", exc)
            return
        log.info("demo previews ready under %s", preview_dir)

    def demo(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"ok": True, "available": False}
        if self.demo_dir is None:
            return empty
        manifest_path = self.demo_dir / "manifest.json"
        if not manifest_path.is_file():
            return empty
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty

        def demo_url(relative: str) -> str:
            return f"/demo-files/{relative}"

        source = manifest.get("source") or {}
        settings = manifest.get("settings") or {}
        reels: dict[str, Any] = {}
        for kind in ("labeled", "plain"):
            raw = (manifest.get("reels") or {}).get(kind)
            if not raw:
                continue
            path = self.demo_dir / Path(raw).name
            if not path.is_file():
                continue
            reels[kind] = {
                "name": path.name,
                "url": demo_url(path.name),
                "duration": _safe_duration(path),
                "size": path.stat().st_size,
            }
        clips: list[dict[str, Any]] = []
        for entry in manifest.get("clips") or []:
            raw = entry.get("path")
            if not raw:
                continue
            path = self.demo_dir / "clips" / Path(raw).name
            if not path.is_file():
                continue
            segment = entry.get("segment") or {}
            duration = float(entry.get("duration") or 0.0)
            clips.append(
                {
                    "index": entry.get("index"),
                    "name": path.name,
                    "url": demo_url(f"clips/{path.name}"),
                    "duration": round(duration, 3),
                    "duration_hms": format_duration(duration),
                    "hits": segment.get("hits"),
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "start_hms": format_timecode(float(segment.get("start") or 0.0)),
                    "end_hms": format_timecode(float(segment.get("end") or 0.0)),
                    "size": path.stat().st_size,
                }
            )
        preview_dir = self.demo_dir / PREVIEW_DIRNAME
        preview_ready = False
        for kind in ("labeled", "plain"):
            if kind not in reels:
                continue
            candidate = preview_dir / f"reel_{kind}.mp4"
            if not candidate.is_file():
                reels[kind]["preview_url"] = None
                continue
            preview_ready = True
            preview_url = demo_url(f"{PREVIEW_DIRNAME}/reel_{kind}.mp4")
            reels[kind]["preview_url"] = preview_url
            if "preview" not in reels:
                reels["preview"] = {
                    "name": candidate.name,
                    "url": preview_url,
                    "duration": _safe_duration(candidate),
                    "size": candidate.stat().st_size,
                }
        for clip in clips:
            candidate_preview = preview_dir / "clips" / clip["name"]
            clip["preview_url"] = (
                demo_url(f"{PREVIEW_DIRNAME}/clips/{clip['name']}")
                if candidate_preview.is_file()
                else None
            )
        preview_pending = bool(clips or reels) and not preview_ready
        if preview_pending:
            self._start_demo_previews()
        label_dir = self.demo_dir / "labels"
        labels = (
            [
                {"index": index, "url": demo_url(f"labels/{path.name}")}
                for index, path in enumerate(sorted(label_dir.glob("label_*.png")), 1)
            ]
            if label_dir.is_dir()
            else []
        )
        demo_width, demo_height = (None, None)
        primary_reel = (manifest.get("reels") or {}).get("labeled")
        if primary_reel:
            primary_path = self.demo_dir / Path(primary_reel).name
            if primary_path.is_file():
                demo_width, demo_height = _safe_display_size(primary_path)
        verification = manifest.get("verification") or {}
        checks = sum(len(items) for items in verification.values())
        failed = sum(1 for items in verification.values() for item in items if not item.get("ok"))
        created = source.get("created_at")
        recorded = None
        if created:
            try:
                moment = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                tz = _zone(settings.get("timezone") or "Asia/Shanghai")
                if tz is not None:
                    recorded = moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                recorded = None
        duration = float(source.get("duration") or 0.0)
        return {
            "ok": True,
            "available": bool(clips or reels),
            "title": "前五长回合 · 角标合集",
            "source": {
                "name": Path(str(source.get("path") or "IMG_5155.MOV")).name,
                "duration": round(duration, 3),
                "duration_hms": format_duration(duration),
                "size": source.get("size"),
                "hdr": source.get("hdr"),
                "width": demo_width,
                "height": demo_height,
                "created_at": created,
                "recorded_at_local": recorded,
            },
            "settings": {
                "top": settings.get("top"),
                "sort": settings.get("sort"),
                "place": settings.get("place"),
                "timezone": settings.get("timezone"),
                "lead": settings.get("lead"),
                "tail": settings.get("tail"),
                "codec": settings.get("codec"),
            },
            "reels": reels,
            "clips": clips,
            "labels": labels,
            "verification": verification,
            "preview_pending": preview_pending,
            "verdict": {
                "ok": bool(manifest.get("ok")) and failed == 0,
                "files": len(verification),
                "checks": checks,
                "failed": failed,
            },
            "manifest_url": demo_url("manifest.json"),
        }

    def health(self) -> dict[str, Any]:
        ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
        ffprobe = os.environ.get("FFPROBE", "ffprobe")
        ffmpeg_path = _which(ffmpeg)
        ffprobe_path = _which(ffprobe)
        return {
            "ok": True,
            "version": __version__,
            "runs_root": str(self.runs_root),
            "tools": {
                "ffmpeg": ffmpeg_path,
                "ffprobe": ffprobe_path,
                "ok": bool(ffmpeg_path and ffprobe_path),
            },
            "demo": {"available": self.demo().get("available", False)},
            "limits": {"max_upload_bytes": MAX_UPLOAD_BYTES},
            "defaults": dict(self.defaults),
        }


def _safe_display_size(path: Path) -> tuple[int | None, int | None]:
    """Width/height after rotation, or (None, None) when the file cannot be probed."""
    try:
        info = probe_media(path)
    except (SystemExit, RuntimeError, ValueError, OSError):
        return (None, None)
    return (info.display_width or None, info.display_height or None)


def _safe_duration(path: Path) -> float:
    """Probe a file's duration, returning 0.0 instead of exploding on a bad file."""
    try:
        return round(probe_media(path).duration, 3)
    except (SystemExit, RuntimeError, ValueError, OSError):
        return 0.0
