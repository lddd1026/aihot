"""URL 边界复验：非法/相对链接在采集层的处理（D3 修复后契约）。

新契约（sources.base）：只接受 http(s) 且带主机名的绝对地址；
相对链接按源地址补全；javascript:/mailto: 等非 http(s) 一律丢弃。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
    ago,
    db_query,
    rss_xml,
    run_cli,
    source,
    write_config,
)


def main() -> int:
    suite = Suite("url_edges")
    server = LocalServer()
    server.routes["/weird"] = lambda q: Payload(
        200,
        rss_xml(
            [
                {"title": "relative", "link": "/relative/path", "date": ago(hours=1)},
                {"title": "protocol-relative", "link": "//cdn.example/asset", "date": ago(hours=1)},
                {"title": "js-scheme", "link": "javascript:alert(1)", "date": ago(hours=1)},
                {"title": "plain-text", "link": "not a url", "date": ago(hours=1)},
                {"title": "mailto", "link": "mailto:a@b.com", "date": ago(hours=1)},
                {"title": "normal", "link": "https://ok.example/normal", "date": ago(hours=1)},
            ]
        ),
    )
    try:
        TEMPROOT.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="aihot-m1-urledge-", dir=TEMPROOT))
        cfg = write_config(workspace, [source("weird", server.url("/weird"))])
        result = run_cli(["fetch", "--config", str(cfg), "--limit", "0"])
        rows = db_query(
            workspace / "data" / "aihot.db",
            "SELECT title, url FROM items ORDER BY title",
        )
        observed = {row["title"]: row["url"] for row in rows}
        suite.evidence_json("observed-urls.json", observed)

        dropped = {"js-scheme", "plain-text", "mailto"} - set(observed)
        non_http = {
            title: url
            for title, url in observed.items()
            if not url.startswith(("http://", "https://"))
        }
        expected_dropped = {"js-scheme", "plain-text", "mailto"}
        suite.check(
            "URL边界-非http(s)/含空白链接被丢弃",
            result.rc == 0
            and dropped == expected_dropped
            and not non_http,
            f"javascript:/mailto:/含空格文本 已丢弃={dropped == expected_dropped}，"
            f"落库非 http(s) 条目={non_http or '无'}",
            evidence=result.stdout,
        )
        suite.check(
            "URL边界-相对链接按源地址补全",
            observed.get("relative", "").startswith(server.url("/"))
            and observed.get("relative", "").endswith("/relative/path"),
            f"relative → {observed.get('relative')!r}（源地址 {server.url('/')}）",
        )
        suite.check(
            "URL边界-协议相对链接补全 scheme",
            observed.get("protocol-relative") == "http://cdn.example/asset",
            f"//cdn.example/asset → {observed.get('protocol-relative')!r}",
        )
        suite.check(
            "URL边界-正常链接不受影响",
            observed.get("normal") == "https://ok.example/normal",
            f"normal → {observed.get('normal')!r}",
        )
    finally:
        server.stop()
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
