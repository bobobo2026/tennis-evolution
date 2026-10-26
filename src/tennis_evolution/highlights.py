"""Highlight list parsing, ranking and serialisation.

Any detector works as an input: sliceapp's text export, a hand written list, or a
JSON file produced by your own tool. The pipeline only needs ``start``/``end`` and,
optionally, ``hits`` per rally.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .util import format_timecode, parse_timecode

__all__ = [
    "Segment",
    "parse_text",
    "parse_json",
    "load",
    "save",
    "select_top",
    "ranked",
]

SortKey = Literal["hits", "duration", "start"]

_TEXT_LINE = re.compile(
    r"""
    ^\s*
    (?P<start>[0-9:.]+)          # 27:14 or 1634 or 1:02:03
    \s*[-\u2013\u2014]\s*        # -, –, —
    (?P<end>[0-9:.]+)
    \s+
    (?P<body>.*?)
    \s*$
    """,
    re.VERBOSE,
)
_HITS = re.compile(r"\(\s*(?P<hits>\d+)\s*hits?\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class Segment:
    """One rally/highlight inside the source video, in seconds."""

    start: float
    end: float
    hits: int | None = None
    kind: str = ""

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"start must be >= 0, got {self.start}")
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be greater than start ({self.start})")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def label(self) -> str:
        return f"{self.hits} hits" if self.hits is not None else (self.kind or "rally")

    def to_dict(self) -> dict[str, object]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Segment:
        try:
            start = parse_timecode(payload["start"])  # type: ignore[arg-type]
            end = parse_timecode(payload["end"])  # type: ignore[arg-type]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(f"segment is missing {exc.args[0]!r}") from exc
        hits_raw = payload.get("hits")
        hits = (
            int(hits_raw)
            if isinstance(hits_raw, (int, float, str)) and str(hits_raw).isdigit()
            else None
        )
        return cls(start=start, end=end, hits=hits, kind=str(payload.get("kind") or ""))

    def describe(self) -> str:
        return f"{format_timecode(self.start)}-{format_timecode(self.end)} {self.label}"


def parse_text(text: str) -> list[Segment]:
    """Parse sliceapp-style highlight text.

    Example::

        0:00 - 0:06  Quick exchange (3 hits)
        27:14 - 28:08  Long rally (40 hits)

    Lines that do not match a time range are ignored, so headers/footers are safe.
    """
    segments: list[Segment] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _TEXT_LINE.match(line)
        if not match:
            continue
        body = match.group("body").strip()
        hits: int | None = None
        hits_match = _HITS.search(body)
        if hits_match:
            hits = int(hits_match.group("hits"))
            body = _HITS.sub("", body).strip()
        kind = re.sub(r"\s+", " ", body)
        segments.append(
            Segment(
                start=parse_timecode(match.group("start")),
                end=parse_timecode(match.group("end")),
                hits=hits,
                kind=kind,
            )
        )
    return segments


def parse_json(payload: object) -> list[Segment]:
    """Parse either a bare list of segments or ``{"segments": [...]}``."""
    if isinstance(payload, dict):
        items = payload.get("segments")
        if items is None:
            raise ValueError("JSON object must contain a 'segments' list")
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("JSON must be a list or an object with 'segments'")
    if not isinstance(items, list):
        raise ValueError("'segments' must be a list")
    return [Segment.from_dict(item) for item in items]  # type: ignore[arg-type]


def load(path: str | Path) -> list[Segment]:
    """Load segments from a ``.json`` file or a text highlight list."""
    file_path = Path(path)
    if not file_path.exists():
        raise SystemExit(f"highlights file not found: {file_path}")
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".json":
        return parse_json(json.loads(text))
    segments = parse_text(text)
    if not segments and text.strip():
        # try JSON content in a .txt file - users rename things all the time
        try:
            segments = parse_json(json.loads(text))
        except (ValueError, json.JSONDecodeError):
            segments = []
    if not segments:
        raise SystemExit(
            f"no highlights parsed from {file_path}. Expected lines like:\n"
            "  27:14 - 28:08  Long rally (40 hits)"
        )
    return segments


def save(segments: Iterable[Segment], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"segments": [segment.to_dict() for segment in segments]}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def ranked(segments: Iterable[Segment], sort: SortKey = "hits") -> list[Segment]:
    """Sort highlights best-first.

    ``hits`` ranks by hit count (longest rally first), falling back to duration and
    then to the earlier timestamp so the result is deterministic.
    """
    items = list(segments)
    if sort == "hits":
        return sorted(
            items,
            key=lambda s: (s.hits if s.hits is not None else -1, s.duration, -s.start),
            reverse=True,
        )
    if sort == "duration":
        return sorted(items, key=lambda s: (s.duration, s.hits or 0, -s.start), reverse=True)
    return sorted(items, key=lambda s: s.start)


def select_top(
    segments: Iterable[Segment], top: int | None = None, sort: SortKey = "hits"
) -> list[Segment]:
    ordered = ranked(segments, sort)
    if top is None or top <= 0:
        return ordered
    return ordered[:top]
