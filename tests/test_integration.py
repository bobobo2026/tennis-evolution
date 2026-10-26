"""End-to-end tests against real ffmpeg, using a tiny synthetic recording."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tennis_evolution.cli import main
from tennis_evolution.labels import discover_fonts
from tennis_evolution.verify import all_ok, verify_file

pytestmark = pytest.mark.integration

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg is not installed")
    directory = tmp_path_factory.mktemp("media")
    path = directory / "synthetic.mp4"
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x568:rate=30:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=8",
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
    return path


def _highlights(tmp_path) -> str:
    path = tmp_path / "highlights.txt"
    path.write_text(
        "0:01 - 0:02  Rally (4 hits)\n0:04 - 0:06  Long rally (9 hits)\n", encoding="utf-8"
    )
    return str(path)


def _run_all(video, tmp_path, *extra: str) -> tuple[int, dict]:
    outdir = tmp_path / "out"
    code = main(
        [
            "all",
            str(video),
            "--highlights",
            _highlights(tmp_path),
            "--top",
            "2",
            "--outdir",
            str(outdir),
            "--codec",
            "x264",
            "--preset",
            "ultrafast",
            "--crf",
            "30",
            "--lead",
            "0.2",
            "--tail",
            "0.2",
            *extra,
        ]
    )
    manifest = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))
    return code, manifest


def test_all_without_labels(synthetic_video, tmp_path, capsys) -> None:
    code, manifest = _run_all(synthetic_video, tmp_path, "--no-labels")
    capsys.readouterr()
    assert code == 0
    assert manifest["ok"] is True
    clips = sorted((tmp_path / "out" / "clips").glob("*.mp4"))
    assert len(clips) == 2
    assert (tmp_path / "out" / "reel_plain.mp4").exists()
    assert not (tmp_path / "out" / "reel_labeled.mp4").exists()
    assert manifest["clips"][0]["segment"]["hits"] == 9  # ranked by hits

    expected = sum(clip["duration"] for clip in manifest["clips"])
    results = verify_file(tmp_path / "out" / "reel_plain.mp4", expected_duration=expected)
    assert all_ok(results), [result.line() for result in results]


def test_all_with_labels(synthetic_video, tmp_path, capsys) -> None:
    try:
        discover_fonts()
    except SystemExit:  # pragma: no cover - depends on the runner
        pytest.skip("no CJK font installed")
    code, manifest = _run_all(
        synthetic_video, tmp_path, "--place", "测试 ECHO Tennis", "--timezone", "Asia/Shanghai"
    )
    capsys.readouterr()
    assert code == 0
    assert manifest["ok"] is True
    labeled = tmp_path / "out" / "reel_labeled.mp4"
    assert labeled.exists()
    assert len(list((tmp_path / "out" / "labels").glob("label_*.png"))) == 2
    assert all_ok(verify_file(labeled))


def test_dry_run_does_not_create_clips(synthetic_video, tmp_path, capsys) -> None:
    outdir = tmp_path / "out"
    code = main(
        [
            "cut",
            str(synthetic_video),
            "--highlights",
            _highlights(tmp_path),
            "--top",
            "1",
            "--outdir",
            str(outdir),
            "--dry-run",
        ]
    )
    capsys.readouterr()
    assert code == 0
    assert not list((outdir).glob("*.mp4") if outdir.exists() else [])


def test_concat_escape_hatch_reports_warnings(synthetic_video, tmp_path, capsys) -> None:
    outdir = tmp_path / "out"
    main(
        [
            "cut",
            str(synthetic_video),
            "--highlights",
            _highlights(tmp_path),
            "--top",
            "2",
            "--outdir",
            str(outdir),
            "--lead",
            "0.2",
            "--tail",
            "0.2",
        ]
    )
    capsys.readouterr()
    clips = sorted(outdir.glob("*.mp4"))
    assert len(clips) == 2
    out = tmp_path / "concat.mp4"
    code = main(["concat", *[str(clip) for clip in clips], "--out", str(out)])
    stderr = capsys.readouterr().err
    assert out.exists()
    # either clean or it told us what is wrong - never silent corruption
    assert code in {0, 1}
    if code == 1:
        assert "Non-monotonic" in stderr or "offset" in stderr or "mismatch" in stderr
