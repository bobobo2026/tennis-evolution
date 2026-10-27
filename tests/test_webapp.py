"""Acceptance tests for the local web UI (``docs/webapp.md`` §2/§3/§5).

The unit tests in this module never touch ffmpeg: they exercise highlight
parsing/preview, the job store, path safety, Range parsing and the JSON error
envelope. One ``@pytest.mark.integration`` test synthesises a 12 s lavfi clip,
starts a real server on an ephemeral port and drives the whole
``probe -> highlights -> jobs -> poll -> verify -> Range`` flow.

Nothing here documents the frontend; that lives in ``static/`` and is verified by
the browser pass of the QA run.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from http import client as http_client
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tennis_evolution.util import format_duration
from tennis_evolution.webapp import create_server
from tennis_evolution.webapp.app import (
    AppError,
    _build_job_options,
    _clean_relpath,
    _parse_range,
    _sanitize_filename,
    _valid_job_id,
)
from tennis_evolution.webapp.jobs import (
    MAX_UPLOAD_BYTES,
    STAGE_ORDER,
    JobManager,
    JobOptions,
)

SAMPLE_HIGHLIGHTS = (
    Path(__file__).resolve().parents[1] / "examples" / "highlights_sample.txt"
).read_text(encoding="utf-8")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

#: Keys ``docs/webapp.md`` §2.6 promises in a list item (the view may add more).
SUMMARY_REQUIRED = {
    "id",
    "status",
    "stage",
    "stage_label",
    "progress",
    "created_at",
    "elapsed",
    "source_name",
    "top",
    "quality",
    "ok",
    "error",
}

#: Keys ``docs/webapp.md`` §3.3 promises in the full job view.
VIEW_REQUIRED = {
    "id",
    "status",
    "stage",
    "stage_label",
    "progress",
    "created_at",
    "started_at",
    "finished_at",
    "elapsed",
    "options",
    "source",
    "selection",
    "logs",
    "error",
    "result",
}

#: Keys ``docs/webapp.md`` §2.4 promises in a highlight preview.
PREVIEW_REQUIRED = {"ok", "total", "sort", "top", "selected", "dropped", "summary"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _new_manager(root: Path) -> JobManager:
    """Manager isolated from the real ``demo_run/`` and ``webapp-runs/``."""
    return JobManager(runs_root=root / "runs", demo_dir=root / "demo")


class _HoldingManager(JobManager):
    """Manager whose worker parks before running the pipeline.

    Everything after validation (probe/cut/render) needs ffmpeg; for the job
    store lifecycle we only care about the queued state, so the worker blocks on
    an event instead. Released by the fixture teardown.
    """

    def __init__(self, root: Path) -> None:
        self.release = threading.Event()
        super().__init__(runs_root=root / "runs", demo_dir=root / "demo")

    def _execute(self, job: dict[str, Any]) -> None:
        self.release.wait(10)


@contextlib.contextmanager
def _running_server(root: Path) -> Iterator[tuple[Any, JobManager]]:
    manager = _new_manager(root)
    server = create_server(
        runs_root=root / "runs",
        demo_dir=root / "demo",
        port=0,
        manager=manager,
    )
    thread = threading.Thread(target=server.serve_forever, name="test-webapp", daemon=True)
    thread.start()
    try:
        yield server, manager
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _call(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    conn = http_client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        conn.close()


def _call_json(
    port: int,
    method: str,
    path: str,
    payload: Any | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    body = None
    actual = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        actual.setdefault("Content-Type", "application/json")
    status, response_headers, raw = _call(port, method, path, body=body, headers=actual)
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover - assertion aid
        raise AssertionError(f"{method} {path} did not return JSON: {raw[:200]!r}") from exc
    return status, response_headers, decoded


def _is_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _assert_error_envelope(status: int, payload: dict[str, Any], expected_status: int) -> None:
    assert status == expected_status
    assert payload["ok"] is False
    assert set(payload["error"]) == {"code", "message"}
    assert isinstance(payload["error"]["code"], str) and payload["error"]["code"]
    assert _is_chinese(payload["error"]["message"]), payload["error"]["message"]


def _dummy_source(path: Path) -> Path:
    path.write_bytes(b"not a real video")
    return path


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def manager(tmp_path_factory: pytest.TempPathFactory) -> JobManager:
    root = tmp_path_factory.mktemp("webapp-unit")
    return _new_manager(root)


@pytest.fixture()
def holding(tmp_path: Path) -> Iterator[_HoldingManager]:
    manager = _HoldingManager(tmp_path)
    try:
        yield manager
    finally:
        manager.release.set()


@pytest.fixture(scope="module")
def web(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SimpleNamespace]:
    root = tmp_path_factory.mktemp("webapp-http")
    demo = root / "demo"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "note.txt").write_text("demo file\n", encoding="utf-8")
    (demo / "range.bin").write_bytes(bytes(range(250)) * 4)  # 1000 bytes for Range tests
    with _running_server(root) as (server, manager):
        yield SimpleNamespace(
            port=server.server_address[1],
            server=server,
            manager=manager,
            root=root,
            demo=demo,
        )


# --------------------------------------------------------------------------- #
# highlight parsing / preview (docs §2.4)
# --------------------------------------------------------------------------- #


def test_parse_preview_text_ranks_by_hits(manager: JobManager) -> None:
    preview = manager.parse_preview(SAMPLE_HIGHLIGHTS, top=3, sort="hits")

    assert PREVIEW_REQUIRED <= set(preview)
    assert preview["ok"] is True
    assert preview["total"] == 10
    assert preview["top"] == 3
    assert preview["sort"] == "hits"

    selected = preview["selected"]
    assert [item["hits"] for item in selected] == [40, 36, 27]
    assert [item["index"] for item in selected] == [1, 2, 3]
    assert selected[0]["start_hms"] == "27:14"
    assert selected[0]["end_hms"] == "28:08"
    assert selected[0]["duration"] == pytest.approx(54.0)
    assert selected[0]["duration_hms"] == "0:54"
    assert selected[0]["label"] == "40 hits"
    for item in selected:
        assert {
            "index",
            "start",
            "end",
            "start_hms",
            "end_hms",
            "hits",
            "duration",
            "duration_hms",
            "label",
        } <= set(item)
    assert len(preview["dropped"]) == 7

    padded = sum(item["duration"] + 1.0 + 1.5 for item in selected)
    assert preview["summary"]["candidate_duration"] == pytest.approx(268.0, abs=0.1)
    assert preview["summary"]["with_padding_hms"] == format_duration(padded)
    assert preview["summary"]["estimated_output_hms"] == format_duration(padded)


def test_parse_preview_sort_start_and_all(manager: JobManager) -> None:
    preview = manager.parse_preview(SAMPLE_HIGHLIGHTS, top=0, sort="start")
    starts = [item["start"] for item in preview["selected"]]
    assert starts == sorted(starts)
    assert len(preview["selected"]) == 10
    assert preview["dropped"] == []
    assert preview["summary"]["candidate_duration_hms"] == "4:28"

    duration_sorted = manager.parse_preview(SAMPLE_HIGHLIGHTS, top=3, sort="duration")
    durations = [item["duration"] for item in duration_sorted["selected"]]
    assert durations == sorted(durations, reverse=True)


def test_parse_preview_top_beyond_total(manager: JobManager) -> None:
    preview = manager.parse_preview(SAMPLE_HIGHLIGHTS, top=99, sort="hits")
    assert len(preview["selected"]) == 10
    assert preview["dropped"] == []


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(
            {
                "segments": [
                    {"start": "0:10", "end": "0:20", "hits": 6},
                    {"start": "0:30", "end": "0:50", "hits": 12},
                ]
            }
        ),
        json.dumps(
            [
                {"start": 10, "end": 20, "hits": 6},
                {"start": 30, "end": 50, "hits": 12},
            ]
        ),
    ],
)
def test_parse_preview_accepts_json_payloads(manager: JobManager, text: str) -> None:
    preview = manager.parse_preview(text, top=1, sort="hits")
    assert preview["total"] == 2
    assert [item["hits"] for item in preview["selected"]] == [12]
    assert len(preview["dropped"]) == 1


@pytest.mark.parametrize("text", ["", "   ", "hello world", "no time ranges here"])
def test_parse_preview_rejects_unparseable_text(manager: JobManager, text: str) -> None:
    with pytest.raises(ValueError):
        manager.parse_preview(text, top=5, sort="hits")


def test_parse_preview_rejects_bad_sort(manager: JobManager) -> None:
    with pytest.raises(ValueError):
        manager.parse_preview(SAMPLE_HIGHLIGHTS, top=5, sort="random")


# --------------------------------------------------------------------------- #
# job options / create() validation (docs §2.5, §3.1)
# --------------------------------------------------------------------------- #


def test_job_options_roundtrip_and_unknown_keys() -> None:
    options = JobOptions.from_dict(
        {"source": "/tmp/a.mp4", "highlights": "0:01 - 0:02 (3 hits)", "top": 1, "bogus": 1}
    )
    assert options.top == 1
    assert set(options.to_dict()) == {field for field in JobOptions.__dataclass_fields__}


def test_create_rejects_empty_source(manager: JobManager) -> None:
    with pytest.raises(ValueError, match="片源路径"):
        manager.create(JobOptions(source="  ", highlights=SAMPLE_HIGHLIGHTS))


def test_create_rejects_missing_source(manager: JobManager, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        manager.create(JobOptions(source=str(tmp_path / "nope.mp4"), highlights=SAMPLE_HIGHLIGHTS))


def test_create_rejects_directory_source(manager: JobManager, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="必须是一个文件"):
        manager.create(JobOptions(source=str(tmp_path), highlights=SAMPLE_HIGHLIGHTS))


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"top": -1}, "top|保留条数"),
        ({"sort": "oldest"}, "排序"),
        ({"quality": "ultra"}, "画质"),
        ({"timezone": "Mars/Olympus"}, "时区"),
        ({"lead": 31.0}, "留白"),
        ({"tail": -0.5}, "留白"),
        ({"highlights": ""}, "回合|高光"),
        ({"highlights": "totally not a highlight list"}, "回合|高光"),
        ({"labels": False, "plain_reel": False}, "合集"),
    ],
)
def test_create_rejects_invalid_options(
    manager: JobManager, tmp_path: Path, overrides: dict[str, Any], needle: str
) -> None:
    source = _dummy_source(tmp_path / "source.mp4")
    payload: dict[str, Any] = {"source": str(source), "highlights": SAMPLE_HIGHLIGHTS}
    payload.update(overrides)
    with pytest.raises(ValueError, match=needle):
        manager.create(JobOptions.from_dict(payload))


def test_create_validates_before_queueing(manager: JobManager, tmp_path: Path) -> None:
    source = _dummy_source(tmp_path / "source.mp4")
    before = manager.list()
    with pytest.raises(ValueError):
        manager.create(JobOptions(source=str(source), highlights=""))
    assert manager.list() == before, "rejected options must not create a job directory"
    assert list(manager.runs_root.iterdir()) == [], "no run directory for rejected options"


# --------------------------------------------------------------------------- #
# job store lifecycle / views (docs §3.2, §3.3, §2.6)
# --------------------------------------------------------------------------- #


def test_job_store_create_get_cancel_delete(holding: _HoldingManager, tmp_path: Path) -> None:
    source = _dummy_source(tmp_path / "source.mp4")
    summary = holding.create(JobOptions(source=str(source), highlights=SAMPLE_HIGHLIGHTS, top=3))

    assert SUMMARY_REQUIRED <= set(summary)
    assert summary["status"] == "queued"
    assert summary["stage"] == "queued"
    assert summary["progress"] == 0.0
    assert summary["quality"] == "preview"
    assert summary["top"] == 3
    assert summary["source_name"] == "source.mp4"
    assert summary["error"] is None
    job_id = summary["id"]
    assert (tmp_path / "runs" / job_id / "job.json").is_file()

    view = holding.get(job_id)
    assert view is not None
    assert VIEW_REQUIRED <= set(view)
    assert view["status"] == "queued"
    assert view["options"]["source"] == str(source)
    assert view["options"]["place"] == "宁波 ECHO Tennis"
    assert set(view["options"]) == set(JobOptions.__dataclass_fields__)
    assert view["source"] is None
    assert view["selection"] == []
    assert view["result"] is None
    assert view["logs"] and view["logs"][0]["text"]
    assert set(view["logs"][0]) == {"t", "level", "text"}
    assert view["stages"] == list(STAGE_ORDER)

    assert holding.cancel(job_id) is True
    cancelled = holding.get(job_id)
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "任务已取消"
    assert holding.cancel(job_id) is False  # already terminal

    assert holding.delete(job_id) is True
    assert holding.get(job_id) is None
    assert not (tmp_path / "runs" / job_id).exists()
    assert holding.delete(job_id) is False


def test_job_store_delete_requires_terminal_state(holding: _HoldingManager, tmp_path: Path) -> None:
    source = _dummy_source(tmp_path / "source.mp4")
    job_id = holding.create(JobOptions(source=str(source), highlights=SAMPLE_HIGHLIGHTS))["id"]
    # A queued/running job must not be deleted (the HTTP layer maps this to 409).
    with pytest.raises(RuntimeError, match="还在运行"):
        holding.delete(job_id)
    holding.cancel(job_id)
    assert holding.delete(job_id) is True


def test_job_store_reloads_terminal_jobs(tmp_path: Path) -> None:
    root = tmp_path
    runs = root / "runs" / "20260101-000000-dead"
    runs.mkdir(parents=True)
    (runs / "job.json").write_text(
        json.dumps(
            {
                "id": "20260101-000000-dead",
                "status": "done",
                "stage": "done",
                "stage_label": "完成",
                "progress": 1.0,
                "created_at": "2026-01-01T00:00:00+08:00",
                "options": {"source": "/tmp/a.mp4", "top": 1, "quality": "preview"},
                "result": {"ok": True},
                "logs": [],
            }
        ),
        encoding="utf-8",
    )
    manager = _new_manager(root)
    view = manager.get("20260101-000000-dead")
    assert view is not None
    assert view["status"] == "done"
    assert view["result"] == {"ok": True}
    # A queued record from a dead process must be reported as failed, not wedged.
    dead = root / "runs" / "20260101-000001-dead"
    dead.mkdir(parents=True)
    (dead / "job.json").write_text(
        json.dumps({"id": "20260101-000001-dead", "status": "queued", "options": {}}),
        encoding="utf-8",
    )
    refreshed = _new_manager(root)
    view = refreshed.get("20260101-000001-dead")
    assert view is not None
    assert view["status"] == "failed"
    assert "服务重启" in view["error"]


# --------------------------------------------------------------------------- #
# path safety (docs §2.8, §2.7)
# --------------------------------------------------------------------------- #


def test_resolve_file_confines_to_job_dir(tmp_path: Path) -> None:
    manager = _new_manager(tmp_path)
    job_dir = tmp_path / "runs" / "20260101-000000-abcd"
    job_dir.mkdir(parents=True)
    inside = job_dir / "clip.mp4"
    inside.write_bytes(b"x")
    secret = tmp_path / "secret.txt"
    secret.write_text("outside", encoding="utf-8")
    (job_dir / "escape.txt").symlink_to(secret)

    assert manager.resolve_file(job_dir.name, "clip.mp4") == inside
    for relpath in ("../secret.txt", "../../etc/passwd", "/etc/passwd", "sub/../../secret.txt"):
        assert manager.resolve_file(job_dir.name, relpath) is None, relpath
    assert manager.resolve_file(job_dir.name, "escape.txt") is None, (
        "symlink escape must be rejected"
    )
    assert manager.resolve_file("missing-job", "clip.mp4") is None
    assert manager.resolve_file(job_dir.name, ".") is None


def test_demo_file_confines_to_demo_dir(tmp_path: Path) -> None:
    manager = _new_manager(tmp_path)
    demo = tmp_path / "demo"
    demo.mkdir()
    artifact = demo / "reel_labeled.mp4"
    artifact.write_bytes(b"x")
    secret = tmp_path / "secret.txt"
    secret.write_text("outside", encoding="utf-8")
    (demo / "escape.txt").symlink_to(secret)

    assert manager.demo_file("reel_labeled.mp4") == artifact
    for relpath in ("../secret.txt", "/etc/passwd", "..", "escape.txt"):
        assert manager.demo_file(relpath) is None, relpath

    missing = JobManager(runs_root=tmp_path / "runs2", demo_dir=tmp_path / "no-demo")
    assert missing.demo_file("reel_labeled.mp4") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a/b.mp4", "a/b.mp4"),
        ("a//b.mp4", "a/b.mp4"),
        ("./a/b.mp4", "a/b.mp4"),
        ("a/./b.mp4", "a/b.mp4"),
        ("", None),
        ("/etc/passwd", None),
        ("C:/windows/x.mp4", None),
        ("a/../../b.mp4", None),
        ("..", None),
        ("a\x00b.mp4", None),
        ("\\\\server\\share\\x.mp4", None),
    ],
)
def test_clean_relpath(raw: str, expected: str | None) -> None:
    assert _clean_relpath(raw) == expected


@pytest.mark.parametrize(
    ("job_id", "valid"),
    [
        ("20261026-2231-4f2a", True),
        ("job_1.2", True),
        ("..", False),
        ("../etc", False),
        ("a/b", False),
        ("", False),
        (".hidden", False),
        ("a" * 129, False),
    ],
)
def test_valid_job_id(job_id: str, valid: bool) -> None:
    assert _valid_job_id(job_id) is valid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IMG_5155.MOV", "IMG_5155.MOV"),
        ("中文 名称.mp4", "中文 名称.mp4"),
        ("a\\b:c.mp4", "a_b_c.mp4"),
        ("../evil.mp4", "_evil.mp4"),  # separators and leading dots are gone
        ("", ""),
        ("..", ""),
        ("a\x00b.mp4", "a_b.mp4"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    cleaned = _sanitize_filename(raw)
    assert cleaned == expected
    assert "/" not in cleaned and "\\" not in cleaned


# --------------------------------------------------------------------------- #
# Range parsing (docs §2.8)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-1023", 5000, (0, 1023)),
        ("bytes=100-", 5000, (100, 4999)),
        ("bytes=-500", 5000, (4500, 4999)),
        ("bytes=0-999999", 5000, (0, 4999)),
        ("bytes=4999-", 5000, (4999, 4999)),
        ("bytes=5000-", 5000, "unsatisfiable"),
        ("bytes=-0", 5000, "unsatisfiable"),
        ("bytes=-1", 0, "unsatisfiable"),
        ("bytes=0-", 0, "unsatisfiable"),
        ("bytes=10-5", 5000, None),
        ("bytes=abc", 5000, None),
        ("bytes=0-1,2-3", 5000, None),
        ("items=0-1", 5000, None),
        ("bytes=1.5-2", 5000, None),
        ("", 5000, None),
    ],
)
def test_parse_range(header: str, size: int, expected: Any) -> None:
    assert _parse_range(header, size) == expected


# --------------------------------------------------------------------------- #
# HTTP layer: error envelope, Range, uploads, keep-alive (docs §2)
# --------------------------------------------------------------------------- #


def test_index_and_static_served(web: SimpleNamespace) -> None:
    status, headers, body = _call(web.port, "GET", "/")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert "网球进化" in body.decode("utf-8")
    assert headers["cache-control"] == "no-cache"

    status, headers, body = _call(web.port, "GET", "/static/app.js")
    assert status == 200
    assert headers["content-type"].startswith("text/javascript")
    assert body

    status, headers, body = _call(web.port, "HEAD", "/")
    assert status == 200 and body == b""
    assert int(headers["content-length"]) > 0


def test_unknown_routes_return_json_404(web: SimpleNamespace) -> None:
    for path in ("/api/nope", "/files/", "/demo-files/", "/static/../app.py", "/files/..%2Fapp.py"):
        status, headers, payload = _call_json(web.port, "GET", path)
        assert headers["content-type"].startswith("application/json")
        assert payload["ok"] is False
        assert set(payload["error"]) == {"code", "message"}


def test_method_not_allowed_returns_405(web: SimpleNamespace) -> None:
    status, headers, payload = _call_json(web.port, "POST", "/api/health")
    assert status == 405
    assert headers["allow"] == "GET"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "method_not_allowed"
    assert _is_chinese(payload["error"]["message"])


def test_health_and_demo_shapes(web: SimpleNamespace) -> None:
    status, _, payload = _call_json(web.port, "GET", "/api/health")
    assert status == 200
    assert payload["ok"] is True
    assert payload["runs_root"] == str(web.manager.runs_root)
    assert set(payload["tools"]) >= {"ffmpeg", "ffprobe", "ok"}
    assert payload["limits"]["max_upload_bytes"] == 5 * 1024**3
    assert MAX_UPLOAD_BYTES == payload["limits"]["max_upload_bytes"]
    assert set(payload["defaults"]) >= {"source", "place", "timezone", "top", "quality"}
    assert payload["demo"] == {"available": False}

    status, _, demo = _call_json(web.port, "GET", "/api/demo")
    assert status == 200
    assert demo == {"ok": True, "available": False}


def test_demo_files_served_and_confined(web: SimpleNamespace) -> None:
    status, headers, body = _call(web.port, "GET", "/demo-files/note.txt")
    assert status == 200
    assert body == b"demo file\n"
    assert headers["accept-ranges"] == "bytes"

    status, headers, payload = _call_json(web.port, "GET", "/demo-files/../runs/x.txt")
    assert status == 404
    assert payload["error"]["code"] == "file_not_found"


def test_job_not_found_paths(web: SimpleNamespace) -> None:
    for method, path in (
        ("GET", "/api/jobs/does-not-exist"),
        ("DELETE", "/api/jobs/does-not-exist"),
        ("POST", "/api/jobs/does-not-exist/cancel"),
    ):
        status, _, payload = _call_json(web.port, method, path)
        assert status == 404
        assert payload["error"]["code"] == "job_not_found"

    status, _, payload = _call_json(web.port, "GET", "/api/jobs")
    assert status == 200
    assert payload["ok"] is True and payload["jobs"] == []


def test_probe_error_branches(web: SimpleNamespace, tmp_path: Path) -> None:
    status, _, payload = _call_json(web.port, "POST", "/api/probe", {"path": ""})
    _assert_error_envelope(status, payload, 400)

    status, _, payload = _call_json(web.port, "POST", "/api/probe", {})
    _assert_error_envelope(status, payload, 400)

    status, _, payload = _call_json(
        web.port, "POST", "/api/probe", {"path": str(tmp_path / "missing.mp4")}
    )
    _assert_error_envelope(status, payload, 404)
    assert payload["error"]["code"] == "file_not_found"

    junk = tmp_path / "junk.txt"
    junk.write_text("not media", encoding="utf-8")
    status, _, payload = _call_json(web.port, "POST", "/api/probe", {"path": str(junk)})
    _assert_error_envelope(status, payload, 400)
    assert payload["error"]["code"] == "not_media"


def test_highlights_endpoint(web: SimpleNamespace) -> None:
    status, _, payload = _call_json(
        web.port, "POST", "/api/highlights", {"text": SAMPLE_HIGHLIGHTS, "top": 2}
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["total"] == 10
    assert [item["hits"] for item in payload["selected"]] == [40, 36]

    status, _, payload = _call_json(
        web.port,
        "POST",
        "/api/highlights",
        {"text": SAMPLE_HIGHLIGHTS, "top": 2, "sort": "start"},
    )
    assert status == 200
    assert [item["hits"] for item in payload["selected"]] == [3, 5]

    status, _, payload = _call_json(web.port, "POST", "/api/highlights", {"text": ""})
    _assert_error_envelope(status, payload, 400)

    status, _, payload = _call_json(web.port, "POST", "/api/highlights", {"text": "nothing here"})
    _assert_error_envelope(status, payload, 400)
    assert payload["error"]["code"] == "bad_highlights"

    status, _, payload = _call_json(
        web.port, "POST", "/api/highlights", {"text": SAMPLE_HIGHLIGHTS, "top": -1}
    )
    _assert_error_envelope(status, payload, 400)

    status, _, raw = _call(
        web.port,
        "POST",
        "/api/highlights",
        body=b"{oops",
        headers={"Content-Type": "application/json"},
    )
    assert status == 400  # malformed body -> JSON error, not a crash
    payload = json.loads(raw.decode("utf-8"))
    _assert_error_envelope(status, payload, 400)


def test_create_job_error_branches(web: SimpleNamespace, tmp_path: Path) -> None:
    status, _, payload = _call_json(
        web.port, "POST", "/api/jobs", {"highlights": SAMPLE_HIGHLIGHTS}
    )
    _assert_error_envelope(status, payload, 400)

    status, _, payload = _call_json(
        web.port,
        "POST",
        "/api/jobs",
        {"source": str(tmp_path / "missing.mp4"), "highlights": SAMPLE_HIGHLIGHTS},
    )
    _assert_error_envelope(status, payload, 404)
    assert payload["error"]["code"] == "file_not_found"

    source = _dummy_source(tmp_path / "source.mp4")
    status, _, payload = _call_json(
        web.port,
        "POST",
        "/api/jobs",
        {"source": str(source), "highlights": SAMPLE_HIGHLIGHTS, "top": -1},
    )
    _assert_error_envelope(status, payload, 400)

    status, _, payload = _call_json(
        web.port,
        "POST",
        "/api/jobs",
        {"source": str(source), "highlights": SAMPLE_HIGHLIGHTS, "sort": "oldest"},
    )
    _assert_error_envelope(status, payload, 400)

    status, _, payload = _call_json(
        web.port,
        "POST",
        "/api/jobs",
        {"source": str(source), "highlights": SAMPLE_HIGHLIGHTS, "quality": "ultra"},
    )
    _assert_error_envelope(status, payload, 400)
    assert web.manager.list() == [], "failed validation must not create a job"


def test_build_job_options_uses_server_defaults() -> None:
    defaults = {"source": "/tmp/default.mp4", "top": 2, "quality": "high"}
    options = _build_job_options({"highlights": SAMPLE_HIGHLIGHTS}, defaults)
    assert options.source == "/tmp/default.mp4"
    assert options.top == 2
    assert options.quality == "high"
    assert options.timezone == "Asia/Shanghai"
    with pytest.raises(AppError) as error:
        _build_job_options({"highlights": SAMPLE_HIGHLIGHTS}, {})
    assert error.value.status == 400 and error.value.code == "bad_request"


def test_file_range_requests(web: SimpleNamespace) -> None:
    path = "/demo-files/range.bin"
    status, headers, full = _call(web.port, "GET", path)
    assert status == 200
    size = int(headers["content-length"])
    assert size == len(full) == 1000
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "application/octet-stream"

    status, headers, part = _call(web.port, "GET", path, headers={"Range": "bytes=0-99"})
    assert status == 206
    assert headers["content-range"] == f"bytes 0-99/{size}"
    assert part == full[:100]
    assert int(headers["content-length"]) == 100

    status, headers, part = _call(web.port, "GET", path, headers={"Range": "bytes=-10"})
    assert status == 206
    assert headers["content-range"] == f"bytes {size - 10}-{size - 1}/{size}"
    assert part == full[-10:]

    status, headers, payload = _call_json(
        web.port, "GET", path, headers={"Range": "bytes=999999999-"}
    )
    assert status == 416
    assert headers["content-range"] == f"bytes */{size}"
    assert payload["error"]["code"] == "range_not_satisfiable"

    status, _, _ = _call(web.port, "HEAD", path, headers={"Range": "bytes=0-99"})
    assert status == 206


def test_upload_then_probe_uses_returned_path(web: SimpleNamespace) -> None:
    blob = b"0123456789" * 32
    status, _, raw = _call(
        web.port,
        "PUT",
        "/api/uploads/%2E%2E%2Fclip%20demo.mov",
        body=blob,
        headers={"Content-Length": str(len(blob))},
    )
    assert status == 200
    payload = json.loads(raw.decode("utf-8"))
    uploaded = Path(payload["path"])
    assert uploaded.is_file()
    assert uploaded.read_bytes() == blob
    assert payload["size"] == len(blob)
    assert payload["name"] == "_clip demo.mov"
    assert uploaded.parent == web.manager.runs_root / "uploads"
    assert uploaded.is_relative_to(web.manager.runs_root)

    status, _, payload = _call_json(web.port, "POST", "/api/probe", {"path": str(uploaded)})
    _assert_error_envelope(status, payload, 400)
    assert payload["error"]["code"] == "not_media"


def test_upload_rejects_oversized_body(web: SimpleNamespace) -> None:
    conn = http_client.HTTPConnection("127.0.0.1", web.port, timeout=10)
    try:
        conn.putrequest("PUT", "/api/uploads/too-big.mov")
        conn.putheader("Content-Length", str(MAX_UPLOAD_BYTES + 1))
        conn.endheaders()
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 413
        payload = json.loads(raw.decode("utf-8"))
        assert payload["error"]["code"] == "payload_too_large"
    finally:
        conn.close()


def test_keep_alive_reuses_connection(web: SimpleNamespace) -> None:
    conn = http_client.HTTPConnection("127.0.0.1", web.port, timeout=10)
    try:
        conn.request("GET", "/api/health")
        first = conn.getresponse()
        assert first.status == 200
        first.read()
        assert not first.will_close
        conn.request("GET", "/")
        second = conn.getresponse()
        assert second.status == 200
        assert second.read()
        assert not second.will_close
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# end-to-end: synthetic video -> probe -> highlights -> job -> verify -> Range
# --------------------------------------------------------------------------- #


def _synthesize_video(path: Path, duration: int = 12) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, local ffmpeg
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x568:rate=30:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "15",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )


def _wait_for_job(port: int, job_id: str, *, timeout: float = 240.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    progress = 0.0
    view: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, _, view = _call_json(port, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        assert view["progress"] >= progress - 1e-9, "progress must never go backwards"
        progress = view["progress"]
        if view["status"] in {"done", "failed", "cancelled"}:
            return view
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s: {view.get('logs')[-5:]}")


@pytest.mark.integration
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe are not installed")
def test_end_to_end_job_with_synthetic_video(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    _synthesize_video(video)

    try:
        from tennis_evolution.labels import discover_fonts

        discover_fonts()
        use_labels = True
    except (SystemExit, OSError):  # pragma: no cover - depends on the runner
        use_labels = False

    highlights = (
        "0:01 - 0:02  Rally (4 hits)\n"
        "0:04 - 0:06  Long rally (9 hits)\n"
        "0:08 - 0:10  Rally (6 hits)\n"
    )

    with _running_server(tmp_path / "http") as (server, manager):
        port = server.server_address[1]

        status, _, health = _call_json(port, "GET", "/api/health")
        assert status == 200 and health["tools"]["ok"] is True

        status, _, probe = _call_json(port, "POST", "/api/probe", {"path": str(video)})
        assert status == 200
        source = probe["source"]
        assert source["video_codec"] == "h264"
        assert source["duration"] == pytest.approx(12.0, abs=0.5)
        assert (source["width"], source["height"]) == (320, 568)
        assert source["has_audio"] is True

        status, _, preview = _call_json(
            port, "POST", "/api/highlights", {"text": highlights, "top": 2, "sort": "hits"}
        )
        assert status == 200
        assert [item["hits"] for item in preview["selected"]] == [9, 6]

        status, _, created = _call_json(
            port,
            "POST",
            "/api/jobs",
            {
                "source": str(video),
                "highlights": highlights,
                "top": 2,
                "sort": "hits",
                "lead": 0.2,
                "tail": 0.2,
                "labels": use_labels,
                "plain_reel": not use_labels,
                "browser_preview": False,
                "quality": "preview",
                "place": "测试场馆",
                "timezone": "Asia/Shanghai",
            },
        )
        assert status == 200
        job_id = created["job"]["id"]
        assert created["job"]["status"] in {"queued", "running"}

        view = _wait_for_job(port, job_id)
        assert view["status"] == "done", view["error"]
        assert view["progress"] == 1.0
        assert view["elapsed"] >= 0
        result = view["result"]
        assert result["ok"] is True
        assert len(result["clips"]) == 2
        assert result["reels"], "expected at least one reel"
        verification = result["verification"]
        assert verification, "verify must have checked files"
        failures = [
            (name, item["name"], item["detail"])
            for name, items in verification.items()
            for item in items
            if not item["ok"]
        ]
        assert failures == [], failures
        for clip in result["clips"]:
            assert clip["url"].startswith(f"/files/{job_id}/")
            assert clip["size"] > 0

        # manifest is served and keeps the public contract (§ manifest.json)
        status, _, manifest = _call_json(port, "GET", result["manifest_url"])
        assert status == 200
        assert manifest["ok"] is True
        assert manifest["verification"] == verification

        # media files are served with Range support
        reel = next(iter(result["reels"].values()))
        status, headers, body = _call(port, "GET", reel["url"], headers={"Range": "bytes=0-1023"})
        assert status == 206
        assert headers["content-range"] == f"bytes 0-1023/{reel['size']}"
        assert len(body) == 1024
        assert headers["content-type"] == "video/mp4"

        status, headers, body = _call(port, "HEAD", reel["url"])
        assert status == 200 and body == b""
        assert headers["accept-ranges"] == "bytes"
        assert int(headers["content-length"]) == reel["size"]

        status, _, listing = _call_json(port, "GET", "/api/jobs")
        assert status == 200
        assert listing["jobs"][0]["id"] == job_id
        assert listing["jobs"][0]["ok"] is True

        status, _, deleted = _call_json(port, "DELETE", f"/api/jobs/{job_id}")
        assert status == 200 and deleted["deleted"] is True
        assert not (manager.runs_root / job_id).exists()
        status, _, payload = _call_json(port, "GET", f"/api/jobs/{job_id}")
        assert status == 404
