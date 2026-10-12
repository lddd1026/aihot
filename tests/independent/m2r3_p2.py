"""M2 回归（第 3 轮）· P2 补充复验：NPU/VLM/MLOps 词表 + --no-llm 的 runs 字段完整性。

5a 词表：NPU/NPUs/MLOps/VLM（词边界形式）过门槛；控制组仍淘汰
5b --no-llm 的 runs 记录字段与 LLM 路径一致（batches/cost_cny 等齐全且为 0）
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    Suite,
    db_query,
    default_pipeline,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "r3-p2"

STAT_FIELDS = {
    "no_llm", "refine_calls", "refine_failed", "refine_rejected", "batches", "succeeded",
    "failed_batches", "fallback_items", "prompt_tokens", "completion_tokens", "cost_cny",
    "cost_limit_hit", "time_budget_hit",
}


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def seed(path: Path, rows: list[tuple[str, str]], *, prefix: str) -> None:
    seed_items(path, [
        {
            "source_id": source, "title": title, "url": f"https://example.com/{prefix}/{index}",
            "published_at": ts(1 + index), "raw_summary": "", "lang": "zh",
        }
        for index, (source, title) in enumerate(rows)
    ])


def enrich_runs(db_path: Path) -> list[dict]:
    out = []
    rows = db_query(db_path, "SELECT run_id, status, stage_stats FROM runs ORDER BY started_at")
    for row in rows:
        stats = json.loads(row["stage_stats"] or "{}")
        if stats.get("stage") == "enrich":
            out.append({"run_id": row["run_id"], "status": row["status"], **stats})
    return out


def case_5a(suite: Suite) -> None:
    base = workspace(f"{WS}-5a")
    db_path = base / "data" / "aihot.db"
    rows = [
        ("hackernews", "Intel launches new NPU for laptops"),
        ("hackernews", "Intel launches new NPUs for laptops"),
        ("hackernews", "Show HN: Npunlock – Run custom C kernels for Intel NPUs"),
        ("hackernews", "MLOps platforms compared for 2026"),
        ("hackernews", "A survey of VLM architectures"),
        ("hackernews", "Rust 1.99 released with faster builds"),
    ]
    seed(db_path, rows, prefix="r3p2a")
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(gate=["hackernews"], refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-r3"},
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    kept = {
        row["url"].rsplit("/", 1)[-1]
        for row in db_query(db_path, "SELECT url FROM items WHERE category != '过滤'")
    }
    dropped = {
        row["url"].rsplit("/", 1)[-1]
        for row in db_query(db_path, "SELECT url FROM items WHERE category = '过滤'")
    }
    suite.evidence("5a-keywords.txt", result.text + "\n" + json.dumps(
        {"kept": sorted(kept), "dropped": sorted(dropped)}, ensure_ascii=False, indent=2))
    for index, title in enumerate(rows[:5]):
        suite.check(
            f"5a-过门槛：{title}",
            str(index) in kept,
            f"保留={str(index) in kept}",
        )
    suite.check(
        "5a-控制组仍淘汰（无 AI 关键词）",
        "5" in dropped,
        f"Rust 1.99 淘汰={'5' in dropped}",
    )


def case_5b(suite: Suite) -> None:
    base = workspace(f"{WS}-5b")
    db_path = base / "data" / "aihot.db"
    seed(db_path, [
        ("qbitai", "OpenAI ships GPT-6 Sol"),
        ("openai_news", "Anthropic releases Claude Opus 6"),
    ], prefix="r3p2b")
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-r3"},
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    records = enrich_runs(db_path)
    record = records[0] if records else {}
    suite.evidence("5b-no-llm-runs.txt", result.text + "\n"
                   + json.dumps(records, ensure_ascii=False, indent=2))
    missing = sorted(STAT_FIELDS - set(record))
    suite.check(
        "5b---no-llm 统计字段齐备（与 LLM 路径同构）",
        record.get("no_llm") is True and not missing,
        f"缺失字段={missing or '无'}；no_llm={record.get('no_llm')}",
    )
    suite.check(
        "5b---no-llm 成本/批次为 0 且可读",
        float(record.get("cost_cny") or 0) == 0.0
        and record.get("batches") == 0
        and record.get("succeeded") == 0
        and record.get("prompt_tokens") == 0,
        f"cost={record.get('cost_cny')} batches={record.get('batches')} "
        f"succeeded={record.get('succeeded')} tokens={record.get('prompt_tokens')}",
    )
    suite.check(
        "5b-基础计数正确（enriched=2/filtered=0）",
        record.get("enriched") == 2 and record.get("filtered") == 0
        and record.get("prefilter_kept") == 2,
        f"enriched={record.get('enriched')} filtered={record.get('filtered')} "
        f"kept={record.get('prefilter_kept')}",
    )


def main() -> int:
    suite = Suite("p2-supplement")
    print("== 5a 词表（NPU/NPUs/MLOps/VLM） ==")
    case_5a(suite)
    print("\n== 5b --no-llm runs 字段完整性 ==")
    case_5b(suite)
    suite.check(
        "5z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
