from __future__ import annotations

import json

import pytest

from tennis_evolution.cli import build_parser, main

SAMPLE = "0:00 - 0:06  Quick exchange (3 hits)\n27:14 - 28:08  Long rally (40 hits)\n"


def test_parser_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "tennis-evolution" in capsys.readouterr().out


def test_highlights_command_prints_top(capsys, tmp_path) -> None:
    path = tmp_path / "h.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    assert main(["highlights", str(path), "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "40 hits" in out
    assert "27:14" in out


def test_highlights_command_writes_json(capsys, tmp_path) -> None:
    path = tmp_path / "h.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "selected.json"
    assert main(["highlights", str(path), "--top", "2", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["segments"]) == 2
    assert payload["segments"][0]["hits"] == 40


def test_missing_highlights_file_exits(tmp_path) -> None:
    with pytest.raises(SystemExit):
        main(["highlights", str(tmp_path / "nope.txt")])


def test_probe_missing_video_exits(tmp_path) -> None:
    with pytest.raises(SystemExit):
        main(["probe", str(tmp_path / "nope.mp4")])
