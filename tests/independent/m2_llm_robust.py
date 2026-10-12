"""M2 验收 3：LLM 加工健壮性（假 LLM 服务器注入坏数据，黑盒 CLI）。

每个场景都必须：不崩溃（无 traceback、rc ∈ {0,1}）、降级为原文、计入统计、落库完整。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
    Reply,
    Suite,
    db_query,
    default_pipeline,
    has_traceback,
    parse_stats,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "llm-robust"
ITEM_COUNT = 5


DISTINCT_TITLES = [
    "OpenAI ships GPT-6 Sol to all users",
    "Anthropic releases Claude Opus 6",
    "Meta open-sources Llama-9 family",
    "DeepSeek publishes V5 technical report",
    "Google announces Gemini 8 Ultra",
]


def fixture_items() -> list[dict]:
    return [
        {
            "source_id": "qbitai",
            "title": f"{title}（第 {index} 条）",
            "url": f"https://example.com/robust/{index}",
            "published_at": datetime.now(UTC).isoformat(),
            "raw_summary": f"raw summary {index}",
            "lang": "en",
        }
        for index, title in enumerate(DISTINCT_TITLES, 1)
    ]


def good_reply(count: int = ITEM_COUNT, **overrides: object) -> str:
    base = {
        "cn_title": "中文标题",
        "cn_summary": "中文摘要",
        "category": "模型发布",
        "importance": 88,
        "is_ai": True,
    }
    base.update(overrides)
    return json.dumps({"items": [{"index": index, **base} for index in range(1, count + 1)]})


def run_case(suite: Suite, case: dict) -> dict:
    name = case["name"]
    base = workspace(f"{WS}-{case['slug']}")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items())
    fake_ctx = FakeLLM() if case.get("replies") is not None else None
    fake = fake_ctx.__enter__() if fake_ctx else None
    if fake is not None:
        for reply in case["replies"]:
            fake.enqueue(reply)
    try:
        base_url = fake.base_url if fake is not None else case["base_url"]
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={
                "base_url": base_url,
                "api_key": "sk-fake-robust",
                "batch_size": ITEM_COUNT,
                "daily_cost_limit_cny": 100.0,
            },
        )
        result = run_cli(
            ["enrich", "--config", str(cfg), "--limit", "50", "--top", "2"],
            timeout=case.get("timeout", 180.0),
        )
        requests = fake.request_count if fake is not None else 0
    finally:
        if fake_ctx is not None:
            fake_ctx.stop()

    rows = db_query(
        db_path,
        "SELECT title, cn_title, cn_summary, category, importance, enriched_at"
        " FROM items ORDER BY id",
    )
    stats = parse_stats(result.stdout)
    suite.evidence(
        f"{name}.txt",
        result.text
        + f"\n--- llm requests: {requests} ---\n"
        + json.dumps(rows, ensure_ascii=False, indent=2),
    )
    return {"rows": rows, "stats": stats, "result": result, "requests": requests}


def main() -> int:
    suite = Suite("llm-robust")
    cases: list[dict] = [
        {"slug": "normal", "name": "3a-正常JSON", "replies": [Reply(content=good_reply())],
         "expect_rc": 0, "expect_hit": True},
        {"slug": "fenced",
         "name": "3b-Markdown围栏包裹",
         "replies": [Reply(content="```json\n" + good_reply() + "\n```")],
         "expect_rc": 0, "expect_hit": True},
        {"slug": "truncated", "name": "3c-截断JSON",
         "replies": [Reply(content='{"items": [{"index": 1, "cn_title": "x"')],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "illegal", "name": "3d-非法JSON",
         "replies": [Reply(content="这不是 JSON，哈哈")],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "empty-items", "name": "3e-items为空数组",
         "replies": [Reply(content='{"items": []}')],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "index-missing",
         "name": "3f-index缺失（1条无index）",
         "replies": [Reply(content=json.dumps({"items": [
             {"index": 1, "cn_title": "a", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"cn_title": "b", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 3, "cn_title": "c", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 4, "cn_title": "d", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 5, "cn_title": "e", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True}]}))],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "index-dup", "name": "3g-index重复",
         "replies": [Reply(content=json.dumps({"items": [
             {"index": 1, "cn_title": "a", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 1, "cn_title": "b", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 3, "cn_title": "c", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 4, "cn_title": "d", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 5, "cn_title": "e", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True}]}))],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "index-range", "name": "3h-index越界（0与99）",
         "replies": [Reply(content=json.dumps({"items": [
             {"index": 0, "cn_title": "a", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True},
             {"index": 99, "cn_title": "b", "cn_summary": "s", "category": "模型发布",
              "importance": 50, "is_ai": True}]}))],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "bad-category", "name": "3i-category非法值",
         "replies": [Reply(content=good_reply(category="瞎写的分类"))],
         "expect_rc": 0, "expect_hit": True, "expect_category": "其他"},
        {"slug": "not-ai", "name": "3j-is_ai为false",
         "replies": [Reply(content=good_reply(importance=95, is_ai=False))],
         "expect_rc": 0, "expect_hit": True, "expect_category": "其他",
         "expect_max_importance": 15},
        {"slug": "http-500", "name": "3k-HTTP500",
         "replies": [Reply(status=500, content=None), Reply(status=500, content=None),
                     Reply(status=500, content=None)],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "empty-content", "name": "3l-choices为空（结构异常）",
         "replies": [Reply(content=None)],
         "expect_rc": 1, "expect_hit": False},
        {"slug": "timeout", "name": "3m-超时（60s 读超时后重试仍失败）",
         "replies": [Reply(content=good_reply(), delay=61.0),
                     Reply(status=500, content=None), Reply(status=500, content=None)],
         "expect_rc": 1, "expect_hit": False, "timeout": 300.0},
        {"slug": "refused", "name": "3n-连接拒绝",
         "replies": None, "base_url": "http://127.0.0.1:1/v1",
         "expect_rc": 1, "expect_hit": False},
    ]

    for case in cases:
        print(f"\n-- {case['name']} --")
        out = run_case(suite, case)
        text = out["result"].combined
        ok_rc = out["result"].rc == case["expect_rc"]
        no_tb = not has_traceback(text)
        rows = out["rows"]
        all_written = len(rows) == ITEM_COUNT and all(row["enriched_at"] for row in rows)
        hit = any(
            row["cn_title"] and row["cn_title"] != row["title"] for row in rows
        )
        fallback = all(
            row["category"] == "其他" and row["importance"] == 30 for row in rows
        )
        stats = out["stats"]
        counted = "failed_batches" in stats

        suite.check(
            f"{case['name']}-不崩溃",
            ok_rc and no_tb,
            f"rc={out['result'].rc}（期望 {case['expect_rc']}）traceback={has_traceback(text)} "
            f"LLM请求={out['requests']}",
        )
        suite.check(
            f"{case['name']}-落库完整",
            all_written,
            f"items={len(rows)}/{ITEM_COUNT}，"
            f"enriched_at 全部非空={all(row['enriched_at'] for row in rows)}",
        )
        suite.check(
            f"{case['name']}-统计计入",
            counted,
            f"批次={stats.get('batches')} 失败={stats.get('failed_batches')} "
            f"token={stats.get('prompt_tokens')}+{stats.get('completion_tokens')} "
            f"成本=¥{stats.get('cost_cny')}",
        )
        if case["expect_hit"]:
            suite.check(f"{case['name']}-成功加工", hit, f"cn_title 被改写={hit}")
        else:
            categories = sorted({row["category"] for row in rows})
            detail = f"全部 category=其他 且 importance=30（原文降级）={fallback}"
            if not fallback:
                detail += f"；实际 category={categories}"
            suite.check(f"{case['name']}-降级为原文", fallback, detail)
        if "expect_category" in case:
            got = {row["category"] for row in rows}
            suite.check(
                f"{case['name']}-category 兜底",
                got == {case["expect_category"]},
                f"期望 {case['expect_category']}，实际 {sorted(got)}",
            )
        if "expect_max_importance" in case:
            got_max = max(row["importance"] or 0 for row in rows)
            suite.check(
                f"{case['name']}-is_ai=false 降权",
                got_max <= case["expect_max_importance"],
                f"importance 最大值={got_max}（期望 ≤{case['expect_max_importance']}）",
            )

    suite.check(
        "3z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
