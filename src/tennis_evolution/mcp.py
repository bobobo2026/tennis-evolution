"""Model Context Protocol server (stdio, newline-delimited JSON-RPC 2.0).

Lets any MCP-capable agent (Claude Code/Desktop, Cursor, Codex, ...) drive the pipeline:

    {"mcpServers": {"tennis-evolution": {"command": "uvx", "args": ["tennis-evolution", "mcp"]}}}

Implemented on the standard library only. Rendering runs in a child process so the CLI's
progress output can never corrupt the JSON-RPC stream on stdout, and long renders are
exposed as background jobs the agent polls.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .highlights import load as load_highlights
from .highlights import select_top
from .license import FREE_MAX_RALLIES, PRICING_URL, LicenseError, activate, current
from .util import format_timecode

__all__ = ["serve_stdio", "handle", "TOOLS"]

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "tennis-evolution turns a long tennis recording into a highlight reel with hit count, venue, "
    "wall clock and source timestamp burned in. Everything runs locally. Typical flow: "
    "probe_video -> rank_highlights (check the rally list) -> start_highlight_reel -> poll "
    "get_job until done -> read manifest.ok. The rally list (time ranges, optionally hit counts) "
    "comes from the user: a sliceapp export, their own notes, or another detector; lines look "
    "like '27:14 - 28:08 40 hits'. Free version keeps the 5 best rallies and adds a small "
    f"watermark; Pro removes both ({PRICING_URL})."
)

_HIGHLIGHT_PROPS: dict[str, Any] = {
    "highlights_path": {
        "type": "string",
        "description": "Path to a rally list (.txt from sliceapp or .json).",
    },
    "highlights_text": {
        "type": "string",
        "description": (
            "Rally list pasted inline, one rally per line, e.g. '27:14 - 28:08 40 hits'. "
            "Use either this or highlights_path."
        ),
    },
    "top": {
        "type": "integer",
        "minimum": 0,
        "description": f"Keep the N best rallies (0 = all; free version caps at {FREE_MAX_RALLIES}).",
        "default": 5,
    },
    "sort": {
        "type": "string",
        "enum": ["hits", "duration", "start"],
        "default": "hits",
        "description": "Ranking: longest rally first (hits), longest clip, or chronological.",
    },
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "probe_video",
        "title": "Inspect a tennis recording",
        "description": (
            "Read duration, resolution, rotation, HDR/colour, audio tracks and recording time of a "
            "local video file. Run this first to confirm the file is readable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"video": {"type": "string", "description": "Local video path."}},
            "required": ["video"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "rank_highlights",
        "title": "Parse and rank a rally list",
        "description": (
            "Parse a rally list and show which rallies would be kept, so the user can confirm "
            "before rendering."
        ),
        "inputSchema": {"type": "object", "properties": _HIGHLIGHT_PROPS},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "start_highlight_reel",
        "title": "Render a labeled tennis highlight reel",
        "description": (
            "Start the full pipeline in the background: lossless keyframe-aligned clips, a plain "
            "reel, a labeled reel (hit count / venue / wall clock / source timestamp) and "
            "manifest.json with self-check results. Returns a job_id; poll get_job. A 47-minute "
            "iPhone HDR recording with 5 rallies takes 3-5 minutes on an M-series Mac."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "video": {"type": "string", "description": "Local source recording."},
                "outdir": {"type": "string", "description": "Output directory (created)."},
                **_HIGHLIGHT_PROPS,
                "place": {"type": "string", "description": "Venue text for the label."},
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone for the wall clock, e.g. Asia/Shanghai.",
                },
                "unit": {"type": "string", "description": "Hit counter unit (default 拍; 'hits')."},
                "source_label": {
                    "type": "string",
                    "description": "Prefix for the source timestamp (default 片源; 'Source').",
                },
                "labels": {"type": "boolean", "default": True},
                "codec": {"type": "string", "enum": ["hevc", "x264"], "default": "hevc"},
            },
            "required": ["video", "outdir"],
        },
    },
    {
        "name": "get_job",
        "title": "Check a render job",
        "description": (
            "Status of a job from start_highlight_reel: running/done/failed, the last log "
            "lines, and once done the manifest (outputs and verification)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "verify_video",
        "title": "Self-check a finished video",
        "description": (
            "Clean decode, zero start offset, A/V duration match, non-black first/last frame, "
            "non-silent audio."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "fast": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "license_status",
        "title": "Show free/Pro status",
        "description": "Whether a Pro licence is active, the free-tier limits and the pricing URL.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "activate_license",
        "title": "Activate a Pro licence key",
        "description": "Verify a te1.… key offline and store it for future renders.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
]


class ToolError(Exception):
    """Reported to the agent as ``isError: true`` instead of a protocol error."""


@dataclass
class Job:
    job_id: str
    outdir: Path
    log_path: Path
    process: subprocess.Popen[bytes]
    started: float = field(default_factory=time.time)
    cleanup: list[Path] = field(default_factory=list)


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "tennis_evolution", *args]


def _highlights_file(arguments: dict[str, Any]) -> tuple[Path, Path | None]:
    path = arguments.get("highlights_path")
    text = arguments.get("highlights_text")
    if path:
        resolved = Path(path).expanduser()
        if not resolved.exists():
            raise ToolError(f"highlights file not found: {resolved}")
        return resolved, None
    if text and str(text).strip():
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", prefix="te-highlights-", delete=False, encoding="utf-8"
        )
        with handle:
            handle.write(str(text))
        tmp = Path(handle.name)
        return tmp, tmp
    raise ToolError("give highlights_path or highlights_text (one rally per line)")


def _run_cli(*args: str, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(_cli(*args), capture_output=True, text=True, timeout=timeout, check=False)


def _tool_probe(arguments: dict[str, Any]) -> dict[str, Any]:
    video = Path(str(arguments["video"])).expanduser()
    if not video.exists():
        raise ToolError(f"video not found: {video}")
    proc = _run_cli("probe", str(video))
    if proc.returncode != 0:
        raise ToolError(proc.stderr.strip()[-2000:] or "probe failed")
    return json.loads(proc.stdout)


def _tool_rank(arguments: dict[str, Any]) -> dict[str, Any]:
    path, tmp = _highlights_file(arguments)
    try:
        segments = load_highlights(path)
    except (ValueError, SystemExit) as exc:
        raise ToolError(f"could not parse the rally list: {exc}") from exc
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    top = int(arguments.get("top", 5))
    pro = current() is not None
    effective = top if pro or 0 < top <= FREE_MAX_RALLIES else FREE_MAX_RALLIES
    chosen = select_top(segments, effective, str(arguments.get("sort", "hits")))
    result: dict[str, Any] = {
        "total": len(segments),
        "selected": [
            {
                "rank": i,
                "start": format_timecode(s.start),
                "end": format_timecode(s.end),
                "hits": s.hits,
                "label": s.label,
                "seconds": round(s.duration, 2),
            }
            for i, s in enumerate(chosen, 1)
        ],
        "tier": "pro" if pro else "free",
    }
    if effective != top:
        result["note"] = (
            f"free version keeps {FREE_MAX_RALLIES} rallies; Pro unlocks all: {PRICING_URL}"
        )
    return result


def _tool_start(arguments: dict[str, Any]) -> dict[str, Any]:
    video = Path(str(arguments["video"])).expanduser()
    if not video.exists():
        raise ToolError(f"video not found: {video}")
    outdir = Path(str(arguments["outdir"])).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    highlights, tmp = _highlights_file(arguments)
    argv = [
        "all",
        str(video),
        "--highlights",
        str(highlights),
        "--top",
        str(int(arguments.get("top", 5))),
        "--sort",
        str(arguments.get("sort", "hits")),
        "--outdir",
        str(outdir),
        "--codec",
        str(arguments.get("codec", "hevc")),
    ]
    for key, flag in (
        ("place", "--place"),
        ("timezone", "--timezone"),
        ("unit", "--unit"),
        ("source_label", "--source-label"),
    ):
        if arguments.get(key):
            argv += [flag, str(arguments[key])]
    if arguments.get("labels") is False:
        argv.append("--no-labels")
    job_id = uuid.uuid4().hex[:12]
    log_path = outdir / f"job-{job_id}.log"
    log = log_path.open("wb")
    process = subprocess.Popen(_cli(*argv), stdout=log, stderr=subprocess.STDOUT)
    log.close()
    job = Job(job_id, outdir, log_path, process, cleanup=[tmp] if tmp else [])
    with _LOCK:
        _JOBS[job_id] = job
    return {
        "job_id": job_id,
        "status": "running",
        "outdir": str(outdir),
        "log": str(log_path),
        "hint": "poll get_job every 20-30 seconds",
    }


def _tail(path: Path, lines: int = 15) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def _tool_job(arguments: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS.get(str(arguments["job_id"]))
    if job is None:
        raise ToolError("unknown job_id (jobs live as long as this MCP server process)")
    code = job.process.poll()
    result: dict[str, Any] = {
        "job_id": job.job_id,
        "elapsed_seconds": round(time.time() - job.started, 1),
        "log_tail": _tail(job.log_path),
    }
    if code is None:
        result["status"] = "running"
        return result
    for path in job.cleanup:
        path.unlink(missing_ok=True)
    manifest_path = job.outdir / "manifest.json"
    result["status"] = "done" if code == 0 else "failed"
    result["exit_code"] = code
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result["manifest_path"] = str(manifest_path)
        result["ok"] = manifest.get("ok")
        result["reels"] = manifest.get("reels")
        result["clips"] = [c.get("path") for c in manifest.get("clips", [])]
        result["tier"] = manifest.get("settings", {}).get("tier")
    return result


def _tool_verify(arguments: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(arguments["path"])).expanduser()
    if not path.exists():
        raise ToolError(f"file not found: {path}")
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "verify.json"
        args = ["verify", str(path), "--json", str(report)]
        if arguments.get("fast"):
            args.append("--fast")
        proc = _run_cli(*args, timeout=1800)
        checks = json.loads(report.read_text()).get("file", []) if report.exists() else []
    return {"ok": proc.returncode == 0, "checks": checks}


def _tool_license(_arguments: dict[str, Any]) -> dict[str, Any]:
    lic = current()
    return {
        "tier": "pro" if lic else "free",
        "license": lic.to_dict() if lic else None,
        "free_limits": {"max_rallies": FREE_MAX_RALLIES, "watermark": True},
        "pricing": PRICING_URL,
    }


def _tool_activate(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        lic = activate(str(arguments["key"]))
    except LicenseError as exc:
        raise ToolError(str(exc)) from exc
    return {"tier": "pro", "license": lic.to_dict()}


_HANDLERS = {
    "probe_video": _tool_probe,
    "rank_highlights": _tool_rank,
    "start_highlight_reel": _tool_start,
    "get_job": _tool_job,
    "verify_video": _tool_verify,
    "license_status": _tool_license,
    "activate_license": _tool_activate,
}


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    handler = _HANDLERS.get(str(name))
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        data = handler(params.get("arguments") or {})
    except (ToolError, KeyError, subprocess.TimeoutExpired) as exc:
        message = f"missing argument: {exc}" if isinstance(exc, KeyError) else str(exc)
        return {"content": [{"type": "text", "text": message}], "isError": True}
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "structuredContent": data,
        "isError": False,
    }


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; ``None`` for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        result: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "tennis-evolution",
                "title": "Tennis Evolution",
                "version": __version__,
            },
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = _call_tool(params)
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve_stdio() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        else:
            reply = handle(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0
