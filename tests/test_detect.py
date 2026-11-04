from __future__ import annotations

import numpy as np
import pytest

from tennis_evolution.detect import Params, cluster_onsets


def test_cluster_onsets_groups_by_gap() -> None:
    times = np.array([1.0, 2.1, 3.2, 4.3, 5.4, 6.5, 20.0, 21.1, 22.2, 23.3, 24.4, 25.5])
    rallies = cluster_onsets(times, Params(min_hits=4))
    assert len(rallies) == 2
    assert all(r.hits == 6 for r in rallies)
    first = min(rallies, key=lambda r: r.start)
    assert first.start == pytest.approx(1.0)
    assert first.end == pytest.approx(7.3)


def test_cluster_onsets_drops_short_clusters() -> None:
    times = np.array([1.0, 2.1, 3.2])
    assert cluster_onsets(times, Params(min_hits=4)) == []


def test_cluster_onsets_drops_implausible_rate() -> None:
    # 12 hits inside 0.5 s = 24 hits/s, not a rally
    times = np.linspace(0.0, 0.5, 12)
    assert cluster_onsets(times, Params(min_hits=4)) == []


def test_cluster_onsets_caps_duration() -> None:
    times = np.arange(0.0, 200.0, 1.2)
    rallies = cluster_onsets(times, Params(min_hits=4, max_duration=60.0))
    assert rallies
    assert all(r.duration <= 61.5 for r in rallies)


def test_cluster_onsets_sorted_by_hits() -> None:
    times = np.concatenate([np.arange(0, 6) * 1.2, np.arange(100, 110) * 1.2])
    rallies = cluster_onsets(times, Params(min_hits=4))
    assert [r.hits for r in rallies] == sorted([r.hits for r in rallies], reverse=True)


def test_cluster_onsets_empty() -> None:
    assert cluster_onsets(np.array([]), Params()) == []


def test_detect_is_exposed_in_cli() -> None:
    from tennis_evolution.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["detect", "clip.mp4", "--top", "3"])
    assert args.command == "detect"
    assert args.top == 3


def test_highlights_and_detect_are_mutually_optional() -> None:
    from tennis_evolution.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["reel", "clip.mp4", "--detect", "--out", "x.mp4"])
    assert args.detect is True
    assert args.highlights is None
