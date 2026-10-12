"""M2 回归（第 2 轮）· D1 复验：LLM 精修过度合并的三层防护。

用假 LLM 注入：
1a 默认配置（不写 llm_refine）→ 精修根本不调用（零风险路径）
1b confidence=high 但把 N 条互不相关说成一组 → 规则拆散 + refine_rejected>0 + 不合并
1c confidence=medium → 不合并（也不计拒绝）
1d 组内确实相关（相似度 ≥55）→ 正常合并
1e 模块级：单组 >4 拆散、混合组拆成连通分量、单元素组放行
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m2_support import (  # noqa: E402
    FakeLLM,
    Suite,
    db_query,
    default_pipeline,
    real_db_snapshot,
    run_cli,
    seed_items,
    workspace,
    write_config,
)

WS = "r2-d1"

UNRELATED = [
    ("qbitai", "OpenAI ships GPT-6 Sol", "https://example.com/r2d1/1"),
    ("openai_news", "Anthropic releases Claude Opus 6", "https://example.com/r2d1/2"),
    ("techcrunch_ai", "Meta open-sources Llama-9", "https://example.com/r2d1/3"),
    ("qbitai", "DeepSeek publishes V5 report", "https://example.com/r2d1/4"),
    ("hackernews", "Google announces Gemini 8 Ultra", "https://example.com/r2d1/5"),
    ("openai_news", "Mistral launches Le Chat Enterprise", "https://example.com/r2d1/6"),
    ("techcrunch_ai", "Cohere releases Command R10", "https://example.com/r2d1/7"),
    ("qbitai", "Perplexity ships Comet 2 browser", "https://example.com/r2d1/8"),
    ("openai_news", "Nvidia unveils Rubin GPU", "https://example.com/r2d1/9"),
    ("hackernews", "Amazon launches Nova 3 models", "https://example.com/r2d1/10"),
]

RELATED = [
    ("qbitai", "GitHub 新推出 Copilot 代码评审功能（公开预览）",
     "https://example.com/r2d1/rel-1"),
    ("openai_news", "GitHub 上线 Copilot 代码评审功能，进入公开预览阶段",
     "https://example.com/r2d1/rel-2"),
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


def groups_reply(indexes: list[int], confidence: str = "high") -> str:
    return json.dumps({"groups": [
        {"indexes": indexes, "event": "OpenAI 发布 GPT-6 Sol", "confidence": confidence}
    ]})


def enrich_runs(db_path: Path) -> list[dict]:
    rows = db_query(db_path, "SELECT run_id, status, stage_stats FROM runs ORDER BY started_at")
    out = []
    for row in rows:
        stats = json.loads(row["stage_stats"] or "{}")
        if stats.get("stage") == "enrich":
            out.append({"run_id": row["run_id"], "status": row["status"], **stats})
    return out


def cluster_summary(db_path: Path) -> list[dict]:
    return db_query(
        db_path,
        "SELECT c.cluster_id AS cluster_id, c.mention_count AS mention_count,"
        " (SELECT COUNT(*) FROM items i WHERE i.cluster_id = c.cluster_id) AS items"
        " FROM clusters c ORDER BY c.cluster_id",
    )


def case_1a(suite: Suite) -> None:
    """默认配置（不写 llm_refine）→ 精修零调用。"""
    base = workspace(f"{WS}-1a")
    db_path = base / "data" / "aihot.db"
    seed(db_path, UNRELATED)
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=None),  # 不写 dedup.llm_refine
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-r2", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(items_reply(10))
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "1"])
        calls = fake.request_count
    runs = enrich_runs(db_path)
    clusters = cluster_summary(db_path)
    suite.evidence("1a-default-config.txt", result.text + "\n" + json.dumps(
        {"llm_requests": calls, "runs": runs, "clusters": clusters}, ensure_ascii=False, indent=2))
    suite.check(
        "1a-默认配置不调用 LLM 精修",
        result.rc == 0 and calls == 1 and "LLM 精修后" not in result.stdout,
        f"rc={result.rc}，LLM 请求={calls}（仅 1 次条目加工），输出含「LLM 精修后」="
        f"{'LLM 精修后' in result.stdout}",
    )
    stats = runs[0] if runs else {}
    suite.check(
        "1a-runs 记录 refine_calls=0（默认关闭）",
        bool(runs) and stats.get("refine_calls") == 0 and stats.get("refine_rejected") == 0,
        f"refine_calls={stats.get('refine_calls')} refine_rejected={stats.get('refine_rejected')}",
    )
    suite.check(
        "1a-零合并（10 条 → 10 簇）",
        len(clusters) == 10 and all(row["items"] == 1 for row in clusters),
        f"簇数={len(clusters)}，逐簇条目数={[row['items'] for row in clusters]}",
    )


def case_1b(suite: Suite) -> None:
    """high 置信度把 10 条互不相关说成一组 → 规则拆散。"""
    base = workspace(f"{WS}-1b")
    db_path = base / "data" / "aihot.db"
    seed(db_path, UNRELATED)
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=True),
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-r2", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(groups_reply(list(range(1, 11)), "high"))
        fake.enqueue_content(items_reply(10))
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "1"])
    runs = enrich_runs(db_path)
    clusters = cluster_summary(db_path)
    suite.evidence("1b-high-but-unrelated.txt", result.text + "\n" + json.dumps(
        {"runs": runs, "clusters": clusters}, ensure_ascii=False, indent=2))
    stats = runs[0] if runs else {}
    suite.check(
        "1b-规则拆散（10 条 → 10 簇，零合并）",
        len(clusters) == 10 and all(row["items"] == 1 for row in clusters),
        f"簇数={len(clusters)}，逐簇条目数={[row['items'] for row in clusters]}",
    )
    suite.check(
        "1b-refine_rejected>0 且计入 runs",
        stats.get("refine_rejected", 0) >= 1 and stats.get("refine_calls") == 1,
        f"refine_calls={stats.get('refine_calls')} refine_rejected={stats.get('refine_rejected')}",
    )
    suite.check(
        "1b-日志/输出可见拒绝原因",
        "聚类精修被规则拒绝" in result.combined or "聚类精修被规则拒绝" in _log_text(base),
        f"stdout/log 含「聚类精修被规则拒绝」="
        f"{('聚类精修被规则拒绝' in result.combined) or ('聚类精修被规则拒绝' in _log_text(base))}",
    )


def case_1c(suite: Suite) -> None:
    """medium 置信度 → 不合并，且不计拒绝。"""
    base = workspace(f"{WS}-1c")
    db_path = base / "data" / "aihot.db"
    seed(db_path, UNRELATED)
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=True),
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-r2", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(groups_reply(list(range(1, 11)), "medium"))
        fake.enqueue_content(items_reply(10))
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "1"])
    runs = enrich_runs(db_path)
    clusters = cluster_summary(db_path)
    stats = runs[0] if runs else {}
    suite.evidence("1c-medium-confidence.txt", result.text + "\n" + json.dumps(
        {"runs": runs, "clusters": clusters}, ensure_ascii=False, indent=2))
    suite.check(
        "1c-medium 不合并且 refine_rejected=0",
        len(clusters) == 10
        and all(row["items"] == 1 for row in clusters)
        and stats.get("refine_rejected") == 0
        and stats.get("refine_calls") == 1,
        f"簇数={len(clusters)}，refine_calls={stats.get('refine_calls')}，"
        f"refine_rejected={stats.get('refine_rejected')}",
    )


def case_1d(suite: Suite) -> None:
    """组内确实相关（相似度 ≥55 且 <88）→ 精修正常合并。"""
    base = workspace(f"{WS}-1d")
    db_path = base / "data" / "aihot.db"
    seed(db_path, RELATED)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(refine=True),
        data_dir=base / "data", log_dir=base / "logs",
        llm={"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-fake-r2", "batch_size": 15,
             "daily_cost_limit_cny": 100.0},
    )
    # 步骤①：规则层不合并（证明该对确实需要精修）
    rule_only = run_cli(["enrich", "--no-llm", "--dry-run", "--config", str(cfg), "--top", "2"])
    rule_clusters = re.search(r"聚类: (\d+) 条 → (\d+) 簇", rule_only.stdout)
    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=True),
            data_dir=base / "data", log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-r2", "batch_size": 15,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(groups_reply([1, 2], "high"))
        fake.enqueue_content(items_reply(1))
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "20", "--top", "2"])
    runs = enrich_runs(db_path)
    clusters = cluster_summary(db_path)
    stats = runs[0] if runs else {}
    suite.evidence(
        "1d-related-merge.txt",
        rule_only.text + "\n\n" + result.text + "\n"
        + json.dumps({"runs": runs, "clusters": clusters}, ensure_ascii=False, indent=2),
    )
    suite.check(
        "1d-规则层本不合并（相似度在 55~88 之间）",
        bool(rule_clusters) and rule_clusters.group(1) == "2" and rule_clusters.group(2) == "2",
        f"规则层聚类={rule_clusters.group(0) if rule_clusters else '未解析'}（期望 2 条 → 2 簇）",
    )
    suite.check(
        "1d-精修正常合并为 1 簇（mention=2）",
        len(clusters) == 1 and clusters[0]["mention_count"] == 2 and clusters[0]["items"] == 2,
        f"簇数={len(clusters)}，clusters={clusters}",
    )
    suite.check(
        "1d-本次无规则拒绝",
        stats.get("refine_rejected") == 0 and stats.get("refine_calls") == 1,
        f"refine_calls={stats.get('refine_calls')} refine_rejected={stats.get('refine_rejected')}",
    )


def case_1e(suite: Suite) -> None:
    """模块级：单组 >4 拆散、混合组拆成分量、单元素放行、相似链判定。"""
    from aihot.models import Cluster
    from aihot.pipeline.dedup import title_similarity, validate_merged_groups

    now = datetime.now(UTC)
    titles = {
        1: "OpenAI 发布 GPT-6 Sol 模型",
        2: "OpenAI 发布 GPT-6 Sol 模型（更新）",
        3: "Netflix 重构 Conductor 支持每月 4.2 亿次工作流",
        4: "OpenAI 发布 GPT-6 Sol 模型",
        5: "OpenAI 发布 GPT-6 Sol 模型",
        6: "OpenAI 发布 GPT-6 Sol 模型",
    }
    clusters = [
        Cluster(cluster_id=cid, canonical_title=title, item_hashes=[], source_ids=["src"],
                mention_count=1, heat_score=0.0, first_seen=now)
        for cid, title in titles.items()
    ]
    accepted, rejections = validate_merged_groups([[1, 2, 3]], clusters)
    suite.check(
        "1e-混合组拆成连通分量",
        sorted(map(sorted, accepted)) == [[1, 2], [3]] and len(rejections) == 1,
        f"accepted={accepted} rejections={rejections}",
    )
    accepted_big, rejections_big = validate_merged_groups([[1, 4, 5, 6, 2]], clusters)
    suite.check(
        "1e-单组超过 4 条直接拆散",
        sorted(map(sorted, accepted_big)) == [[1], [2], [4], [5], [6]] and len(rejections_big) == 1,
        f"accepted={accepted_big}（{len(accepted_big)} 组）rejections={rejections_big}",
    )
    accepted_ok, rejections_ok = validate_merged_groups([[1, 2], [5]], clusters)
    suite.check(
        "1e-相关组放行（相似度 ≥55）",
        sorted(map(sorted, accepted_ok)) == [[1, 2], [5]] and not rejections_ok,
        f"accepted={accepted_ok} rejections={rejections_ok}；"
        f"相似度(1,2)={title_similarity(titles[1], titles[2]):.1f}",
    )
    unrelated_score = title_similarity(titles[1], titles[3])
    accepted_low, rejections_low = validate_merged_groups([[1, 3]], clusters)
    suite.check(
        "1e-不相关组被拒（相似度 <55）",
        sorted(map(sorted, accepted_low)) == [[1], [3]] and len(rejections_low) == 1,
        f"相似度(1,3)={unrelated_score:.1f}（<55），accepted={accepted_low}",
    )
    suite.evidence_json("1e-module-checks.json", {
        "accepted_mixed": accepted, "rejections_mixed": rejections,
        "accepted_big": accepted_big, "rejections_big": rejections_big,
        "accepted_ok": accepted_ok, "accepted_low": accepted_low, "rejections_low": rejections_low,
        "sim_1_2": round(title_similarity(titles[1], titles[2]), 1),
        "sim_1_3": round(unrelated_score, 1),
    })


def _log_text(base: Path) -> str:
    logs = list((base / "logs").glob("*.log"))
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in logs)


def main() -> int:
    suite = Suite("d1")
    print("== 1a 默认配置（llm_refine 缺省） ==")
    case_1a(suite)
    print("\n== 1b high 置信度但互不相关 ==")
    case_1b(suite)
    print("\n== 1c medium 置信度 ==")
    case_1c(suite)
    print("\n== 1d 真正相关的组 ==")
    case_1d(suite)
    print("\n== 1e 模块级校验 ==")
    case_1e(suite)
    suite.check(
        "1z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
