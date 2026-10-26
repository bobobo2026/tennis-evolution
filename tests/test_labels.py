from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tennis_evolution.cut import ClipPlan
from tennis_evolution.highlights import Segment
from tennis_evolution.labels import (
    FontPair,
    LabelSpec,
    discover_fonts,
    format_wall_clock,
    render_label,
    specs_for_plans,
    write_overlays,
)


def _fonts() -> FontPair:
    try:
        return discover_fonts()
    except SystemExit:  # pragma: no cover - depends on the machine
        pytest.skip("no CJK font installed")


def test_discover_fonts_points_to_existing_files() -> None:
    fonts = _fonts()
    from pathlib import Path

    assert Path(fonts.bold_path).exists()


def test_render_label_canvas_and_alpha() -> None:
    image = render_label(
        (1080, 1920),
        LabelSpec(place="宁波 ECHO Tennis", clock="2026-10-24 17:44", source_ts="27:14", hits=40),
        _fonts(),
    )
    assert image.size == (1080, 1920)
    assert image.mode == "RGBA"
    alpha = image.getchannel("A")
    assert alpha.getbbox() is not None, "label should paint something"
    # top band only: nothing is drawn over the action area
    assert alpha.crop((0, 400, 1080, 1920)).getbbox() is None


def test_render_label_scales_with_canvas() -> None:
    fonts = _fonts()
    small = render_label((540, 960), LabelSpec(place="宁波", hits=7), fonts)
    assert small.size == (540, 960)
    assert small.getchannel("A").getbbox() is not None


def test_label_content_differs_by_hits() -> None:
    fonts = _fonts()
    one = render_label((1080, 1920), LabelSpec(place="Echo", hits=24), fonts)
    two = render_label((1080, 1920), LabelSpec(place="Echo", hits=40), fonts)
    assert one.tobytes() != two.tobytes()


def test_render_label_without_hits_has_no_badge() -> None:
    fonts = _fonts()
    with_badge = render_label((1080, 1920), LabelSpec(place="Echo", hits=24), fonts)
    without = render_label((1080, 1920), LabelSpec(place="Echo"), fonts)
    assert with_badge.tobytes() != without.tobytes()


def test_format_wall_clock_respects_timezone() -> None:
    created = datetime(2026, 10, 24, 9, 16, 54, tzinfo=UTC)
    shanghai = timezone(timedelta(hours=8))
    assert format_wall_clock(created, 1634, shanghai) == "2026-10-24 17:44"
    assert format_wall_clock(created, 0, shanghai) == "2026-10-24 17:16"


def test_specs_for_plans_maps_each_segment() -> None:
    plans = [
        ClipPlan(index=1, segment=Segment(start=1634, end=1688, hits=40), start=1632.9, end=1689.5),
        ClipPlan(index=2, segment=Segment(start=135, end=172, hits=24), start=133.3, end=173.8),
    ]
    created = datetime(2026, 10, 24, 9, 16, 54, tzinfo=UTC)
    specs = specs_for_plans(
        plans, created_at=created, tz=timezone(timedelta(hours=8)), place="宁波 ECHO Tennis"
    )
    assert [spec.hits for spec in specs] == [40, 24]
    assert specs[0].clock == "2026-10-24 17:44"
    assert specs[0].source_ts == "27:14"
    assert specs[1].clock == "2026-10-24 17:19"


def test_write_overlays_creates_one_png_per_spec(tmp_path) -> None:
    fonts = _fonts()
    specs = [LabelSpec(place="Echo", hits=40), LabelSpec(place="Echo", hits=24)]
    paths = write_overlays((1080, 1920), specs, tmp_path, fonts)
    assert [path.name for path in paths] == ["label_01.png", "label_02.png"]
    assert all(path.exists() for path in paths)
