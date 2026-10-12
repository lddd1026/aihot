"""M1 对抗项：超时源（挂起不响应）不阻断整体、不无限等待。

本用例会真实等待 3 次 20s 超时 + 退避（约 63s），单独成脚本运行。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
    ago,
    db_counts,
    db_query,
    rss_xml,
    run_cli,
    source,
    write_config,
)


def main() -> int:
    suite = Suite("slow_source")
    server = LocalServer()

    def hang(query: dict[str, list[str]]) -> Payload:
        time.sleep(120)
        return Payload(200, b"never")

    server.routes["/hang"] = hang
    server.routes["/good"] = lambda q: Payload(
        200,
        rss_xml(
            [{"title": "good-while-hang", "link": "https://ok.example/1", "date": ago(hours=1)}]
        ),
    )
    try:
        TEMPROOT.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="aihot-m1-slow-", dir=TEMPROOT))
        cfg = write_config(
            workspace,
            [source("hang", server.url("/hang")), source("good", server.url("/good"))],
        )
        result = run_cli(["fetch", "--config", str(cfg), "--limit", "0"], timeout=180)
        counts = db_counts(workspace / "data" / "aihot.db")
        runs = db_query(
            workspace / "data" / "aihot.db", "SELECT * FROM runs ORDER BY rowid DESC LIMIT 1"
        )
        status = runs[0]["status"] if runs else "<none>"
        ok = (
            result.rc == 0
            and "[ERR] hang" in result.stdout
            and "Timeout" in result.stdout
            and counts["items"] == 1
            and status == "partial"
            and 50 <= result.seconds <= 120
        )
        suite.check(
            "adv-挂起源超时隔离",
            ok,
            f"rc={result.rc} 耗时={result.seconds:.1f}s（3 次 20s 超时+退避；未无限等待）"
            f" 好源落库={counts['items']} runs.status={status}",
            evidence=result.text,
        )
    finally:
        server.stop()
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
