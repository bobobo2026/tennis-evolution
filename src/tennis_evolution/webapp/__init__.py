"""Local browser UI for tennis-evolution.

Public entry points:

* :func:`create_server` - bind a :class:`WebAppServer` (see ``docs/webapp.md`` §2).
* :func:`serve_forever` - blocking server used by ``__main__`` and the CLI
  ``tennis-evolution web`` subcommand.

Run it with ``PYTHONPATH=src python -m tennis_evolution.webapp --port 8787``.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from pathlib import Path

from .. import __version__
from .app import DEFAULT_PORT, WebAppServer, create_server

__all__ = [
    "DEFAULT_PORT",
    "WebAppServer",
    "create_server",
    "serve_forever",
    "__version__",
]

logger = logging.getLogger("tennis_evolution.webapp")


def serve_forever(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    runs_root: Path | str | None = None,
    demo_dir: Path | str | None = None,
    open_browser: bool = False,
) -> None:
    """Create the HTTP server and block until interrupted.

    ``runs_root`` / ``demo_dir`` default to the task layer's own defaults
    (``<repo>/webapp-runs`` and the workspace ``demo_run/``).
    """
    try:
        server = create_server(
            runs_root=runs_root,
            demo_dir=demo_dir,
            host=host,
            port=port,
        )
    except OSError as exc:
        sys.exit(f"启动失败：端口 {port} 可能已被占用或无法绑定（{exc}）。可用 --port 换一个端口。")

    bound_host, bound_port = server.server_address[:2]
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", "::"} else str(bound_host)
    url = f"http://{display_host}:{bound_port}/"
    logger.info("网球进化 Web 版已启动：%s（runs_root=%s）", url, server.runs_root)
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到中断，正在停止服务")
    finally:
        server.server_close()
        logger.info("服务已停止")
