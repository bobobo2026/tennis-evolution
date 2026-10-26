"""Corner labels: hit count, venue, wall clock and the source timestamp.

Labels are rendered as transparent PNGs with Pillow and burned in by ffmpeg. Doing it
this way (instead of ``drawtext``) keeps the tool usable on ffmpeg builds without
libfreetype -- which is exactly the situation on many Homebrew/minimal builds -- and
gives full control over typography, CJK shaping and HDR-safe colours.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from .cut import ClipPlan
from .util import format_timecode

__all__ = [
    "FontPair",
    "LabelStyle",
    "LabelSpec",
    "discover_fonts",
    "render_label",
    "write_overlays",
    "specs_for_plans",
    "format_wall_clock",
]

_CJK_FONT_CANDIDATES: list[tuple[str, int, tuple[str, int]]] = [
    # (path, index, (regular_path, regular_index)) - macOS first
    (
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        2,
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 1, ("/System/Library/Fonts/STHeiti Light.ttc", 0)),
    ("/System/Library/Fonts/PingFang.ttc", 3, ("/System/Library/Fonts/PingFang.ttc", 0)),
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        0,
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ),
    (
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        0,
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ),
    (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        0,
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ),
    ("C:/Windows/Fonts/msyhbd.ttc", 0, ("C:/Windows/Fonts/msyh.ttc", 0)),
]


@dataclass(frozen=True)
class FontPair:
    """Font file used for headings/numbers and for secondary text."""

    bold_path: str
    bold_index: int = 0
    regular_path: str | None = None
    regular_index: int = 0

    def regular(self) -> tuple[str, int]:
        return (self.regular_path or self.bold_path, self.regular_index)


def _parse_font_spec(value: str) -> tuple[str, int]:
    path, _, index = value.partition(":")
    if index.isdigit():
        return path, int(index)
    return path, 0


def discover_fonts(font: str | None = None, font_bold: str | None = None) -> FontPair:
    """Find a CJK-capable font pair, honouring env vars and explicit overrides."""
    env_regular = os.environ.get("TENNIS_EVOLUTION_FONT")
    env_bold = os.environ.get("TENNIS_EVOLUTION_FONT_BOLD")
    chosen_bold = font_bold or env_bold or font
    chosen_regular = font or env_regular

    if chosen_bold:
        bold_path, bold_index = _parse_font_spec(chosen_bold)
        if chosen_regular:
            reg_path, reg_index = _parse_font_spec(chosen_regular)
        else:
            reg_path, reg_index = bold_path, bold_index
        return FontPair(bold_path, bold_index, reg_path, reg_index)

    for bold_path, bold_index, (reg_path, reg_index) in _CJK_FONT_CANDIDATES:
        if Path(bold_path).exists() and Path(reg_path).exists():
            return FontPair(bold_path, bold_index, reg_path, reg_index)
    raise SystemExit(
        "no CJK font found. Install one (e.g. `fonts-noto-cjk` on Debian/Ubuntu) or pass\n"
        "  --font /path/to/font.ttc[:index]      (secondary text)\n"
        "  --font-bold /path/to/font.ttc[:index] (headings and hit count)"
    )


@dataclass
class LabelStyle:
    """Design values, expressed at a 1080 px wide canvas and scaled for other sizes."""

    design_width: int = 1080
    margin_ratio: float = 0.052
    top_ratio: float = 0.069
    pad_x: int = 34
    pad_y: int = 26
    radius: int = 26
    gap: int = 14
    background_alpha: int = 122
    title_px: int = 48
    sub_px: int = 34
    small_px: int = 29
    number_px: int = 104
    unit_px: int = 40
    unit_drop_px: int = 8
    text: tuple[int, int, int] = (255, 255, 255)
    text_dim: tuple[int, int, int] = (232, 232, 232)
    text_faint: tuple[int, int, int] = (190, 190, 190)
    extra_lines: list[str] = field(default_factory=list)


@dataclass
class LabelSpec:
    """Text content for one segment label."""

    place: str | None = None
    clock: str | None = None
    source_ts: str | None = None
    hits: int | None = None
    unit: str = "拍"
    source_label: str = "片源"


def format_wall_clock(created_at: datetime, offset_seconds: float, tz: tzinfo | None = None) -> str:
    """Wall clock of ``offset_seconds`` after recording start."""
    moment = created_at + timedelta(seconds=offset_seconds)
    if tz is not None:
        moment = moment.astimezone(tz)
    return moment.strftime("%Y-%m-%d %H:%M")


def specs_for_plans(
    plans: list[ClipPlan],
    *,
    created_at: datetime | None = None,
    tz: tzinfo | None = None,
    place: str | None = None,
    unit: str = "拍",
    source_label: str = "片源",
) -> list[LabelSpec]:
    specs: list[LabelSpec] = []
    for plan in plans:
        clock = (
            format_wall_clock(created_at, plan.segment.start, tz)
            if created_at is not None
            else None
        )
        specs.append(
            LabelSpec(
                place=place,
                clock=clock,
                source_ts=format_timecode(plan.segment.start),
                hits=plan.segment.hits,
                unit=unit,
                source_label=source_label,
            )
        )
    return specs


def _font(pair: tuple[str, int], size: int):  # noqa: ANN202 - PIL font instance
    from PIL import ImageFont

    path, index = pair
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.truetype(path, size)


def _text_size(font, text: str) -> tuple[int, int]:  # noqa: ANN001
    box = font.getbbox(text)
    return box[2] - box[0], box[3] - box[1]


def render_label(
    size: tuple[int, int],
    spec: LabelSpec,
    fonts: FontPair,
    style: LabelStyle | None = None,  # noqa: ANN001
):  # noqa: ANN201 - returns PIL.Image.Image
    """Render one transparent overlay for a segment."""
    from PIL import Image, ImageDraw

    style = style or LabelStyle()
    width, height = size
    scale = width / style.design_width
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def px(value: float) -> int:
        return max(1, int(round(value * scale)))

    margin = px(style.margin_ratio * style.design_width)
    top = px(style.top_ratio * style.design_width)
    pad_x, pad_y = px(style.pad_x), px(style.pad_y)
    gap, radius = px(style.gap), px(style.radius)
    bg = (0, 0, 0, style.background_alpha)

    lines: list[tuple[object, str, tuple[int, int, int, int]]] = []
    if spec.hits is not None:
        heading = f"{spec.hits} 拍"
    elif spec.source_label:
        heading = spec.source_label
    else:
        heading = ""
    title_font = _font((fonts.bold_path, fonts.bold_index), px(style.title_px))
    sub_font = _font(fonts.regular(), px(style.sub_px))
    small_font = _font(fonts.regular(), px(style.small_px))

    if spec.place:
        lines.append((title_font, spec.place, (*style.text, 255)))
    if spec.clock:
        lines.append((sub_font, spec.clock, (*style.text_dim, 255)))
    tail_bits = []
    if spec.source_ts:
        tail_bits.append(f"{spec.source_label} {spec.source_ts}")
    if spec.hits is None and heading:
        tail_bits.append(heading)
    tail_bits.extend(style.extra_lines)
    if tail_bits:
        lines.append((small_font, " · ".join(tail_bits), (*style.text_faint, 255)))

    if lines:
        sizes = [_text_size(font, text) for font, text, _ in lines]
        card_w = max(w for w, _ in sizes) + pad_x * 2
        card_h = sum(h for _, h in sizes) + gap * (len(lines) - 1) + pad_y * 2
        draw.rounded_rectangle([margin, top, margin + card_w, top + card_h], radius=radius, fill=bg)
        y = top + pad_y
        for (font, text, colour), (_, line_h) in zip(lines, sizes, strict=True):
            draw.text((margin + pad_x, y - font.getbbox(text)[1]), text, font=font, fill=colour)
            y += line_h + gap

    if spec.hits is not None:
        number_font = _font((fonts.bold_path, fonts.bold_index), px(style.number_px))
        unit_font = _font(fonts.regular(), px(style.unit_px))
        digits = str(spec.hits)
        digits_w, digits_h = _text_size(number_font, digits)
        unit_w, unit_h = _text_size(unit_font, spec.unit)
        badge_pad_x, badge_pad_y = px(30), px(18)
        badge_w = digits_w + px(10) + unit_w + badge_pad_x * 2
        badge_h = max(digits_h, unit_h) + badge_pad_y * 2
        right = width - margin
        left = right - badge_w
        draw.rounded_rectangle([left, top, right, top + badge_h], radius=radius, fill=bg)
        digits_y = top + (badge_h - digits_h) // 2 - number_font.getbbox(digits)[1]
        draw.text((left + badge_pad_x, digits_y), digits, font=number_font, fill=(*style.text, 255))
        unit_bottom = top + badge_h - badge_pad_y - px(style.unit_drop_px)
        unit_y = unit_bottom - unit_h - unit_font.getbbox(spec.unit)[1]
        draw.text(
            (left + badge_pad_x + digits_w + px(10), unit_y),
            spec.unit,
            font=unit_font,
            fill=(*style.text_dim, 255),
        )

    return canvas


def write_overlays(
    size: tuple[int, int],
    specs: list[LabelSpec],
    outdir: str | Path,
    fonts: FontPair,
    style: LabelStyle | None = None,
) -> list[Path]:
    """Render every spec to ``<outdir>/label_NN.png`` and return the paths."""
    directory = Path(outdir)
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, spec in enumerate(specs, 1):
        target = directory / f"label_{index:02d}.png"
        render_label(size, spec, fonts, style).save(target)
        paths.append(target)
    return paths
