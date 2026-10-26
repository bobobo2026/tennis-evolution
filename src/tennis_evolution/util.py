"""Small shared helpers: time parsing/formatting and console helpers."""

from __future__ import annotations

import re
import sys

__all__ = ["parse_timecode", "format_timecode", "format_clock", "format_duration", "note", "warn"]

_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def parse_timecode(value: str | float | int) -> float:
    """Parse ``H:MM:SS`` / ``M:SS`` / ``SS.sss`` into seconds.

    >>> parse_timecode("27:14")
    1634.0
    >>> parse_timecode("1:04:05.5")
    3845.5
    >>> parse_timecode(12.5)
    12.5
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        raise ValueError("empty timecode")
    if ":" not in text:
        seconds = float(text)
        if seconds < 0:
            raise ValueError(f"negative timecode: {value!r}")
        return seconds
    match = _TIME_RE.match(text)
    if not match:
        raise ValueError(f"unsupported timecode: {value!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    if minutes > 59 or seconds >= 60:
        raise ValueError(f"out-of-range timecode: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def format_timecode(seconds: float, *, compact: bool = False) -> str:
    """Render seconds as ``M:SS`` / ``H:MM:SS`` (fraction kept when non-zero).

    ``compact=True`` renders filenames style ``27m14s``.
    """
    if seconds < 0:
        raise ValueError("negative duration")
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if compact:
        if hours:
            return f"{hours}h{minutes:02d}m{secs:02d}s"
        if minutes:
            return f"{minutes}m{secs:02d}s"
        return f"{secs}s"
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_clock(seconds: float) -> str:
    """Render seconds as ``MM:SS`` keeping a fractional part trimmed."""
    minutes, secs = divmod(seconds, 60)
    if abs(secs - round(secs)) < 1e-6:
        return f"{int(minutes)}:{int(round(secs)):02d}"
    return f"{int(minutes)}:{secs:05.2f}"


def format_duration(seconds: float) -> str:
    """Human readable duration: ``47:29`` or ``3:49``."""
    return format_timecode(seconds)


def note(message: str) -> None:
    print(message, file=sys.stderr)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)
