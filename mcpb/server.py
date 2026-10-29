"""MCPB entry point: run the tennis-evolution MCP server installed from PyPI.

Desktop hosts start extensions with a minimal PATH, so the usual ffmpeg locations
are added here, plus an optional directory chosen in the extension settings.
"""

import os
import sys
from pathlib import Path

_COMMON_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


def _set_path() -> None:
    current = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    chosen = os.environ.get("TE_FFMPEG_DIR", "").strip()
    front = [chosen] if chosen and Path(chosen).is_dir() else []
    back = [d for d in _COMMON_DIRS if Path(d).is_dir()]
    merged: list[str] = []
    for p in front + current + back:
        if p not in merged:
            merged.append(p)
    os.environ["PATH"] = os.pathsep.join(merged)


def _set_license() -> None:
    key = os.environ.get("TE_LICENSE_KEY", "").strip()
    if key.startswith("te1."):
        os.environ["TENNIS_EVOLUTION_LICENSE"] = key


def main() -> int:
    _set_path()
    _set_license()
    from tennis_evolution.mcp import serve_stdio

    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
