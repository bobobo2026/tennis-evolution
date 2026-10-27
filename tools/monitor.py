#!/usr/bin/env python3
"""Daily discoverability + business snapshot for Tennis Evolution (standard library only).

Collects: site health, GitHub stars/traffic, PyPI downloads, MCP Registry listing, rank of our
pages in DuckDuckGo/Bing for the target queries, and paid licence counts. Each run appends one
JSON line to the history file and prints the change against the previous run.

    python3 tools/monitor.py                       # snapshot + diff
    python3 tools/monitor.py --merge extra.json    # add metrics gathered elsewhere (e.g. an agent's web search)
    python3 tools/monitor.py --json                # print the snapshot as JSON only

Env: GITHUB_TOKEN (traffic, optional), TE_STATS_TOKEN (sales, optional),
     TE_MONITOR_HISTORY (default ~/.local/share/tennis-evolution/monitor.jsonl).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SITE = os.environ.get("TE_SITE", "https://tennis-evolution.pages.dev")
REPO = "bobobo2026/tennis-evolution"
PYPI = "tennis-evolution"
OURS = ("tennis-evolution.pages.dev", "github.com/bobobo2026/tennis-evolution", "tennisevolution")
PAGES = [
    "/",
    "/zh/",
    "/llms.txt",
    "/sitemap.xml",
    "/guides/iphone-tennis-highlight-video/",
    "/zh/guides/wangqiu-jijin-zidong-jianji/",
]
QUERIES = [
    "tennis highlight video generator",
    "cut tennis rallies from video automatically",
    "tennis highlight reel hit count",
    "tennis video MCP server",
    "sliceapp export highlight reel",
    "网球 集锦 自动剪辑",
    "网球 回合 剪辑 工具",
]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def fetch(
    url: str, *, headers: dict[str, str] | None = None, timeout: float = 20
) -> tuple[int, str, float]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), time.monotonic() - started
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc), time.monotonic() - started


def site_health() -> dict[str, object]:
    pages = {}
    for path in PAGES:
        status, body, seconds = fetch(SITE + path)
        # Pages falls back to "/" with a 200 for unknown paths, so HTML pages must carry their own canonical.
        live = status == 200 and (
            not path.endswith("/") or f'rel="canonical" href="{SITE}{path}"' in body
        )
        pages[path] = {"status": status, "live": live, "ms": round(seconds * 1000)}
    ok = all(page["live"] for page in pages.values())
    return {"ok": ok, "pages": pages}


def github() -> dict[str, object]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, body, _ = fetch(f"https://api.github.com/repos/{REPO}", headers=headers)
    if status != 200:
        return {"error": status}
    repo = json.loads(body)
    data: dict[str, object] = {
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "watchers": repo.get("subscribers_count"),
        "open_issues": repo.get("open_issues_count"),
    }
    if token:
        for kind in ("views", "clones"):
            st, raw, _ = fetch(
                f"https://api.github.com/repos/{REPO}/traffic/{kind}", headers=headers
            )
            if st == 200:
                payload = json.loads(raw)
                data[f"{kind}_14d"] = payload.get("count")
                data[f"{kind}_uniques_14d"] = payload.get("uniques")
    return data


def pypi() -> dict[str, object]:
    status, body, _ = fetch(f"https://pypistats.org/api/packages/{PYPI}/recent")
    if status != 200:
        return {"published": False}
    recent = json.loads(body).get("data", {})
    return {"published": True, **recent}


def mcp_registry() -> dict[str, object]:
    query = urllib.parse.quote("tennis-evolution")
    status, body, _ = fetch(f"https://registry.modelcontextprotocol.io/v0/servers?search={query}")
    if status != 200:
        return {"error": status}
    servers = json.loads(body).get("servers", [])
    names = [s.get("server", s).get("name") for s in servers]
    return {"listed": any(n and "tennis-evolution" in n for n in names)}


def _rank(urls: list[str]) -> int | None:
    for index, url in enumerate(urls, 1):
        if any(mark in url for mark in OURS):
            return index
    return None


def ddg_rank(query: str) -> int | None:
    status, body, _ = fetch("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    if status != 200:
        return None
    urls = [
        urllib.parse.unquote(html.unescape(m))
        for m in re.findall(r'class="result__a"[^>]*href="([^"]+)"', body)
    ]
    return (_rank(urls) or 0) if urls else None


def bing_rank(query: str) -> int | None:
    status, body, _ = fetch(
        "https://www.bing.com/search?count=30&q=" + urllib.parse.quote(query),
        headers={"Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.6"},
    )
    if status != 200:
        return None
    urls = re.findall(r'<li class="b_algo".*?<a[^>]+href="(https?://[^"]+)"', body, re.S)
    return (_rank(urls) or 0) if urls else None


def search() -> dict[str, dict[str, int | None]]:
    results = {}
    for query in QUERIES:
        results[query] = {"ddg": ddg_rank(query), "bing": bing_rank(query)}
        time.sleep(1.5)
    return results


def sales() -> dict[str, object]:
    token = _stats_token()
    if not token:
        return {"configured": False}
    status, body, _ = fetch(SITE + "/api/stats", headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        return {"error": status}
    return json.loads(body)


def _stats_token() -> str | None:
    token = os.environ.get("TE_STATS_TOKEN")
    token_file = Path.home() / ".config" / "tennis-evolution-issuer" / "stats_token"
    if not token and token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    return token or None


def sitemap_paths() -> list[str]:
    status, body, _ = fetch(SITE + "/sitemap.xml")
    if status != 200:
        return []
    return [urllib.parse.urlparse(loc).path for loc in re.findall(r"<loc>([^<]+)</loc>", body)]


def ai_visibility() -> dict[str, object]:
    """AI crawlers, AI-answer referrals and crawl coverage of the sitemap (last 30 days)."""
    token = _stats_token()
    if not token:
        return {"configured": False}
    status, body, _ = fetch(
        SITE + "/api/stats?view=ai&days=30", headers={"Authorization": f"Bearer {token}"}
    )
    if status != 200:
        return {"error": status}
    data = json.loads(body)

    def per_agent(kind: str, field: str) -> dict[str, int]:
        return {name: entry[field] for name, entry in sorted(data.get(kind, {}).items())}

    pages = sitemap_paths()
    crawled = set(data.get("crawled_paths", {}))
    never = [path for path in pages if path not in crawled]
    return {
        "crawlers_yesterday": per_agent("crawler", "yesterday"),
        "crawlers_30d": per_agent("crawler", "hits"),
        "ai_referrals_yesterday": per_agent("ai_ref", "yesterday"),
        "ai_referrals_30d": per_agent("ai_ref", "hits"),
        "search_bots_30d": per_agent("search_bot", "hits"),
        "search_referrals_30d": per_agent("search_ref", "hits"),
        "coverage": {
            "sitemap_pages": len(pages),
            "crawled_by_ai": len(pages) - len(never),
            "never_crawled": never,
        },
        "crawl_errors": len(data.get("errors", [])),
    }


def history_path() -> Path:
    default = Path.home() / ".local" / "share" / "tennis-evolution" / "monitor.jsonl"
    return Path(os.environ.get("TE_MONITOR_HISTORY", default))


def previous(path: Path) -> dict | None:
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def flatten(data: object, prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    if isinstance(data, dict) and not data and prefix:
        flat[prefix[:-1]] = "none"
    elif isinstance(data, dict):
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}{key}."))
    else:
        flat[prefix[:-1]] = data
    return flat


def describe_rank(value: object) -> str:
    if value is None:
        return "n/a"
    return "not in top 10" if value == 0 else f"#{value}"


def report(snapshot: dict, before: dict | None) -> str:
    now = flatten(snapshot)
    old = flatten(before) if before else {}
    lines = [
        f"Tennis Evolution · {snapshot['date']}" + (f"  (vs {before['date']})" if before else "")
    ]
    for key, value in now.items():
        if key in ("date", "taken_at") or key.endswith(".ms"):
            continue
        prior = old.get(key)
        label = key
        if key.startswith("search.") and isinstance(value, int | type(None)):
            shown = describe_rank(value)
            if isinstance(value, int) and isinstance(prior, int) and value != prior:
                better = (value or 99) < (prior or 99)
                shown += f"  ({'↑' if better else '↓'} from {describe_rank(prior)})"
        elif (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and isinstance(prior, int | float)
        ):
            delta = value - prior
            shown = f"{value}" + (f"  ({delta:+g})" if delta else "")
        else:
            shown = f"{value}" + (f"  (was {prior})" if key in old and prior != value else "")
        lines.append(f"  {label:<70} {shown}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--merge", help="JSON file with extra metrics to merge into the snapshot")
    parser.add_argument("--json", action="store_true", help="print the snapshot as JSON")
    parser.add_argument(
        "--no-search", action="store_true", help="skip the search-engine rank checks"
    )
    parser.add_argument("--dry-run", action="store_true", help="do not append to the history file")
    args = parser.parse_args()

    now = datetime.now(UTC).astimezone()
    snapshot: dict[str, object] = {
        "date": now.strftime("%Y-%m-%d"),
        "taken_at": now.isoformat(timespec="seconds"),
        "site": site_health(),
        "github": github(),
        "pypi": pypi(),
        "mcp_registry": mcp_registry(),
        "sales": sales(),
        "ai": ai_visibility(),
    }
    if not args.no_search:
        snapshot["search"] = search()
    if args.merge:
        extra = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(snapshot.get(key), dict):
                snapshot[key] = {**snapshot[key], **value}  # type: ignore[dict-item]
            else:
                snapshot[key] = value

    path = history_path()
    before = previous(path)
    if not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(report(snapshot, before))
        if not args.dry_run:
            print(f"\nhistory: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
