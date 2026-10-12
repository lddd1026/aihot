"""M2 验收 4：缓存与幂等（第二次零 LLM 调用 / 队列清空 / dry-run 不写库 / 淘汰条目不再入队）。"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
    Suite,
    db_one,
    db_query,
    default_pipeline,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "cache"
BATCH = 5


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def fixture_items() -> list[dict]:
    kept = [
        "OpenAI ships GPT-6 Sol",
        "Anthropic releases Claude Opus 6",
        "Meta open-sources Llama-9",
        "DeepSeek publishes V5 report",
        "Google announces Gemini 8 Ultra",
        "Mistral launches Le Chat Enterprise",
        "Cohere releases Command R10",
        "Perplexity ships Comet 2 browser",
        "Hugging Face launches datasets v9",
        "arXiv benchmarks multimodal agents",
    ]
    items = [
        {
            "source_id": "qbitai",
            "title": f"{title}（第 {index} 条）",
            "url": f"https://example.com/cache/{index}",
            "published_at": ts(index * 0.5),
            "raw_summary": f"summary {index}",
            "lang": "en",
        }
        for index, title in enumerate(kept, 1)
    ]
    # 预筛淘汰：招聘（所有源）+ 门控源与 AI 无关
    items.append({
        "source_id": "qbitai", "title": "招聘｜大模型算法工程师", "url": "https://example.com/cache/hire",
        "published_at": ts(2), "raw_summary": "简历投递", "lang": "zh",
    })
    items.append({
        "source_id": "hackernews", "title": "Rust 1.99 released with faster builds",
        "url": "https://example.com/cache/offtopic", "published_at": ts(2), "lang": "en",
    })
    return items


def batch_reply(count: int, start: int = 1) -> str:
    return json.dumps({"items": [
        {"index": index, "cn_title": f"中文标题 {index}", "cn_summary": f"摘要 {index}",
         "category": "模型发布", "importance": 70, "is_ai": True}
        for index in range(start, start + count)
    ]})


def main() -> int:
    suite = Suite("cache")
    base = workspace(f"{WS}-run")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items())

    with FakeLLM() as fake:
        # 改配置指向假服务后运行
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-cache", "batch_size": BATCH,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(batch_reply(BATCH))
        fake.enqueue_content(batch_reply(5))
        run1 = run_cli(["enrich", "--config", str(cfg), "--limit", "50", "--top", "2"])
        calls1 = fake.request_count
        run2 = run_cli(["enrich", "--config", str(cfg), "--limit", "50", "--top", "2"])
        calls2 = fake.request_count - calls1
    suite.evidence("4a-run1.txt", run1.text)
    suite.evidence("4b-run2.txt", run2.text)

    suite.check(
        "4a-首轮加工并调用 LLM",
        run1.rc == 0 and calls1 == 2,
        f"rc={run1.rc}，LLM 请求={calls1}（期望 2 批）",
    )
    enriched = db_one(db_path, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL")
    suite.check(
        "4b-队列清空",
        enriched == 12,
        f"enriched_at 非空={enriched}/12（10 加工 + 2 过滤标记）",
    )
    suite.check(
        "4c-第二轮零 LLM 调用",
        calls2 == 0 and "没有待加工的条目" in run2.stdout,
        f"第二轮 LLM 请求={calls2}，输出含「没有待加工的条目」="
        f"{'没有待加工的条目' in run2.stdout}",
    )
    filtered = db_query(
        db_path,
        "SELECT url, category, enriched_at FROM items WHERE category = '过滤' ORDER BY url",
    )
    suite.check(
        "4d-预筛淘汰条目已标记且不回队",
        len(filtered) == 2 and all(row["enriched_at"] for row in filtered),
        f"过滤条目={[row['url'].rsplit('/', 1)[-1] for row in filtered]}；"
        "第二轮输出无任何 LLM 调用（见 4c）",
    )

    # dry-run：不写库、不消耗队列
    base2 = workspace(f"{WS}-dry")
    db2 = base2 / "data" / "aihot.db"
    seed_items(db2, fixture_items())
    before = db_query(db2, "SELECT enriched_at, category FROM items ORDER BY id")
    clusters_before = db_one(db2, "SELECT COUNT(*) FROM clusters")
    with FakeLLM() as fake2:
        cfg2 = write_config(
            base2 / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base2 / "data",
            log_dir=base2 / "logs",
            llm={"base_url": fake2.base_url, "api_key": "sk-fake-dry", "batch_size": BATCH,
                 "daily_cost_limit_cny": 100.0},
        )
        fake2.enqueue_content(batch_reply(BATCH))
        fake2.enqueue_content(batch_reply(5))
        dry = run_cli(["enrich", "--config", str(cfg2), "--limit", "50", "--top", "2", "--dry-run"])
        dry_calls = fake2.request_count
    after = db_query(db2, "SELECT enriched_at, category FROM items ORDER BY id")
    clusters_after = db_one(db2, "SELECT COUNT(*) FROM clusters")
    suite.evidence("4e-dry-run.txt", dry.text)
    suite.check(
        "4e-dry-run 不写库",
        dry.rc == 0 and before == after and clusters_before == clusters_after == 0,
        f"rc={dry.rc}；items 前后一致={before == after}；"
        f"clusters {clusters_before}→{clusters_after}；"
        f"dry-run 仍调用 LLM {dry_calls} 次（未写库）",
    )
    suite.check(
        "4f-dry-run 输出声明",
        "（dry-run，未写库）" in dry.stdout,
        "输出含「（dry-run，未写库）」",
    )

    # dry-run 之后真实运行：队列未消耗
    with FakeLLM() as fake3:
        cfg3 = write_config(
            base2 / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base2 / "data",
            log_dir=base2 / "logs",
            llm={"base_url": fake3.base_url, "api_key": "sk-fake-dry2", "batch_size": BATCH,
                 "daily_cost_limit_cny": 100.0},
        )
        fake3.enqueue_content(batch_reply(BATCH))
        fake3.enqueue_content(batch_reply(5))
        real = run_cli(["enrich", "--config", str(cfg3), "--limit", "50", "--top", "2"])
    enriched2 = db_one(db2, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL")
    suite.evidence("4g-after-dry-real.txt", real.text)
    suite.check(
        "4g-dry-run 未消耗队列（随后真实运行仍加工 12 条）",
        real.rc == 0 and enriched2 == 12,
        f"rc={real.rc}，enriched_at 非空={enriched2}/12",
    )

    suite.check(
        "4z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
