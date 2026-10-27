"""HTTP layer for the local tennis-evolution web UI.

The route table, payload shapes and error format follow ``docs/webapp.md`` §2.
Only the Python standard library is used; the task layer lives in
:mod:`tennis_evolution.webapp.jobs` and is imported lazily inside request
handlers so a broken job layer can never kill the server at import time.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import uuid
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from .. import __version__

try:  # the job layer ships with this package; keep the HTTP layer importable alone
    from .jobs import MAX_UPLOAD_BYTES
except ImportError:  # pragma: no cover - defensive fallback
    MAX_UPLOAD_BYTES = 5 * 1024**3

logger = logging.getLogger("tennis_evolution.webapp")

DEFAULT_PORT = 8787
MAX_JSON_BODY_BYTES = 16 * 1024 * 1024
COPY_CHUNK_SIZE = 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_UNSAFE_FILENAME_RE = re.compile(r"[/\\:\x00-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

_MIME_OVERRIDES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".hevc": "video/mp4",
    ".webm": "video/webm",
    ".m3u8": "application/vnd.apple.mpegurl",
}


class AppError(Exception):
    """Request error that is rendered as a uniform JSON error response."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


def _guess_content_type(path: Path) -> str:
    override = _MIME_OVERRIDES.get(path.suffix.lower())
    if override is not None:
        return override
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _clean_relpath(relpath: str) -> str | None:
    """Normalise a relative URL path; return ``None`` for absolute/escaping input."""
    if not relpath or "\x00" in relpath:
        return None
    cleaned = relpath.replace("\\", "/")
    if cleaned.startswith("/") or _DRIVE_RE.match(cleaned):
        return None
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _resolve_under(root: Path | None, relpath: str) -> Path | None:
    """Resolve ``relpath`` inside ``root``; ``None`` on escape, traversal or miss."""
    if root is None:
        return None
    cleaned = _clean_relpath(relpath)
    if cleaned is None:
        return None
    try:
        base = root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        target = base.joinpath(*cleaned.split("/")).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target if target.is_file() else None


def _valid_job_id(job_id: str) -> bool:
    return bool(job_id) and ".." not in job_id and _JOB_ID_RE.match(job_id) is not None


def _sanitize_filename(name: str) -> str:
    """Keep unicode names (Chinese included) but drop separators and control chars."""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", name).strip()
    cleaned = cleaned.strip(".").strip()
    if not cleaned or cleaned in {".", ".."}:
        return ""
    return cleaned[:180]


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _parse_range(header: str, size: int) -> tuple[int, int] | Literal["unsatisfiable"] | None:
    """Parse a single ``bytes=`` range.

    Returns inclusive ``(start, end)``, the literal ``"unsatisfiable"``, or
    ``None`` when the header must be ignored (malformed or multi-range) so the
    full body is served.
    """
    spec = header.strip()
    if not spec.lower().startswith("bytes="):
        return None
    value = spec[len("bytes=") :].strip()
    if "," in value:
        # Multi-range is valid but rare; serving the whole file is good enough.
        return None
    first, sep, last = value.partition("-")
    if not sep:
        return None
    first = first.strip()
    last = last.strip()
    if not first:
        if not last.isdigit():
            return None
        suffix = int(last)
        if suffix == 0 or size == 0:
            return "unsatisfiable"
        return max(size - suffix, 0), size - 1
    if not first.isdigit():
        return None
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    if not last:
        return start, size - 1
    if not last.isdigit():
        return None
    end = int(last)
    if end < start:
        return None
    return start, min(end, size - 1)


def _as_optional_str(value: Any, field: str, *, default: str | None) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str):
        raise AppError(400, "bad_request", f"字段 {field} 必须是字符串")
    return value


def _as_str(value: Any, field: str, *, default: str) -> str:
    result = _as_optional_str(value, field, default=default)
    if result is None:
        return default
    return result


