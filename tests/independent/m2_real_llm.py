"""M2 验收 8/②：真实 DeepSeek 链路小样本验证（成本上限守卫 ¥0.2）+ 翻译摘要抽检 20 条。

安全：
- 真实 key 不落盘（临时配置用 api_key_env，key 只走环境变量），不打印；
- 临时库（真实库副本）内操作；不启用任何推送渠道（enrich 本身不推送）。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    REAL_CONFIG,
    REAL_DB,
    Suite,
    copy_real_db,
    db_query,
    parse_stats,
    real_db_snapshot,
    reset_enrichment,
    run_cli,
    workspace,
    write_config,
)

WS = "real-llm"
BUDGET = 0.2  # 允许的真实调用总额（元）
CALL_ITEMS = 6
CATEGORIES = {
    "模型发布", "产品更新", "研究突破", "开源工具", "融资商业", "政策监管", "行业动态", "其他",
}

# 翻译/摘要抽检人工裁定（条目 id → (裁定, 依据)）；样本见 real-llm/sample-20.json
TRANSLATION_VERDICTS: dict[int, tuple[str, str]] = {
    29: ("通过", "Ema 融资 7700 万美元，事实与数字准确"),
    30: ("通过", "希腊总理谈 AI，语义准确"),
    32: ("通过", "Snorkel 估值 35 亿美元，数字准确"),
    33: ("通过", "高通两款 AI 手机芯片，准确"),
    34: ("通过", "Meta 承认 Muse 与 OpenClaw 相似，准确"),
    35: ("通过", "GPT-6 Sol 与 Luna 发布，准确"),
    36: ("通过", "Opus 5.5 发布，准确"),
    37: ("通过", "AstroForge 航天器 AI 控制，准确"),
    42: ("通过", "数据中心争议：按摘要补全了「宾州/两年」，属改写但无误导"),
    43: ("通过", "OpenAI 支援乌克兰网络防御，准确"),
    44: ("通过", "Grab 与 OpenAI 合作，准确"),
    45: ("不通过", "原文「Better prompt caching for GPT-6」被写成「GPT-6 Sol 和 Luna 发布」"
                  "——同簇 10 条共享了别的条目的加工结果（根因见 dedup D-A 过度合并）"),
    46: ("通过", "Introducing GPT-6 Sol and Luna，准确"),
    48: ("通过", "The Download 讨论 AI 泡沫，准确"),
    1: ("通过", "联想天禧 AI 云栖大会，准确"),
    2: ("通过", "商汤异构混推，准确"),
    3: ("通过", "汇智智能 Hellome，准确"),
    4: ("通过", "达卯科技融资与算电协同，准确"),
    5: ("通过", "它石智航具身智能规模化，准确"),
    6: ("通过", "新马智能 AutoOmni 2.0 端侧全模态模型，准确"),
}


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def translation_sample(suite: Suite) -> dict:
    """抽检 20 条（优先英文原文→中文加工），逐条人工裁定。"""
    rows = db_query(
        REAL_DB,
        "SELECT id, source_id, title, category, importance, cn_title, cn_summary"
        " FROM items WHERE enriched_at IS NOT NULL AND category != '过滤' ORDER BY id",
    )
    english = [row for row in rows if not has_cjk(row["title"]) and has_cjk(row["cn_title"])]
    others = [row for row in rows if row not in english]
    sample = (english[:14] + others[:6])[:20]
    judged = []
    for row in sample:
        verdict, basis = TRANSLATION_VERDICTS.get(row["id"], ("未核验", ""))
        judged.append({
            "id": row["id"], "source": row["source_id"], "title": row["title"],
            "cn_title": row["cn_title"], "cn_summary": row["cn_summary"],
            "category": row["category"], "importance": row["importance"],
            "verdict": verdict, "basis": basis,
        })
    passed = sum(1 for row in judged if row["verdict"] == "通过")
    total = len(judged)
    suite.evidence_json("real-llm/sample-20.json", judged)
    suite.check(
        "8e-翻译摘要抽检 20 条",
        total == 20 and passed >= 18,
        f"抽检 {total} 条，通过 {passed} 条（{passed / total * 100:.0f}%）；"
        f"不通过：{[row['id'] for row in judged if row['verdict'] == '不通过']}",
    )
    suite.check(
        "8f-分类标签合法",
        all(row["category"] in CATEGORIES for row in judged),
        f"分类取值={sorted({row['category'] for row in judged})}",
    )
    suite.check(
        "8g-摘要非空且为中文",
        all(row["cn_summary"] and has_cjk(row["cn_summary"]) for row in judged),
        f"摘要非空且含中文={all(bool(row['cn_summary']) for row in judged)}",
    )
    return {"sample": total, "passed": passed}


def real_call(suite: Suite) -> dict:
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    llm = raw.get("llm") or {}
    key = str(llm.get("api_key") or "").strip()
    if not key:
        env_name = str(llm.get("api_key_env") or "").strip()
        key = os.environ.get(env_name, "").strip()
    if not key:
        suite.check("8i-真实 LLM 链路", False, "真实配置与环境变量均无 API key，跳过")
        return {"skip": "no-key"}

    base = workspace(f"{WS}-call")
    db_path = base / "data" / "aihot.db"
    copy_real_db(db_path)
    candidates = db_query(
        db_path,
        "SELECT id FROM items WHERE enriched_at IS NOT NULL AND category != '过滤'"
        " AND source_id IN ('qbitai','infoq_cn','openai_news','techcrunch_ai')"
        " ORDER BY id LIMIT ?",
        (CALL_ITEMS,),
    )
    ids = [row["id"] for row in candidates]
    conn = sqlite3.connect(db_path)
    with conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM items WHERE id NOT IN ({placeholders})", ids)
    conn.close()
    total = reset_enrichment(db_path)
    suite.note(f"真实调用样本：{total} 条（ids={ids}）")

    pipeline_raw = raw.get("pipeline") or {}
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline_like(pipeline_raw),
        data_dir=base / "data",
        log_dir=base / "logs",
        llm={
            "base_url": str(llm.get("base_url") or ""),
            "model": str(llm.get("model") or "deepseek-chat"),
            "api_key": "",
            "api_key_env": "AIHOT_M2_REAL_KEY",
            "batch_size": int(llm.get("batch_size") or 15),
            "daily_cost_limit_cny": float(llm.get("daily_cost_limit_cny") or 1.0),
        },
    )
    result = run_cli(
        ["enrich", "--config", str(cfg), "--limit", str(CALL_ITEMS), "--top", "6"],
        timeout=600.0,
        env_extra={"AIHOT_M2_REAL_KEY": key},
    )
    stats = parse_stats(result.stdout)
    rows = db_query(
        db_path,
        "SELECT source_id, title, cn_title, cn_summary, category, importance"
        " FROM items ORDER BY id",
    )
    translated = sum(1 for row in rows if row["cn_title"] and row["cn_title"] != row["title"])
    cost = float(stats.get("cost_cny") or 0.0)
    per_item = cost / max(1, total)
    daily_200 = per_item * 200
    daily_400 = per_item * 400
    sample = {
        "items": total,
        "ids": ids,
        "rc": result.rc,
        "prompt_tokens": stats.get("prompt_tokens"),
        "completion_tokens": stats.get("completion_tokens"),
        "cost_cny": cost,
        "cost_per_item_cny": round(per_item, 6),
        "estimated_daily_200_items_cny": round(daily_200, 4),
        "estimated_daily_400_items_cny": round(daily_400, 4),
        "prompt_tokens_per_item": (stats.get("prompt_tokens") or 0) / max(1, total),
        "completion_tokens_per_item": (stats.get("completion_tokens") or 0) / max(1, total),
        "translated_items": translated,
        "written": sum(1 for row in rows if row["cn_title"]),
        "rows": rows,
    }
    # 证据中不含密钥：stdout 已由产品脱敏，且 key 仅经环境变量传递
    suite.evidence("real-llm-cli.txt", result.text)
    suite.evidence_json("real-llm.json", {"sample": sample})
    suite.check(
        "8h-真实链路调用成功",
        result.rc == 0 and cost > 0 and translated >= max(1, total // 2),
        f"rc={result.rc}，{total} 条，成本 ¥{cost:.4f}，改写条目 {translated}/{total}，"
        f"token={stats.get('prompt_tokens')}+{stats.get('completion_tokens')}",
    )
    suite.check(
        "8h-真实链路预算守卫（< ¥0.2）",
        cost < BUDGET,
        f"本次真实调用 ¥{cost:.4f}（预算 ¥{BUDGET}）",
    )
    suite.check(
        "8i-单条成本折算 < ¥1/天",
        cost > 0 and daily_200 < 1.0,
        f"¥{per_item:.6f}/条 × 200 条 = ¥{daily_200:.4f}/天（400 条 = ¥{daily_400:.4f}/天）",
    )
    return sample


def default_pipeline_like(pipeline_raw: dict) -> dict:
    """按真实 config 的 pipeline 段构造（缺失用默认）。"""
    prefilter_raw = pipeline_raw.get("prefilter") or {}
    dedup_raw = pipeline_raw.get("dedup") or {}
    enrich_raw = pipeline_raw.get("enrich") or {}
    rank_raw = pipeline_raw.get("rank") or {}
    return {
        "prefilter": {
            "gate_sources": prefilter_raw.get("gate_sources", ["hackernews"]),
            "blacklist_keywords": prefilter_raw.get("blacklist_keywords", []),
            "extra_ai_keywords": prefilter_raw.get("extra_ai_keywords", []),
        },
        "dedup": {
            "title_similarity": dedup_raw.get("title_similarity", 88),
            "max_hours_apart": dedup_raw.get("max_hours_apart", 72),
            "llm_refine": dedup_raw.get("llm_refine", True),
        },
        "enrich": {
            "max_items": enrich_raw.get("max_items", 200),
            "price_input_per_million": enrich_raw.get("price_input_per_million", 2.0),
            "price_output_per_million": enrich_raw.get("price_output_per_million", 8.0),
        },
        "rank": {"interest_bonus": rank_raw.get("interest_bonus",
                                                {"模型发布": 8, "产品更新": 5, "开源工具": 5})},
    }


def main() -> int:
    suite = Suite("real-llm")
    print("== 真实 DeepSeek 小样本链路 ==")
    sample = real_call(suite)
    print("\n== 翻译摘要抽检 20 条（真实库只读） ==")
    translation = translation_sample(suite)
    suite.evidence_json("real-llm/summary.json", {"sample": sample, "translation": translation})
    suite.check(
        "8z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
