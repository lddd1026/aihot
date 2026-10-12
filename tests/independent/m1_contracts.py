"""M1 契约探针：HN 适配器请求参数 + fetch 的 --config 位置语义 + fetch --help。"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
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

JSON_CT = "application/json; charset=utf-8"


def main() -> int:
    suite = Suite("contracts")
    server = LocalServer()
    seen: dict[str, list[str]] = {}

    def hn(query: dict[str, list[str]]) -> Payload:
        seen.update(query)
        hits = [
            {
                "objectID": "9",
                "title": "hn-param-probe",
                "url": "https://hn.example/9",
                "created_at": ago(minutes=30).isoformat(),
                "points": 77,
                "num_comments": 3,
                "author": "bob",
            }
        ]
        return Payload(200, json.dumps({"hits": hits}).encode(), JSON_CT)

    server.routes["/hn"] = hn
    server.routes["/good"] = lambda q: Payload(
        200,
        rss_xml([{"title": "ok", "link": "https://ok.example/1", "date": ago(hours=1)}]),
    )
    server.routes["/llm/models"] = lambda q: Payload(401, b'{"error":"no key"}')
    try:
        TEMPROOT.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="aihot-m1-contract-", dir=TEMPROOT))
        cfg = write_config(
            workspace,
            [
                source("hn", server.url("/hn"), type_="hackernews", extra={"min_points": 30}),
                source("good", server.url("/good")),
            ],
            llm_base_url=server.url("/llm"),
        )
        before = datetime.now()
        result = run_cli(["fetch", "--config", str(cfg), "--limit", "0"])
        params = {
            "tags": seen.get("tags"),
            "numericFilters": seen.get("numericFilters"),
            "hitsPerPage": seen.get("hitsPerPage"),
        }
        numeric = params["numericFilters"][0] if params["numericFilters"] else ""
        window_ok = False
        if "created_at_i>" in numeric:
            stamp = int(numeric.split("created_at_i>")[1].split(",")[0])
            # 配置窗口为 24h：since 应约等于 now-86400s（±1h 容差）
            expected = before.timestamp() - 86400
            window_ok = expected - 3600 <= stamp <= expected + 3600
        suite.check(
            "契约-HN请求带tags/numericFilters/hitsPerPage",
            params["tags"] == ["story"]
            and "points>30" in numeric
            and window_ok
            and params["hitsPerPage"] == ["100"],
            f"实际请求参数={params}（窗口时间戳合理={window_ok}）",
            evidence=result.stdout,
        )
        rows = db_query(workspace / "data" / "aihot.db", "SELECT source_id FROM items")
        suite.check(
            "契约-HN条目落库",
            sorted(row["source_id"] for row in rows) == ["good", "hn"],
            f"落库源={sorted(row['source_id'] for row in rows)}",
        )

        # --config 位置语义（M1 新增 fetch 命令同样支持前后两处）
        after = run_cli(["--config", str(cfg), "fetch", "--dry-run", "--limit", "0"])
        before_cmd = run_cli(["fetch", "--dry-run", "--limit", "0", "--config", str(cfg)])
        suite.check(
            "契约-fetch --config 前后位置均可",
            after.rc == 0 and before_cmd.rc == 0,
            f"--config 在前 rc={after.rc}，--config 在后 rc={before_cmd.rc}",
            evidence=after.text + "\n\n" + before_cmd.text,
        )

        help_result = run_cli(["fetch", "--help"])
        needed = ("--source", "--window-hours", "--dry-run", "--limit")
        suite.check(
            "契约-fetch --help 列出全部参数",
            help_result.rc == 0 and all(flag in help_result.stdout for flag in needed),
            f"参数齐全={all(flag in help_result.stdout for flag in needed)}",
            evidence=help_result.stdout,
        )
        counts = db_counts(workspace / "data" / "aihot.db")
        suite.check(
            "契约-dry-run未写库",
            counts["runs"] == 1,
            f"runs={counts['runs']}（应为 1，仅首次真实运行）",
        )
        suite.check(
            "契约-采集层不调 LLM",
            server.hits.get("/llm/models", 0) == 0,
            f"fetch 期间 LLM 端点请求次数={server.hits.get('/llm/models', 0)}（应为 0）",
        )
    finally:
        server.stop()
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