def _as_int(value: Any, field: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppError(400, "bad_request", f"字段 {field} 必须是整数")
    return value


def _as_float(value: Any, field: str, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppError(400, "bad_request", f"字段 {field} 必须是数字")
    return float(value)


def _as_bool(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n", ""}:
            return False
    raise AppError(400, "bad_request", f"字段 {field} 必须是布尔值")


def _default_int(defaults: Mapping[str, Any], key: str, fallback: int) -> int:
    value = defaults.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _default_float(defaults: Mapping[str, Any], key: str, fallback: float) -> float:
    value = defaults.get(key)
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else fallback
    )


def _default_str(defaults: Mapping[str, Any], key: str, fallback: str) -> str:
    value = defaults.get(key)
    return value if isinstance(value, str) else fallback


def _build_job_options(payload: Mapping[str, Any], defaults: Mapping[str, Any]) -> Any:
    """Turn the ``POST /api/jobs`` JSON body into a ``jobs.JobOptions``."""
    try:
        from .jobs import JobOptions
    except ImportError as exc:  # pragma: no cover - jobs.py lands in parallel
        raise AppError(
            500, "jobs_unavailable", "任务层尚未就绪（webapp/jobs.py 不可用），请稍后重试"
        ) from exc

    source = _as_optional_str(payload.get("source"), "source", default=None)
    if not source:
        source = _as_optional_str(defaults.get("source"), "source", default=None)
    if not source:
        raise AppError(400, "bad_request", "缺少片源路径 source")

    highlights = payload.get("highlights")
    if not isinstance(highlights, str) or not highlights.strip():
        raise AppError(400, "bad_request", "缺少高光列表 highlights")

    top = _as_int(payload.get("top"), "top", default=_default_int(defaults, "top", 5))
    if top < 0:
        raise AppError(400, "bad_request", "top 必须 >= 0（0 表示全部）")
    sort = _as_str(payload.get("sort"), "sort", default=_default_str(defaults, "sort", "hits"))
    if sort not in {"hits", "duration", "start"}:
        raise AppError(400, "bad_request", "sort 只能是 hits / duration / start")

    lead = _as_float(payload.get("lead"), "lead", default=_default_float(defaults, "lead", 1.0))
    tail = _as_float(payload.get("tail"), "tail", default=_default_float(defaults, "tail", 1.5))
    if lead < 0 or tail < 0:
        raise AppError(400, "bad_request", "lead / tail 不能为负数")

    place = _as_optional_str(
        payload.get("place"), "place", default=_default_str(defaults, "place", "宁波 ECHO Tennis")
    )
    timezone = _as_str(
        payload.get("timezone"),
        "timezone",
        default=_default_str(defaults, "timezone", "Asia/Shanghai"),
    )
    if not timezone.strip():
        raise AppError(400, "bad_request", "时区不能为空")
    unit = _as_str(payload.get("unit"), "unit", default=_default_str(defaults, "unit", "拍"))
    source_label = _as_str(
        payload.get("source_label"),
        "source_label",
        default=_default_str(defaults, "source_label", "片源"),
    )
    quality = _as_str(
        payload.get("quality"),
        "quality",
        default=_default_str(defaults, "quality", "preview"),
    )
    if quality not in {"preview", "high"}:
        raise AppError(400, "bad_request", "quality 只能是 preview / high")

    return JobOptions(
        source=source,
        highlights=highlights,
        top=top,
        sort=sort,
        lead=lead,
        tail=tail,
        place=place,
        timezone=timezone,
        unit=unit,
        source_label=source_label,
        labels=_as_bool(payload.get("labels"), "labels", default=True),
        keep_clips=_as_bool(payload.get("keep_clips"), "keep_clips", default=True),
        plain_reel=_as_bool(payload.get("plain_reel"), "plain_reel", default=False),
        browser_preview=_as_bool(payload.get("browser_preview"), "browser_preview", default=True),
        quality=quality,
        note=_as_str(payload.get("note"), "note", default=""),
    )


def _probe_warnings(source: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    streams = source.get("audio_streams")
    if isinstance(streams, list):
        codecs = {
            str(item.get("codec"))
            for item in streams
            if isinstance(item, Mapping) and item.get("codec") is not None
        }
        if "apac" in codecs:
            warnings.append("源片含 ffmpeg 无法解码的空间音频轨（apac），将自动只用 AAC 立体声")
        elif len(streams) > 1:
            warnings.append("源片含多条音轨，将自动选用可解码的立体声轨")
    if source.get("has_audio") is False:
        warnings.append("源片没有音轨，合集将没有声音")
    return warnings


class WebAppServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the job manager and path configuration."""

    daemon_threads = True
    allow_reuse_address = True
    # Do not join handler threads on shutdown: keep-alive connections may still
    # be open and joining them can hang the interpreter exit.
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        runs_root: Path,
        demo_dir: Path | None = None,
        defaults: Mapping[str, Any] | None = None,
        static_dir: Path | None = None,
        manager: Any | None = None,
    ) -> None:
        self.runs_root = Path(runs_root)
        self.demo_dir = Path(demo_dir) if demo_dir is not None else None
        self.defaults: dict[str, Any] = dict(defaults or {})
        self.static_dir = (Path(static_dir) if static_dir is not None else STATIC_DIR).resolve()
        self.max_upload_bytes = MAX_UPLOAD_BYTES
        self._manager = manager
        self._manager_lock = threading.Lock()
        super().__init__(server_address, WebAppHandler)

    @property
    def uploads_dir(self) -> Path:
        """Fallback upload directory; the handler prefers ``manager.runs_root``."""
        return self.runs_root / "uploads"

    def get_manager(self) -> Any:
        """Return the job manager, creating it lazily on first use."""
        with self._manager_lock:
            if self._manager is None:
                from .jobs import JobManager  # local import: keeps app.py importable alone

                self._manager = JobManager(
                    runs_root=self.runs_root,
                    demo_dir=self.demo_dir,
                    # jobs.py treats None as "use built-in defaults"; {} would override them.
                    defaults=self.defaults or None,
                )
            return self._manager


class WebAppHandler(BaseHTTPRequestHandler):
    """Routes §2 of ``docs/webapp.md`` on top of :class:`WebAppServer`."""

    protocol_version = "HTTP/1.1"
    server_version = f"tennis-evolution-webapp/{__version__}"
    sys_version = ""
    server: WebAppServer
    _body_read: bool = False

    # ------------------------------------------------------------------ verbs

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name
        logger.debug("%s - %s", self.address_string(), format % args)

    # -------------------------------------------------------------- dispatch

    def _dispatch(self, method: str) -> None:
        self._body_read = False
        try:
            path = self._request_path()
            self._route(method, path)
        except AppError as exc:
            self._consume_request_body()
            self._send_error_json(exc)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # noqa: BLE001 - never let a handler kill the server
            logger.exception("unhandled error for %s %s", method, self.path)
            self._consume_request_body()
            self._send_error_json(AppError(500, "internal_error", "服务器内部错误，请查看终端日志"))

    def _request_path(self) -> str:
        parts = urlsplit(self.path)
        return unquote(parts.path)

    def _route(self, method: str, path: str) -> None:
        if path in {"/", "/index.html"}:
            self._require_method(method, "GET", "HEAD")
            self._handle_static("index.html", head=method == "HEAD")
            return
        if path.startswith("/static/"):
            self._require_method(method, "GET", "HEAD")
            self._handle_static(path[len("/static/") :], head=method == "HEAD")
            return
        if path == "/api/health":
            self._require_method(method, "GET")
            self._handle_health()
            return
        if path == "/api/demo":
            self._require_method(method, "GET")
            self._handle_demo()
            return
        if path == "/api/probe":
            self._require_method(method, "POST")
            self._handle_probe()
            return
        if path == "/api/highlights":
            self._require_method(method, "POST")
            self._handle_highlights()
            return
        if path == "/api/jobs":
            if method == "GET":
                self._handle_list_jobs()
                return
            if method == "POST":
                self._handle_create_job()
                return
            self._require_method(method, "GET", "POST")
        if path.startswith("/api/jobs/"):
            self._route_job(method, path[len("/api/jobs/") :])
            return
        if path == "/api/uploads" or path.startswith("/api/uploads/"):
            self._require_method(method, "PUT")
            filename = path[len("/api/uploads/") :] if path.startswith("/api/uploads/") else ""
            self._handle_upload(filename)
            return
        if path.startswith("/files/"):
            self._require_method(method, "GET", "HEAD")
            self._handle_job_file(method, path[len("/files/") :])
            return
        if path.startswith("/demo-files/"):
            self._require_method(method, "GET", "HEAD")
            self._handle_demo_file(method, path[len("/demo-files/") :])
            return
        raise AppError(404, "not_found", f"路径不存在：{path}")

    def _route_job(self, method: str, rest: str) -> None:
        job_id, _, suffix = rest.partition("/")
        if not _valid_job_id(job_id):
            raise AppError(404, "job_not_found", f"任务不存在：{job_id or rest}")
        if suffix == "":
            if method == "GET":
                self._handle_get_job(job_id)
                return
            if method == "DELETE":
                self._handle_delete_job(job_id)
                return
            self._require_method(method, "GET", "DELETE")
        if suffix == "cancel":
            self._require_method(method, "POST")
            self._handle_cancel_job(job_id)
            return
        raise AppError(404, "not_found", f"路径不存在：/api/jobs/{rest}")

    def _require_method(self, method: str, *allowed: str) -> None:
        if method in allowed:
            return
        allow = ", ".join(allowed)
        raise AppError(
            405,
            "method_not_allowed",
            f"{method} 不支持该路径，允许：{allow}",
            headers={"Allow": allow},
        )

    # ------------------------------------------------------------ negotiation

    def _content_length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise AppError(400, "bad_request", "Content-Length 不是合法整数") from exc
        if value < 0:
            raise AppError(400, "bad_request", "Content-Length 不能为负数")
        return value

    def _content_length_safe(self) -> int | None:
        try:
            return self._content_length()
        except AppError:
            return None

    def _read_json_object(self) -> dict[str, Any]:
        length = self._content_length()
        if length is None:
            self.close_connection = True
            raise AppError(411, "length_required", "请求必须带 Content-Length")
        if length > MAX_JSON_BODY_BYTES:
            self.close_connection = True
            raise AppError(
                413,
                "payload_too_large",
                f"请求体过大（上限 {MAX_JSON_BODY_BYTES // (1024 * 1024)} MiB）",
            )
        raw = self.rfile.read(length) if length else b""
        self._body_read = True
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(400, "bad_request", "请求体不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise AppError(400, "bad_request", "请求体必须是 JSON 对象")
        return payload

    def _consume_request_body(self) -> None:
        """Drain an unread request body so a keep-alive connection stays usable."""
        if self._body_read:
            return
        self._body_read = True
        if self.close_connection:
            return
        length = self._content_length_safe()
        if not length:
            return
        if length > COPY_CHUNK_SIZE:
            # Too large to drain cheaply; drop the connection instead.
            self.close_connection = True
            return
        try:
            self.rfile.read(length)
        except OSError:
            self.close_connection = True

    # ------------------------------------------------------------- responses

    def _respond(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        head: bool = False,
        extra_headers: Mapping[str, str] | None = None,
        cache_control: str = "no-store",
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if not head and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_json(
        self,
        status: int,
        payload: Mapping[str, Any],
        *,
        head: bool = False,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._respond(
            status,
            body,
            "application/json; charset=utf-8",
            head=head,
            extra_headers=extra_headers,
        )

    def _send_error_json(self, error: AppError) -> None:
        body = json.dumps(
            {"ok": False, "error": {"code": error.code, "message": error.message}},
            ensure_ascii=False,
        ).encode("utf-8")
        self._respond(
            error.status,
            body,
            "application/json; charset=utf-8",
            extra_headers=error.headers,
        )

    # ---------------------------------------------------------------- statics

    def _handle_static(self, relpath: str, *, head: bool) -> None:
        target = _resolve_under(self.server.static_dir, relpath)
        if target is None:
            raise AppError(404, "not_found", f"静态资源不存在：{relpath}")
        try:
            body = target.read_bytes()
        except OSError as exc:
            raise AppError(500, "static_read_failed", f"读取静态资源失败：{exc}") from exc
        self._respond(
            200,
            body,
            _guess_content_type(target),
            head=head,
            cache_control="no-cache",
        )

    # ------------------------------------------------------------------ files

    def _handle_job_file(self, method: str, rest: str) -> None:
        job_id, _, relpath = rest.partition("/")
        if not _valid_job_id(job_id):
            raise AppError(404, "file_not_found", "任务文件名非法")
        cleaned = _clean_relpath(relpath)
        if cleaned is None:
            raise AppError(404, "file_not_found", "任务文件路径非法")
        manager = self._manager()
        try:
            target = manager.resolve_file(job_id, cleaned)
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("解析任务产物路径失败", exc) from exc
        if target is None:
            raise AppError(404, "file_not_found", f"任务产物不存在：{job_id}/{cleaned}")
        self._serve_path(Path(target), head=method == "HEAD")

    def _handle_demo_file(self, method: str, relpath: str) -> None:
        cleaned = _clean_relpath(relpath)
        if cleaned is None:
            raise AppError(404, "file_not_found", "demo 文件路径非法")
        target = self._resolve_demo_file(cleaned)
        if target is None:
            raise AppError(404, "file_not_found", f"demo 产物不存在：{cleaned}")
        self._serve_path(target, head=method == "HEAD")

    def _resolve_demo_file(self, relpath: str) -> Path | None:
        try:
            manager = self.server.get_manager()
        except ImportError:
            return _resolve_under(self.server.demo_dir, relpath)
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("初始化任务层失败", exc) from exc
        try:
            target = manager.demo_file(relpath)
        except (AttributeError, ImportError):
            return _resolve_under(self.server.demo_dir, relpath)
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("解析 demo 产物路径失败", exc) from exc
        if target is None:
            return _resolve_under(self.server.demo_dir, relpath)
        return Path(target)

    def _serve_path(self, path: Path, *, head: bool) -> None:
        try:
            size = int(path.stat().st_size)
        except OSError as exc:
            raise AppError(404, "file_not_found", "文件不存在或不可读") from exc

        content_type = _guess_content_type(path)
        headers: dict[str, str] = {"Accept-Ranges": "bytes"}
        range_header = self.headers.get("Range")
        parsed = _parse_range(range_header, size) if range_header else None
        if parsed == "unsatisfiable":
            body = json.dumps(
                {
                    "ok": False,
                    "error": {"code": "range_not_satisfiable", "message": "Range 超出文件大小"},
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self._respond(
                416,
                body,
                "application/json; charset=utf-8",
                head=head,
                extra_headers={"Content-Range": f"bytes */{size}"},
            )
            return
        if parsed is None:
            status, start, end = 200, 0, size - 1
        else:
            start, end = parsed
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = max(end - start + 1, 0)
        self._stream_file(path, status, start, length, content_type, headers, head=head)

    def _stream_file(
        self,
        path: Path,
        status: int,
        start: int,
        length: int,
        content_type: str,
        extra_headers: Mapping[str, str],
        *,
        head: bool,
    ) -> None:
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise AppError(404, "file_not_found", "文件不存在或不可读") from exc
        with handle:
            handle.seek(start)
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for key, value in extra_headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if head or length <= 0:
                    return
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(COPY_CHUNK_SIZE, remaining))
                    if not chunk:
                        self.close_connection = True
                        return
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    # ------------------------------------------------------------- api: health

    def _handle_health(self) -> None:
        manager = self._manager()
        try:
            health = manager.health()
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("环境自检失败", exc) from exc
        if not isinstance(health, Mapping):
            raise AppError(500, "internal_error", "任务层返回的自检信息格式非法")
        payload: dict[str, Any] = dict(health)
        payload["ok"] = True
        self._send_json(200, payload)

    # --------------------------------------------------------------- api: demo

    def _handle_demo(self) -> None:
        try:
            manager = self.server.get_manager()
        except ImportError:
            # The task layer is optional while it is still being written.
            self._send_json(200, {"ok": True, "available": False})
            return
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("初始化任务层失败", exc) from exc
        try:
            demo = manager.demo()
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("读取 demo 信息失败", exc) from exc
        if not isinstance(demo, Mapping):
            raise AppError(500, "internal_error", "任务层返回的 demo 信息格式非法")
        payload: dict[str, Any] = dict(demo)
        payload["ok"] = True
        payload.setdefault("available", False)
        self._send_json(200, payload)

    # -------------------------------------------------------------- api: probe

    def _handle_probe(self) -> None:
        payload = self._read_json_object()
        raw_path = payload.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise AppError(400, "bad_request", "请求体缺少 path 字段（本地视频绝对路径）")
        path = raw_path.strip()
        manager = self._manager()
        try:
            result = manager.probe_path(path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise AppError(404, "file_not_found", f"找不到文件：{path}") from exc
        except SystemExit as exc:  # media.probe signals a missing file this way
            message = str(exc) or f"找不到文件：{path}"
            if "not found" in message.lower():
                raise AppError(404, "file_not_found", f"找不到文件：{path}") from exc
            raise AppError(400, "not_media", f"不是可识别的视频文件：{message}") from exc
        except ValueError as exc:
            raise AppError(400, "not_media", f"不是可识别的视频文件：{exc}") from exc
        except RuntimeError as exc:  # ffprobe rejected the file (FfmpegError)
            raise AppError(
                400, "not_media", "无法读取视频信息，这个文件可能不是有效的视频"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("探测片源失败", exc) from exc

        response: dict[str, Any] = {"ok": True}
        if isinstance(result, Mapping) and "source" in result:
            response.update(dict(result))
        else:
            response["source"] = dict(result) if isinstance(result, Mapping) else {}
        source = response.get("source")
        if not isinstance(source, Mapping):
            source = {}
        preset = response.get("warnings")
        if not isinstance(preset, list):
            response["warnings"] = _probe_warnings(source)
        self._send_json(200, response)

    # --------------------------------------------------------- api: highlights

    def _handle_highlights(self) -> None:
        payload = self._read_json_object()
        text = payload.get("text")
        if text is None and "json" in payload:
            text = json.dumps(payload["json"], ensure_ascii=False)
        if not isinstance(text, str) or not text.strip():
            raise AppError(400, "bad_request", "请求体缺少高光文本（text 或 json 字段）")
        top = _as_int(payload.get("top"), "top", default=5)
        if top < 0:
            raise AppError(400, "bad_request", "top 必须 >= 0（0 表示全部）")
        sort = _as_str(payload.get("sort"), "sort", default="hits")
        if sort not in {"hits", "duration", "start"}:
            raise AppError(400, "bad_request", "sort 只能是 hits / duration / start")

        manager = self._manager()
        try:
            preview = manager.parse_preview(text, top, sort)
        except (ValueError, SystemExit) as exc:
            raise AppError(
                400,
                "bad_highlights",
                f"高光解析失败：{exc}。每行格式如「27:14 - 28:08  Long rally (40 hits)」",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("解析高光列表失败", exc) from exc
        if not isinstance(preview, Mapping):
            raise AppError(500, "internal_error", "任务层返回的高光预览格式非法")
        response: dict[str, Any] = dict(preview)
        response["ok"] = True
        self._send_json(200, response)

    # --------------------------------------------------------------- api: jobs

    def _handle_create_job(self) -> None:
        payload = self._read_json_object()
        options = _build_job_options(payload, self.server.defaults)
        manager = self._manager()
        try:
            summary = manager.create(options)
        except FileNotFoundError as exc:
            raise AppError(404, "file_not_found", str(exc)) from exc
        except ValueError as exc:
            raise AppError(400, "bad_request", f"任务参数不合法：{exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("创建任务失败", exc) from exc
        if not isinstance(summary, Mapping):
            raise AppError(500, "internal_error", "任务层返回的任务摘要格式非法")
        self._send_json(200, {"ok": True, "job": dict(summary)})

    def _handle_list_jobs(self) -> None:
        manager = self._manager()
        try:
            jobs = manager.list()
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("读取任务列表失败", exc) from exc
        items = list(jobs) if isinstance(jobs, list) else []
        self._send_json(200, {"ok": True, "jobs": items})

    def _handle_get_job(self, job_id: str) -> None:
        manager = self._manager()
        try:
            view = manager.get(job_id)
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("读取任务详情失败", exc) from exc
        if view is None:
            raise AppError(404, "job_not_found", f"任务不存在：{job_id}")
        if not isinstance(view, Mapping):
            raise AppError(500, "internal_error", "任务层返回的任务详情格式非法")
        # docs §2.6 "详情 = 完整 job view (§3.3)": flat fields plus ok.  The nested
        # "job" key mirrors POST /api/jobs (§2.5) so both client styles work.
        payload: dict[str, Any] = dict(view)
        payload["job"] = dict(view)
        payload["ok"] = True
        self._send_json(200, payload)

    def _handle_cancel_job(self, job_id: str) -> None:
        manager = self._manager()
        try:
            cancelled = manager.cancel(job_id)
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("取消任务失败", exc) from exc
        if not cancelled:
            raise AppError(404, "job_not_found", f"任务不存在或无法取消：{job_id}")
        self._send_json(200, {"ok": True, "cancelled": True})

    def _handle_delete_job(self, job_id: str) -> None:
        manager = self._manager()
        try:
            deleted = manager.delete(job_id)
        except RuntimeError as exc:
            raise AppError(409, "job_running", str(exc)) from exc
        except ValueError as exc:
            raise AppError(400, "bad_request", f"无法删除任务：{exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("删除任务失败", exc) from exc
        if not deleted:
            raise AppError(404, "job_not_found", f"任务不存在或无法删除：{job_id}")
        self._send_json(200, {"ok": True, "deleted": True})

    # -------------------------------------------------------------- api: upload

    def _handle_upload(self, filename: str) -> None:
        safe_name = _sanitize_filename(filename)
        if not safe_name:
            raise AppError(400, "bad_request", "上传文件名非法")
        length = self._content_length()
        if length is None:
            self.close_connection = True
            raise AppError(411, "length_required", "上传请求必须带 Content-Length")
        limit = self.server.max_upload_bytes
        if length > limit:
            self.close_connection = True
            raise AppError(
                413,
                "payload_too_large",
                f"文件超过上传上限 {_human_bytes(limit)}",
            )

        uploads = self._uploads_dir()
        try:
            uploads.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppError(500, "upload_failed", f"创建上传目录失败：{exc}") from exc
        target = uploads / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        partial = target.with_name(target.name + ".part")
        received = 0
        try:
            with partial.open("wb") as handle:
                while received < length:
                    chunk = self.rfile.read(min(COPY_CHUNK_SIZE, length - received))
                    if not chunk:
                        raise AppError(
                            400,
                            "upload_incomplete",
                            f"上传中断：只收到 {_human_bytes(received)}",
                        )
                    handle.write(chunk)
                    received += len(chunk)
                    if received > limit:
                        raise AppError(
                            413,
                            "payload_too_large",
                            f"文件超过上传上限 {_human_bytes(limit)}",
                        )
            partial.replace(target)
        except AppError:
            partial.unlink(missing_ok=True)
            self.close_connection = True
            raise
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise AppError(500, "upload_failed", f"写入上传文件失败：{exc}") from exc
        finally:
            self._body_read = True

        self._send_json(
            200,
            {"ok": True, "path": str(target.resolve()), "name": safe_name, "size": received},
        )

    # ----------------------------------------------------------------- helpers

    def _uploads_dir(self) -> Path:
        """Uploads always live next to the run directories owned by the manager."""
        manager = self._manager()
        runs_root = Path(getattr(manager, "runs_root", self.server.runs_root))
        return runs_root / "uploads"

    def _manager(self) -> Any:
        try:
            return self.server.get_manager()
        except ImportError as exc:
            raise AppError(
                500, "jobs_unavailable", "任务层尚未就绪（webapp/jobs.py 不可用），请稍后重试"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as JSON 500
            raise self._manager_error("初始化任务层失败", exc) from exc

    def _manager_error(self, action: str, exc: BaseException) -> AppError:
        logger.exception("%s: %s", action, exc)
        return AppError(500, "internal_error", f"{action}：{exc}")


def create_server(
    *,
    runs_root: Path | str | None = None,
    demo_dir: Path | str | None = None,
    defaults: Mapping[str, Any] | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    static_dir: Path | None = None,
    manager: Any | None = None,
) -> WebAppServer:
    """Bind and return a :class:`WebAppServer` ready for ``serve_forever()``.

    ``runs_root`` / ``demo_dir`` default to the task layer's own defaults.  The
    ``static_dir`` and ``manager`` arguments exist for tests; production callers
    leave them as ``None`` so the packaged ``static/`` folder and the real
    ``JobManager`` are used.
    """
    from .jobs import default_demo_dir, default_runs_root

    root = Path(runs_root).expanduser() if runs_root is not None else default_runs_root()
    root.mkdir(parents=True, exist_ok=True)
    if demo_dir is not None:
        resolved_demo: Path | None = Path(demo_dir).expanduser()
    else:
        fallback = default_demo_dir()
        resolved_demo = fallback if fallback is not None and fallback.is_dir() else None
    server = WebAppServer(
        (host, port),
        runs_root=root,
        demo_dir=resolved_demo,
        defaults=defaults,
        static_dir=static_dir,
        manager=manager,
    )
    actual_host, actual_port = server.server_address[:2]
    logger.info("webapp listening on http://%s:%s (runs_root=%s)", actual_host, actual_port, root)
    return server
