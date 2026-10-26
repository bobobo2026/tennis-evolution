from __future__ import annotations

from pathlib import Path

import pytest

from tennis_evolution.cut import ClipPlan, align, plan_clips
from tennis_evolution.highlights import Segment
from tennis_evolution.media import MediaInfo, StreamInfo

KEYFRAMES = [0.0, 1.0, 2.0, 3.5, 5.0, 6.5, 8.0]


def fake_info(duration: float = 2849.195) -> MediaInfo:
    return MediaInfo(
        path=Path("IMG_5155.MOV"),
        duration=duration,
        size=3_275_259_475,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        streams=[
            StreamInfo(
                index=0, codec_type="video", codec_name="hevc", width=1920, height=1080, fps=29.97
            ),
            StreamInfo(
                index=1, codec_type="audio", codec_name="aac", channels=2, sample_rate=48000
            ),
        ],
        rotation=-90,
    )


def test_align_snaps_outward_to_keyframes() -> None:
    start, end, notes = align(Segment(start=2.2, end=3.0), KEYFRAMES, lead=0.5, tail=0.5)
    assert start == pytest.approx(1.0)
    assert end == pytest.approx(3.5)
    assert notes == []


def test_align_adds_lead_and_tail() -> None:
    segment = Segment(start=1634, end=1688, hits=40)
    kfs = [1629.768 + i * 1.0667 for i in range(60)]
    start, end, _ = align(segment, kfs, lead=1.0, tail=1.5)
    assert segment.start - start == pytest.approx(1.0, abs=1.1)
    assert end - segment.end >= 1.5
    assert start <= segment.start - 1.0


def test_align_without_keyframes_falls_back() -> None:
    start, end, notes = align(Segment(start=10, end=20), [], lead=1.0, tail=1.5, duration=100)
    assert (start, end) == (9.0, 21.5)
    assert notes


def test_align_clamps_to_source_duration() -> None:
    start, end, notes = align(
        Segment(start=99, end=100), [95.0], lead=1.0, tail=1.5, duration=100.5
    )
    assert end <= 100.5
    assert any("no later keyframe" in note for note in notes)


def test_clip_filename_and_padding() -> None:
    segment = Segment(start=1634, end=1688, hits=40)
    plan = ClipPlan(index=1, segment=segment, start=1632.968, end=1689.498)
    assert plan.filename == "01_40hits_27m14s-28m08s.mp4"
    assert plan.lead == pytest.approx(1.032)
    assert plan.tail == pytest.approx(1.498)
    assert plan.duration == pytest.approx(56.53)


def test_clip_filename_without_hits() -> None:
    plan = ClipPlan(
        index=2, segment=Segment(start=10, end=20, kind="Quick exchange"), start=9, end=21
    )
    assert plan.filename == "02_quick-exchange_10s-20s.mp4"


def test_plan_clips_uses_supplied_keyframes() -> None:
    info = fake_info()
    plans = plan_clips(
        info, [Segment(start=2.2, end=3.0)], keyframe_list=KEYFRAMES, lead=0.5, tail=0.5
    )
    assert len(plans) == 1
    assert plans[0].start == pytest.approx(1.0)
    assert plans[0].end == pytest.approx(3.5)


def test_plan_clips_numbers_are_sequential() -> None:
    segments = [Segment(start=2, end=3), Segment(start=5, end=6)]
    plans = plan_clips(fake_info(), segments, keyframe_list=KEYFRAMES)
    assert [plan.index for plan in plans] == [1, 2]
