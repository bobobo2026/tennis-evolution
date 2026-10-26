from __future__ import annotations

import pytest

from tennis_evolution.highlights import Segment, parse_json, parse_text, ranked, select_top

SLICEAPP_SAMPLE = """Highlights — IMG_5155.m4a (made with sliceapp.so/tools/)
0:00 - 0:06  Quick exchange (3 hits)
2:15 - 2:52  Long rally (24 hits)
27:14 - 28:08  Long rally (40 hits)
28:08 - 28:15  Rally (5 hits)
29:15 - 29:18  Single hit (1 hits)
"""


def test_parse_sliceapp_text() -> None:
    segments = parse_text(SLICEAPP_SAMPLE)
    assert len(segments) == 5
    first = segments[0]
    assert (first.start, first.end, first.hits, first.kind) == (0.0, 6.0, 3, "Quick exchange")
    assert segments[2].hits == 40
    assert segments[2].duration == pytest.approx(54.0)


def test_parse_text_handles_headers_and_blank_lines() -> None:
    segments = parse_text("\n\n# comment\nnot a highlight\n1:00 - 1:10 Rally (4 hits)\n")
    assert len(segments) == 1
    assert segments[0].hits == 4


def test_parse_text_without_hits() -> None:
    segments = parse_text("10:00 - 10:20 rally")
    assert segments[0].hits is None
    assert segments[0].label == "rally"


def test_segment_validation() -> None:
    with pytest.raises(ValueError):
        Segment(start=10, end=10)
    with pytest.raises(ValueError):
        Segment(start=-1, end=5)


def test_ranked_by_hits_then_duration() -> None:
    segments = parse_text(SLICEAPP_SAMPLE)
    ordered = ranked(segments, "hits")
    assert [s.hits for s in ordered] == [40, 24, 5, 3, 1]
    assert ordered[0].start == pytest.approx(1634.0)


def test_ranked_by_duration() -> None:
    segments = parse_text(SLICEAPP_SAMPLE)
    ordered = ranked(segments, "duration")
    assert ordered[0].duration == pytest.approx(54.0)
    assert ordered[1].duration == pytest.approx(37.0)


def test_ranked_by_start() -> None:
    segments = parse_text(SLICEAPP_SAMPLE)
    ordered = ranked(segments, "start")
    assert [s.start for s in ordered] == sorted(s.start for s in segments)


def test_select_top_limits() -> None:
    segments = parse_text(SLICEAPP_SAMPLE)
    assert len(select_top(segments, 2)) == 2
    assert len(select_top(segments, 0)) == len(segments)
    assert len(select_top(segments, None)) == len(segments)


def test_parse_json_roundtrip() -> None:
    segments = parse_json(
        {
            "segments": [
                {"start": 135, "end": 172, "hits": 24, "kind": "Long rally"},
                {"start": "27:14", "end": "28:08", "hits": "40"},
            ]
        }
    )
    assert segments[0].hits == 24
    assert segments[1].start == pytest.approx(1634.0)
    assert segments[1].hits == 40


def test_parse_json_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_json({"nope": 1})
    with pytest.raises(ValueError):
        parse_json(42)
