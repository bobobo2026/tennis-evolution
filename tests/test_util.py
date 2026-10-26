from __future__ import annotations

import pytest

from tennis_evolution.util import format_timecode, parse_timecode


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("27:14", 1634.0),
        ("0:06", 6.0),
        ("1:04:05", 3845.0),
        ("1:04:05.5", 3845.5),
        ("12.5", 12.5),
        ("0", 0.0),
        ("1634", 1634.0),
    ],
)
def test_parse_timecode(text: str, expected: float) -> None:
    assert parse_timecode(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "27:75", "abc", "-3"])
def test_parse_timecode_rejects_bad_input(text: str) -> None:
    with pytest.raises(ValueError):
        parse_timecode(text)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (1634, "27:14"),
        (6, "0:06"),
        (3845, "1:04:05"),
        (56.7, "0:56"),
    ],
)
def test_format_timecode(seconds: float, expected: str) -> None:
    assert format_timecode(seconds) == expected


def test_format_timecode_compact() -> None:
    assert format_timecode(1634, compact=True) == "27m14s"
    assert format_timecode(3845, compact=True) == "1h04m05s"
