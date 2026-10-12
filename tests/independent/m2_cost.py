"""M2 验收 5：成本核算与上限（假 usage 数据，黑盒 CLI）。

- 成本 = prompt/1e6*price_in + completion/1e6*price_out（与独立复算比对）；
- 超过 llm.daily_cost_limit_cny 时停止后续批次并标记 cost_limit_hit，且不丢数据。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
    Reply,
    Suite,
    db_one,
    db_query,
    default_pipeline,
    parse_stats,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "cost"
BATCH = 5
PRICE_IN = 2.0
PRICE_OUT = 8.0


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def fixture_items(count: int) -> list[dict]:
    topics = [
        "OpenAI ships GPT-6 Sol", "Anthropic releases Claude Opus 6",
        "Meta open-sources Llama-9", "DeepSeek publishes V5 report",
        "Google announces Gemini 8 Ultra", "Mistral launches Le Chat Enterprise",
        "Cohere releases Command R10", "Perplexity ships Comet 2",
        "Hugging Face launches datasets v9", "arXiv benchmarks multimodal agents",
        "Nvidia unveils Rubin GPUs", "Microsoft adds Copilot agents to Office",
        "Apple announces on-device LLM", "Amazon launches Nova 3 models",
        "Alibaba releases Qwen 4",
    ]
    return [
        {
            "source_id": "qbitai",
            "title": f"{topics[index % len(topics)]}（第 {index} 条）",
            "url": f"https://example.com/cost/{index}",
            "published_at": ts(index * 0.4),
            "lang": "en",
        }
        for index in range(count)
    ]


def batch_reply(count: int, start: int = 1) -> str:
    return json.dumps({"items": [
        {"index": index, "cn_title": f"中文标题 {index}", "cn_summary": f"摘要 {index}",
         "category": "模型发布", "importance": 70, "is_ai": True}
        for index in range(start, start + count)
    ]})


def run_case(
    suite: Suite,
    *,
    slug: str,
    item_count: int,
    cost_limit: float,
    usage: dict[str, int],
    batches_to_reply: int,
) -> dict:
    base = workspace(f"{WS}-{slug}")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items(item_count))
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={
                "base_url": fake.base_url, "api_key": "sk-fake-cost", "batch_size": BATCH,
                "daily_cost_limit_cny": cost_limit,
            },
        )
        for _ in range(batches_to_reply):
            fake.enqueue(Reply(content=batch_reply(BATCH), usage=usage))
        fake.set_default(json.dumps({"items": []}))
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "100", "--top", "3"])
        calls = fake.request_count
    rows = db_query(
        db_path,
        "SELECT url, title, cn_title, category, importance, enriched_at FROM items ORDER BY id",
    )
    stats = parse_stats(result.stdout)
    suite.evidence(
        f"{slug}.txt",
        result.text + f"\n--- llm requests: {calls} ---\n"
        + json.dumps(rows, ensure_ascii=False, indent=2),
    )
    return {"rows": rows, "stats": stats, "result": result, "calls": calls, "db": db_path}


def main() -> int:
    suite = Suite("cost")

    # ---------- 5a：成本核算正确（2 批 × usage 100k/50k） ----------
    usage = {"prompt_tokens": 100_000, "completion_tokens": 50_000, "total_tokens": 150_000}
    expected_per_call = 100_000 / 1e6 * PRICE_IN + 50_000 / 1e6 * PRICE_OUT  # 0.6
    case = run_case(suite, slug="5a-calc", item_count=10, cost_limit=100.0,
                    usage=usage, batches_to_reply=2)
    stats = case["stats"]
    suite.check(
        "5a-成本核算（2 批 usage 100k/50k）",
        stats.get("prompt_tokens") == 200_000
        and stats.get("completion_tokens") == 100_000
        and abs(stats.get("cost_cny", 0) - expected_per_call * 2) < 5e-4,
        f"token={stats.get('prompt_tokens')}+{stats.get('completion_tokens')}，"
        f"CLI 成本=¥{stats.get('cost_cny')}，独立复算=¥{expected_per_call * 2:.4f}",
    )
    suite.check(
        "5a-批次统计一致",
        stats.get("batches") == 2 and stats.get("failed_batches") == 0,
        f"批次={stats.get('batches')} 失败={stats.get('failed_batches')}，LLM 请求={case['calls']}",
    )
    suite.check(
        "5a-未触发上限",
        "已达每日成本上限" not in case["result"].stdout,
        "输出无成本上限告警",
    )

    # ---------- 5b：超上限停止后续批次（每批 0.2，限额 0.3 → 2 批后停） ----------
    usage_small = {"prompt_tokens": 50_000, "completion_tokens": 12_500, "total_tokens": 62_500}
    case2 = run_case(suite, slug="5b-limit", item_count=15, cost_limit=0.3,
                     usage=usage_small, batches_to_reply=3)
    stats2 = case2["stats"]
    rows2 = case2["rows"]
    fallback_rows = [row for row in rows2 if row["cn_title"] == row["title"]]
    enriched_rows = [row for row in rows2 if row["cn_title"] != row["title"]]
    suite.check(
        "5b-超上限停止后续批次",
        stats2.get("batches") == 2 and case2["calls"] == 2,
        f"批次={stats2.get('batches')}（期望 2）LLM 请求={case2['calls']}；"
        f"成本=¥{stats2.get('cost_cny')}（限额 0.3）",
    )
    suite.check(
        "5b-cost_limit_hit 标记与告警",
        "已达每日成本上限" in case2["result"].stdout
        and "部分条目降级为原文" in case2["result"].stdout,
        "输出含「已达每日成本上限…部分条目降级为原文」",
    )
    suite.check(
        "5b-不丢数据（15 条全部落库、10 加工 + 5 降级）",
        len(rows2) == 15
        and all(row["enriched_at"] for row in rows2)
        and len(enriched_rows) == 10
        and len(fallback_rows) == 5
        and all(row["category"] == "其他" and row["importance"] == 30 for row in fallback_rows),
        f"总 {len(rows2)} 条，加工 {len(enriched_rows)}，降级 {len(fallback_rows)}，"
        "全部 enriched_at 非空",
    )
    suite.check(
        "5b-写回行数与队列一致",
        "已写回: 15 条加工" in case2["result"].stdout,
        f"输出行：{[line for line in case2['result'].stdout.splitlines() if '已写回' in line]}",
    )

    # ---------- 5c：成本上限行为边界（限额小于单批成本 → 首批仍执行，0 批之后停止） ----------
    case3 = run_case(suite, slug="5c-first-batch", item_count=10, cost_limit=0.1,
                     usage=usage_small, batches_to_reply=2)
    stats3 = case3["stats"]
    priced = stats3.get("cost_cny", 0)
    suite.check(
        "5c-上限小于单批成本：仅首批执行且标记 hit",
        stats3.get("batches") == 1 and case3["calls"] == 1
        and "已达每日成本上限" in case3["result"].stdout
        and db_one(case3["db"], "SELECT COUNT(*) FROM items WHERE enriched_at IS NULL") == 0,
        f"批次={stats3.get('batches')} 请求={case3['calls']} 成本=¥{priced}"
        "（说明：上限检查在批次前，故首批必然执行；剩余条目降级不丢）",
    )

    suite.check(
        "5z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
