#!/usr/bin/env python3
"""Ping IndexNow (Bing, Yandex, Seznam, Naver; Bing feeds ChatGPT search and Copilot) with every URL
in the live sitemap. Run after each deploy:  python3 tools/indexnow.py"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

SITE = "https://tennis-evolution.pages.dev"
KEY = "7fbd3247a2582a5a4434766de4e98eab"


def main() -> int:
    sitemap = urllib.request.Request(
        f"{SITE}/sitemap.xml", headers={"User-Agent": "tennis-evolution-indexnow/1.0"}
    )
    with urllib.request.urlopen(sitemap, timeout=20) as response:
        urls = re.findall(r"<loc>([^<]+)</loc>", response.read().decode())
    payload = {
        "host": SITE.split("//", 1)[1],
        "key": KEY,
        "keyLocation": f"{SITE}/{KEY}.txt",
        "urlList": urls,
    }
    request = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(f"IndexNow {response.status}: submitted {len(urls)} URLs")
    except urllib.error.HTTPError as exc:
        print(f"IndexNow {exc.code}: {exc.read().decode()[:300]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
