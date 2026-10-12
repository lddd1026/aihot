"""M1 验收项 4：5 个真实启用源全量采集性能与落库正确性（临时库）。

- 真实网络请求（公开 RSS/API），仅 GET；不推送、不调 LLM。
- 数据落在系统临时目录，绝不触碰真实 data/aihot.db。
- 另含：真实 config.yaml 的 `fetch --dry-run`（验证真实库零写入 + 密钥不外泄）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    PROJECT,
    TEMPROOT,
    Suite,
    db_counts,
    db_query,
    run_cli,
    write_config,
)

REAL_CONFIG = PROJECT / "config.yaml"
REAL_DB = PROJECT / "data" / "aihot.db"
LINE_RE = re.compile(r"\[(OK |ERR)\] (\S+)\s+(.*?)（(\d+) ms）")


def main() -> int:
    suite = Suite("perf")
    if not REAL_CONFIG.exists():
        suite.note("真实 config.yaml 不存在，无法执行真实源性能验证")
        return suite.finish()
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    sources = raw.get("sources") or []
    enabled = [item for item in sources if item.get("enabled") is True]
    suite.note(f"真实启用源 {len(enabled)} 个: {[item.get('id') for item in enabled]}")

    TEMPROOT.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="aihot-m1-perf-", dir=TEMPROOT))
    cfg = write_config(workspace, sources, window_hours=24)
    db = workspace / "data" / "aihot.db"

    result = run_cli(["fetch", "--config", str(cfg), "--limit", "10"], timeout=180)
    stats = [
        {
            "mark": match.group(1),
            "id": match.group(2),
            "detail": match.group(3),
            "ms": int(match.group(4)),
        }
        for match in LINE_RE.finditer(result.stdout)
    ]
    counts = db_counts(db)
    runs = db_query(db, "SELECT * FROM runs ORDER BY rowid DESC LIMIT 1")
    status = runs[0]["status"] if runs else "<none>"
    stage = json.loads(runs[0]["stage_stats"]) if runs else {}
    ok_sources = [stat["id"] for stat in stats if stat["mark"].strip() == "OK"]
    summed = sum(
        int(stat["detail"].split(" ")[0]) for stat in stats if stat["mark"].strip() == "OK"
    )
    suite.check(
        "4-5源全量采集2分钟内",
        result.rc == 0
        and result.seconds < 120
        and len(stats) == len(enabled)
        and len(ok_sources) == len(enabled)
        and stage.get("fetched") == summed
        and counts["items"] == stage.get("saved", -1),
        f"实测 {result.seconds:.1f}s（阈值 120s），源 {len(ok_sources)}/{len(enabled)} OK，"
        f"抓取 {stage.get('fetched')} 条 → 新增 {stage.get('saved')} 条"
        f"（批内/跨源去重 {stage.get('fetched', 0) - stage.get('saved', 0)}）"
        f"→ 库内 {counts['items']} 条，runs.status={status}",
        evidence=result.text,
    )
    suite.evidence_json("per-source-stats.json", stats)

    rows = db_query(db, "SELECT * FROM items ORDER BY source_id, id")
    problems: list[str] = []
    for row in rows:
        for field in ("title", "url", "published_at", "fetched_at", "first_seen_at", "lang"):
            if not row[field]:
                problems.append(f"{row['source_id']}#{row['id']} 缺 {field}")
        try:
            extra = json.loads(row["extra"])
            if not isinstance(extra, dict):
                problems.append(f"{row['source_id']}#{row['id']} extra 非对象")
        except json.JSONDecodeError:
            problems.append(f"{row['source_id']}#{row['id']} extra 非法 JSON")
        if row["url_hash"] != hashlib.sha1(row["url"].encode("utf-8")).hexdigest():
            problems.append(f"{row['source_id']}#{row['id']} url_hash 不一致")
    suite.check(
        "5-真实数据字段完整+extra合法JSON",
        bool(rows) and not problems,
        f"检查 {len(rows)} 行，问题 {len(problems)} 个" + (f"：{problems[:3]}" if problems else ""),
    )

    per_source = db_query(
        db,
        "SELECT source_id, COUNT(*) AS n, MIN(published_at) AS oldest "
        "FROM items GROUP BY source_id",
    )
    suite.evidence_json("per-source-rows.json", per_source)
    hn_rows = db_query(db, "SELECT extra FROM items WHERE source_id = 'hackernews' LIMIT 3")
    hn_keys_ok = all(
        {"points", "num_comments", "hn_id", "hn_url"} <= set(json.loads(row["extra"]))
        for row in hn_rows
    )
    suite.check(
        "5-HN条目extra含points/评论数",
        bool(hn_rows) and hn_keys_ok,
        f"HN 抽样 {len(hn_rows)} 条，extra 键包含 points/num_comments/hn_id/hn_url={hn_keys_ok}",
    )

    second = run_cli(["fetch", "--config", str(cfg), "--limit", "0"], timeout=180)
    second_saved = _saved(second.stdout)
    after = db_counts(db)
    suite.check(
        "2-真实数据二次采集幂等",
        second.rc == 0
        and after["items"] == after["unique_keys"]
        and after["items"] >= counts["items"],
        f"二次新增={second_saved} 条（期间真实新发布可能>0），"
        f"唯一键={after['unique_keys']} 总={after['items']}",
        evidence=second.stdout,
    )

    before_real = db_counts(REAL_DB)
    dry = run_cli(["fetch", "--dry-run", "--limit", "5", "--config", str(REAL_CONFIG)], timeout=180)
    after_real = db_counts(REAL_DB)
    suite.check(
        "4b-真实config dry-run真实库零写入",
        dry.rc == 0 and before_real == after_real and "dry-run，不写库" in dry.stdout,
        f"rc={dry.rc} 真实库快照 {before_real['items']}/{before_real['runs']} → "
        f"{after_real['items']}/{after_real['runs']}",
        evidence=dry.stdout,
    )
    secrets = _real_secrets()
    combined = dry.stdout + dry.stderr
    leaked = [name for name, value in secrets.items() if value and value in combined]
    suite.check(
        "4c-真实运行输出不含密钥",
        not leaked,
        f"扫描 {len(secrets)} 个真实密钥：泄漏 {leaked or '无'}",
        evidence=dry.text,
    )
    return suite.finish()


def _saved(stdout: str) -> str:
    match = re.search(r"新增 (\d+) 条", stdout)
    return match.group(1) if match else "?"


def _real_secrets() -> dict[str, str]:
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    llm = raw.get("llm") or {}
    wecom = (raw.get("push") or {}).get("wecom") or {}
    return {
        "llm.api_key": str(llm.get("api_key") or ""),
        "wecom.webhook": str(wecom.get("webhook") or ""),
    }


if __name__ == "__main__":
    raise SystemExit(main())
