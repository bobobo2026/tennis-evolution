from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tennis_evolution import license as lic
from tennis_evolution.mcp import TOOLS, handle

SAMPLE = "\n".join(f"0:{i:02d} - 0:{i + 1:02d}  Rally ({i} hits)" for i in range(1, 9)) + "\n"


def _call(name: str, arguments: dict) -> dict:
    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert reply is not None
    return reply["result"]


def test_initialize_negotiates_version() -> None:
    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
    )
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["serverInfo"]["name"] == "tennis-evolution"
    assert "tools" in reply["result"]["capabilities"]


def test_notifications_get_no_reply() -> None:
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_error() -> None:
    reply = handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})
    assert reply["error"]["code"] == -32601


def test_tools_list_schema() -> None:
    reply = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    names = {tool["name"] for tool in reply["result"]["tools"]}
    assert {"probe_video", "rank_highlights", "start_highlight_reel", "get_job"} <= names
    for tool in TOOLS:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_rank_highlights_free_cap(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("TENNIS_EVOLUTION_LICENSE", raising=False)
    result = _call("rank_highlights", {"highlights_text": SAMPLE, "top": 0})
    assert not result["isError"]
    data = result["structuredContent"]
    assert data["total"] == 8
    assert len(data["selected"]) == lic.FREE_MAX_RALLIES
    assert data["selected"][0]["hits"] == 8
    assert "note" in data


def test_tool_errors_are_reported_in_band() -> None:
    result = _call("probe_video", {"video": "/definitely/missing.mov"})
    assert result["isError"]
    assert _call("rank_highlights", {})["isError"]
    assert _call("no_such_tool", {})["isError"]


def test_stdio_roundtrip() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "tennis_evolution", "mcp"],
        input="\n".join(json.dumps(m) for m in lines) + "\n",
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"},
        timeout=30,
        check=True,
    )
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[1]["result"]["tools"]
