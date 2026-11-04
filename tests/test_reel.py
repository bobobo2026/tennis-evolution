from __future__ import annotations

from pathlib import Path

from tennis_evolution.media import MediaInfo, StreamInfo
from tennis_evolution.reel import planned_size, scale_chain


def _info(width: int = 2688, height: int = 1512, rotation: int = 0) -> MediaInfo:
    return MediaInfo(
        path=Path("DJI_0122.MP4"),
        duration=700.9,
        size=4_600_000_000,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        streams=[
            StreamInfo(
                index=0,
                codec_type="video",
                codec_name="hevc",
                width=width,
                height=height,
                fps=59.94,
                pix_fmt="yuv420p10le",
            ),
            StreamInfo(
                index=1, codec_type="audio", codec_name="aac", channels=2, sample_rate=48000
            ),
        ],
        rotation=rotation,
    )


def test_planned_size_without_cap() -> None:
    assert planned_size(_info(), None) == (2688, 1512)


def test_planned_size_caps_height_and_keeps_aspect() -> None:
    width, height = planned_size(_info(), 1080)
    assert height == 1080
    assert width == 1920  # 2688 * 1080/1512 = 1920, still 16:9
    assert width % 2 == 0


def test_planned_size_ignores_cap_above_source() -> None:
    assert planned_size(_info(), 2160) == (2688, 1512)


def test_planned_size_uses_display_orientation() -> None:
    # portrait phone footage: 1920x1080 encoded, rotated -90
    assert planned_size(_info(1920, 1080, rotation=-90), None) == (1080, 1920)
    # 1080x1920 capped to 1080 high keeps the portrait aspect: 608x1080
    assert planned_size(_info(1920, 1080, rotation=-90), 1080) == (608, 1080)


def test_scale_chain_is_empty_when_nothing_capped() -> None:
    assert scale_chain(None, None) == ""


def test_scale_chain_fps_only() -> None:
    assert scale_chain(30, None) == ",fps=30"


def test_scale_chain_height_only() -> None:
    assert scale_chain(None, 1080) == ",scale=-2:1080:flags=lanczos"


def test_scale_chain_both_orders_fps_first() -> None:
    assert scale_chain(30, 1080) == ",fps=30,scale=-2:1080:flags=lanczos"
