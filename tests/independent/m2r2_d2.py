"""M2 回归（第 2 轮）· D2 复验：簇编号全局单调 + 历史簇不被覆盖。

3a 合成库：第一轮（6 条）之后追加 4 条再跑 → 新簇编号必须 > 历史最大值
3b 真实库副本：历史 64 簇 + 新增条目 → 新簇编号 > 64，旧簇逐行不变
3c 精修路径（llm_refine=true + 假 LLM 合并）：合并后的簇编号是否仍全局单调
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    REAL_DB,
    FakeLLM,
    Suite,
    copy_real_db,
    db_one,
    db_query,
    default_pipeline,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "r2-d2"

UNRELATED = [
    ("qbitai", "OpenAI ships GPT-6 Sol", "https://example.com/r2d2/1"),
    ("openai_news", "Anthropic releases Claude Opus 6", "https://example.com/r2d2/2"),
    ("techcrunch_ai", "Meta open-sources Llama-9", "https://example.com/r2d2/3"),
    ("qbitai", "DeepSeek publishes V5 report", "https://example.com/r2d2/4"),
    ("hackernews", "Google announces Gemini 8 Ultra", "https://example.com/r2d2/5"),
    ("openai_news", "Mistral launches Le Chat Enterprise", "https://example.com/r2d2/6"),
]
MORE = [
    ("techcrunch_ai", "Cohere releases Command R10", "https://example.com/r2d2/7"),
    ("qbitai", "Perplexity ships Comet 2 browser", "https://example.com/r2d2/8"),
    ("openai_news", "Nvidia unveils Rubin GPU", "https://example.com/r2d2/9"),
    ("hackernews", "Amazon launches Nova 3 models", "https://example.com/r2d2/10"),
]
RELATED = [
    ("qbitai", "GitHub 新推出 Copilot 代码评审功能（公开预览）",
     "https://example.com/r2d2/rel-1"),
    ("openai_news", "GitHub 上线 Copilot 代码评审功能，进入公开预览阶段",
     "https://example.com/r2d2/rel-2"),
]


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def seed(path: Path, rows: list[tuple[str, str, str]]) -> None:
    seed_items(path, [
        {
            "source_id": source, "title": title, "url": url,
            "published_at": ts(1 + index), "raw_summary": f"summary {index}", "lang": "zh",
        }
        for index, (source, title, url) in enumerate(rows)
    ])


def items_reply(count: int) -> str:
    return json.dumps({"items": [
        {"index": index, "cn_title": f"中文标题 {index}", "cn_summary": "摘要",
         "category": "行业动态", "importance": 50, "is_ai": True}
        for index in range(1, count + 1)
    ]})


def integrity(db_path: Path) -> dict:
    """簇完整性：编号集合、悬空引用、mention 与实际条目数不一致。"""
    clusters = db_query(
        db_path, "SELECT cluster_id, mention_count FROM clusters ORDER BY cluster_id"
    )
    ids = [row["cluster_id"] for row in clusters]
    dangling = db_query(
        db_path,
        "SELECT DISTINCT i.cluster_id AS cid FROM items i LEFT JOIN clusters c"
        " ON c.cluster_id = i.cluster_id WHERE i.cluster_id IS NOT NULL AND c.cluster_id IS NULL",
    )
    actual = {
        row["cluster_id"]: row["n"]
        for row in db_query(
            db_path,
            "SELECT cluster_id, COUNT(*) AS n FROM items WHERE cluster_id IS NOT NULL"
            " GROUP BY cluster_id",
        )
    }
    mismatched = [
        {"cluster_id": cid, "mention_count": mention, "items": actual.get(cid, 0)}
        for cid, mention in ((row["cluster_id"], row["mention_count"]) for row in clusters)
        if mention != actual.get(cid, 0)
    ]
    return {
        "cluster_ids": ids,
        "max_id": max(ids, default=0),
        "count": len(ids),
        "dangling": [row["cid"] for row in dangling],
        "mismatched": mismatched,
    }


def cluster_rows(db_path: Path) -> list[dict]:
    return db_query(
        db_path,
        "SELECT cluster_id, canonical_title, item_hashes, source_ids, mention_count, first_seen"
        " FROM clusters ORDER BY cluster_id",
    )


def item_assignments(db_path: Path) -> list[dict]:
    return db_query(
        db_path, "SELECT url, cluster_id FROM items WHERE cluster_id IS NOT NULL ORDER BY url"
    )


def case_3a(suite: Suite) -> None:
    """合成库两轮：新簇编号 > 历史最大值，旧簇不变。"""
    base = workspace(f"{WS}-3a")
    db_path = base / "data" / "aihot.db"
    seed(db_path, UNRELATED)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-d2"},
    )
    run1 = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    before_rows = cluster_rows(db_path)
    before_assign = item_assignments(db_path)
    first = integrity(db_path)

    seed(db_path, MORE)
    run2 = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    after_rows = cluster_rows(db_path)
    after_assign = item_assignments(db_path)
    second = integrity(db_path)
    new_ids = [cid for cid in second["cluster_ids"] if cid > first["max_id"]]

    suite.evidence(
        "3a-synthetic-two-rounds.txt",
        run1.text + "\n\n" + run2.text + "\n\n"
        + json.dumps({"first": first, "second": second}, ensure_ascii=False, indent=2),
    )
    suite.check(
        "3a-第二轮新簇编号 > 历史最大值",
        len(new_ids) == len(MORE) and second["max_id"] > first["max_id"],
        f"第一轮 max_id={first['max_id']}（{first['count']} 簇）；"
        f"第二轮 max_id={second['max_id']}，新增簇编号={new_ids}（期望 {len(MORE)} 个）",
    )
    suite.check(
        "3a-无悬空 cluster_id",
        not second["dangling"],
        f"悬空={second['dangling'] or '无'}",
    )
    suite.check(
        "3a-mention_count 与实际条目数一致",
        not second["mismatched"],
        f"不一致={second['mismatched'] or '无'}",
    )
    old_after = [row for row in after_rows if row["cluster_id"] <= first["max_id"]]
    suite.check(
        "3a-历史簇未被覆盖",
        json.dumps(before_rows, ensure_ascii=False) == json.dumps(old_after, ensure_ascii=False),
        f"第一轮 {len(before_rows)} 簇 → 第二轮后其中 {len(old_after)} 簇逐行一致",
    )
    suite.check(
        "3a-历史条目的 cluster_id 未变",
        [row for row in after_assign if row["url"] in {b["url"] for b in before_assign}]
        == before_assign,
        f"前 {len(before_assign)} 条分配保持一致",
    )


def case_3b(suite: Suite) -> None:
    """真实库副本：历史 64 簇 + 新增条目 → 新簇编号 > 64，旧簇不变。"""
    base = workspace(f"{WS}-3b")
    db_path = copy_real_db(base / "data" / "aihot.db")
    before_rows = cluster_rows(db_path)
    before = integrity(db_path)
    seed(db_path, UNRELATED)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-d2"},
    )
    run = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    after_rows = cluster_rows(db_path)
    after = integrity(db_path)
    new_ids = [cid for cid in after["cluster_ids"] if cid > before["max_id"]]
    old_after = [row for row in after_rows if row["cluster_id"] <= before["max_id"]]

    suite.evidence(
        "3b-real-copy.txt",
        run.text + "\n\n" + json.dumps(
            {"before": {"count": before["count"], "max_id": before["max_id"]},
             "after": {"count": after["count"], "max_id": after["max_id"], "new_ids": new_ids},
             "mismatched": after["mismatched"], "dangling": after["dangling"]},
            ensure_ascii=False, indent=2),
    )
    suite.check(
        "3b-真实库副本新簇编号 > 历史最大",
        len(new_ids) == len(UNRELATED) and min(new_ids, default=0) > before["max_id"],
        f"历史 max_id={before['max_id']}，新增 {len(new_ids)} 簇：{new_ids}"
        f"（期望均 > {before['max_id']}）",
    )
    suite.check(
        "3b-无悬空 cluster_id",
        not after["dangling"],
        f"悬空={after['dangling'] or '无'}",
    )
    suite.check(
        "3b-新增簇 mention_count 与实际一致",
        not after["mismatched"],
        f"不一致={after['mismatched'] or '无'}",
    )
    suite.check(
        "3b-历史 64 簇未被覆盖",
        json.dumps(before_rows, ensure_ascii=False) == json.dumps(old_after, ensure_ascii=False),
        f"历史 {len(before_rows)} 簇 → 处理后其中 {len(old_after)} 簇逐行一致",
    )


def case_3c(suite: Suite) -> None:
    """精修路径（llm_refine=true）：合并后的簇编号是否仍全局单调。

    注意：第 3 轮起 `_remap_cluster_ids` 在精修**之后**执行，因此精修提示词中的编号是
    规则层的 1..n（而非全局编号）；假 LLM 需按 listing 编号（[1,2]）返回。
    """
    base = workspace(f"{WS}-3c")
    db_path = base / "data" / "aihot.db"
    seed(db_path, UNRELATED)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(refine=False),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-d2"},
    )
    run1 = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "20", "--top", "1"])
    first = integrity(db_path)
    before_rows = cluster_rows(db_path)

    new_items = RELATED + MORE[:2]
    seed(db_path, new_items)
    with FakeLLM() as fake:
        cfg2 = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=True),
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-d2", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(json.dumps({"groups": [
            {"indexes": [1, 2], "event": "GitHub 上线 Copilot 代码评审", "confidence": "high"}
        ]}))
        fake.enqueue_content(items_reply(3))
        run2 = run_cli(["enrich", "--config", str(cfg2), "--limit", "20", "--top", "1"])
        prompt_used = (
            json.dumps(fake.requests[0]["body"], ensure_ascii=False) if fake.requests else ""
        )
    after_rows = cluster_rows(db_path)
    after = integrity(db_path)
    new_urls = {item[2] for item in new_items}
    assigns = db_query(
        db_path,
        "SELECT url, cluster_id FROM items WHERE cluster_id IS NOT NULL ORDER BY url",
    )
    new_assign = {row["url"]: row["cluster_id"] for row in assigns if row["url"] in new_urls}
    new_ids = sorted(set(new_assign.values()))
    old_after = [row for row in after_rows if row["cluster_id"] <= first["max_id"]]
    changed_rows = [
        row["cluster_id"]
        for row, before in zip(old_after, before_rows, strict=False)
        if json.dumps(row, ensure_ascii=False) != json.dumps(before, ensure_ascii=False)
    ]

    suite.evidence(
        "3c-refine-path.txt",
        run1.text + "\n\n" + run2.text + "\n\n"
        + json.dumps(
            {"first": first, "after": after, "new_assign": new_assign,
             "listing_used_global_ids": "7. [" in prompt_used},
            ensure_ascii=False, indent=2),
    )
    suite.check(
        "3c-精修确实发生（listing 为规则层编号）",
        "1. [" in prompt_used and "2. [" in prompt_used
        and new_assign.get(RELATED[0][2]) == new_assign.get(RELATED[1][2]),
        f"提示词含「1. [」={'1. [' in prompt_used}；相关对同簇="
        f"{new_assign.get(RELATED[0][2]) == new_assign.get(RELATED[1][2])}；"
        f"新条目分配={new_assign}",
    )
    suite.check(
        "3c-精修路径新簇编号 > 历史最大（要求）",
        len(new_ids) == 3 and min(new_ids, default=0) > first["max_id"],
        f"第一轮 max_id={first['max_id']}；本轮新条目簇编号={new_ids}"
        f"（期望 3 个且均 > {first['max_id']}）",
    )
    suite.check(
        "3c-精修路径无悬空/无不一致",
        not after["dangling"] and not after["mismatched"],
        f"悬空={after['dangling'] or '无'}；mention 不一致={after['mismatched'] or '无'}",
    )
    suite.check(
        "3c-精修路径历史簇未被覆盖",
        json.dumps(before_rows, ensure_ascii=False) == json.dumps(old_after, ensure_ascii=False),
        f"历史 {len(before_rows)} 簇 → 处理后其行内容发生变化的簇={changed_rows or '无'}"
        f"（对比 {len(old_after)}/{len(before_rows)} 行）",
    )


def main() -> int:
    suite = Suite("d2")
    print("== 3a 合成库两轮（--no-llm） ==")
    case_3a(suite)
    print("\n== 3b 真实库副本（--no-llm） ==")
    case_3b(suite)
    print("\n== 3c 精修路径（llm_refine=true） ==")
    case_3c(suite)

    # 真实库当前状态（开发者声明的「重算派生字段」）
    real = integrity(REAL_DB)
    real_clusters = db_one(REAL_DB, "SELECT COUNT(*) FROM clusters")
    suite.evidence_json("3z-real-db-integrity.json", {**real, "clusters_total": real_clusters})
    suite.check(
        "3z-真实库无悬空引用",
        not real["dangling"],
        f"悬空引用={real['dangling'] or '无'}（{real_clusters} 簇）",
    )
    suite.check(
        "3z-真实库 mention_count 与条目数一致",
        not real["mismatched"],
        f"不一致簇={real['mismatched'] or '无'}",
    )
    suite.check(
        "3z-真实库只读守护（items 未变）",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
