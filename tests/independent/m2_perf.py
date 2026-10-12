"""M2 验收 8：性能（全量加工 < 12 分钟）。

- 真实 176 条：复制真实库 → 重置加工 → 假 LLM（动态合法响应，0.15s/次模拟延迟）全量 enrich；
- 合成 400 条且关闭标题合并（最坏批次量：≈400 簇）；
- 成本折算方法说明（真实样本见 m2_real_llm.py）。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    EVIDENCE,
    REAL_DB,
    FakeLLM,
    Suite,
    copy_real_db,
    db_one,
    default_pipeline,
    real_db_snapshot,
    reset_enrichment,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "perf"
LIMIT_SECONDS = 12 * 60
LATENCY = 0.15  # 每次 LLM 调用模拟延迟（秒）


def dynamic_content(body: dict | None) -> str:
    """按请求内容生成合法响应：聚类精修返回全单例；条目加工按 listing 条数返回。"""
    messages = (body or {}).get("messages") or []
    system = messages[0].get("content", "") if messages else ""
    user = messages[1].get("content", "") if len(messages) > 1 else ""
    if "事件聚类助手" in system:
        ids = re.findall(r"^(\d+)\. \[", user, re.MULTILINE)
        return json.dumps({"groups": [[int(value)] for value in ids]})
    indexes = re.findall(r"^\[(\d+)\] 来源：", user, re.MULTILINE)
    return json.dumps({"items": [
        {"index": int(value), "cn_title": f"中文标题 {value}", "cn_summary": "一句话摘要",
         "category": "模型发布", "importance": 60, "is_ai": True}
        for value in indexes
    ]})


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def synthetic_items(count: int) -> list[dict]:
    sources = ["qbitai", "infoq_cn", "techcrunch_ai", "openai_news", "hackernews"]
    topics = [
        "OpenAI ships GPT-6 Sol", "Anthropic releases Claude Opus 6",
        "Meta open-sources Llama-9", "DeepSeek publishes V5 report",
        "Google announces Gemini 8 Ultra", "Mistral launches Le Chat Enterprise",
        "Nvidia unveils Rubin GPU", "Microsoft adds Copilot agents to Office",
        "Alibaba releases Qwen 4", "Amazon launches Nova 3 models",
    ]
    items = []
    for index in range(count):
        topic = topics[index % len(topics)]
        items.append({
            "source_id": sources[index % len(sources)],
            "title": f"{topic} #编号{index} 独家报道",
            "url": f"https://example.com/perf/{index}",
            "published_at": ts((index % 60) * 0.4),
            "raw_summary": f"摘要 {index}",
            "lang": "zh" if index % 2 else "en",
            "extra": (
                {"points": index % 300} if sources[index % len(sources)] == "hackernews" else {}
            ),
        })
    return items


def run_full(suite: Suite, slug: str, *, source_db: Path | None, items: list[dict] | None,
              limit: int, similarity: float = 88.0) -> dict:
    base = workspace(f"{WS}-{slug}")
    db_path = base / "data" / "aihot.db"
    if source_db is not None:
        copy_real_db(db_path)
        total = reset_enrichment(db_path)
    else:
        total = seed_items(db_path, items or [])
    fake = FakeLLM()
    fake.set_generator(dynamic_content, usage={"prompt_tokens": 8000, "completion_tokens": 1500},
                       delay=LATENCY)
    with fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(similarity=similarity),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-perf", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        start = time.perf_counter()
        result = run_cli(["enrich", "--config", str(cfg), "--limit", str(limit), "--top", "5"],
                         timeout=LIMIT_SECONDS + 120)
        seconds = time.perf_counter() - start
        calls = fake.request_count
    stats = {
        "total_items": total,
        "seconds": round(seconds, 1),
        "llm_calls": calls,
        "rc": result.rc,
        "enriched": db_one(db_path, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL"),
        "filtered": db_one(db_path, "SELECT COUNT(*) FROM items WHERE category='过滤'"),
        "clusters": db_one(db_path, "SELECT COUNT(*) FROM clusters"),
    }
    suite.evidence(f"{slug}-cli.txt", result.text)
    return stats


def main() -> int:
    suite = Suite("perf")

    # ---------- 8a：真实 176 条全量 ----------
    print("== 8a 真实 176 条全量加工 ==")
    real = run_full(suite, "real", source_db=REAL_DB, items=None, limit=400)
    suite.evidence_json("8a-real.json", real)
    suite.check(
        "8a-真实数据全量 < 12 分钟",
        real["rc"] == 0 and 0 < real["seconds"] < LIMIT_SECONDS
        and real["enriched"] == real["total_items"],
        f"{real['total_items']} 条 → {real['seconds']}s（阈值 {LIMIT_SECONDS}s），"
        f"LLM 调用 {real['llm_calls']} 次（每次 {LATENCY}s 模拟延迟），"
        f"加工 {real['enriched']} 条 / 过滤 {real['filtered']} 条 / {real['clusters']} 簇",
    )

    # ---------- 8b：合成 400 条（关闭标题合并 = 最坏批次量） ----------
    print("== 8b 合成 400 条（最多簇数）全量加工 ==")
    big = run_full(
        suite, "big400", source_db=None, items=synthetic_items(400), limit=1000, similarity=100
    )
    suite.evidence_json("8b-big400.json", big)
    suite.check(
        "8b-400 条全量 < 12 分钟（每簇 1 条的最坏情况）",
        big["rc"] == 0 and 0 < big["seconds"] < LIMIT_SECONDS
        and big["enriched"] + big["filtered"] == big["total_items"],
        f"{big['total_items']} 条 → {big['seconds']}s（阈值 {LIMIT_SECONDS}s），"
        f"LLM 调用 {big['llm_calls']} 次（≈{big['clusters']} 簇 / batch 15），"
        f"加工 {big['enriched']} 条 / 过滤 {big['filtered']} 条",
    )

    # ---------- 8c：成本折算（真实样本外推方法说明 + 假样本校验） ----------
    # 真实单价来自 config.yaml：¥2/M 输入 + ¥8/M 输出；真实 per-item token 见 m2_real_llm.py 证据
    real_llm_evidence = EVIDENCE / "real-llm" / "real-llm.json"
    extrapolation: dict = {"method": "真实小样本 token/条 × 每日条数 × 配置单价"}
    sample = {}
    if real_llm_evidence.exists():
        sample = (json.loads(real_llm_evidence.read_text(encoding="utf-8")) or {}).get("sample", {})
    per_item_in = sample.get("prompt_tokens_per_item", 0)
    per_item_out = sample.get("completion_tokens_per_item", 0)
    if per_item_in and per_item_out:
        daily_items = 200
        cost = (per_item_in * daily_items / 1e6 * 2.0) + (per_item_out * daily_items / 1e6 * 8.0)
        extrapolation.update({
            "per_item_prompt_tokens": per_item_in,
            "per_item_completion_tokens": per_item_out,
            "daily_items": daily_items,
            "estimated_daily_cost_cny": round(cost, 4),
            "estimated_daily_cost_cny_400_items": round(cost * 2, 4),
            "actual_sample_cost_cny": sample.get("cost_cny"),
        })
        suite.check(
            "8c-单条成本折算 < ¥1/天",
            cost < 1.0,
            f"真实样本 {per_item_in:.0f}+{per_item_out:.0f} token/条 × {daily_items} 条/天 "
            f"= ¥{cost:.4f}/天（阈值 ¥1）",
        )
    else:
        extrapolation["note"] = f"真实样本证据缺失或不可用: {real_llm_evidence}"
        suite.check(
            "8c-单条成本折算 < ¥1/天",
            False,
            f"无法折算：{real_llm_evidence} 缺失或无 token 数据（需先跑 m2_real_llm.py）",
        )
    suite.evidence_json("8c-cost-extrapolation.json", extrapolation)

    # ---------- 8d：config max_items 截断保护（--limit 缺省时） ----------
    base = workspace(f"{WS}-maxitems")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, synthetic_items(25))
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(max_items=10),
        data_dir=base / "data",
        log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-cap"},
    )
    capped = run_cli(["enrich", "--no-llm", "--config", str(cfg)])
    suite.evidence("8d-max-items-cap.txt", capped.text)
    suite.check(
        "8d-max_items 截断保护（25 条库，上限 10）",
        capped.rc == 0 and "待加工: 10 条（上限 10）" in capped.stdout
        and db_one(db_path, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL") == 10,
        "输出「待加工: 10 条（上限 10）」，库内仅前 10 条被加工",
    )

    suite.check(
        "8z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
