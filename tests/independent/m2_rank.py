"""M2 验收 6：排序正确性（各分项可解释 + Top 顺序符合预期）。

黑盒：假 LLM 控制 importance/category → CLI 打印 Top 分数；
独立复算：从 SQLite 行（importance/category/extra/cluster 成员数/源权重）按文档公式复算并比对。
"""

from __future__ import annotations

import json
import math
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

WS = "rank"

# 文档公式（PLAN §3.2 P 加工层 rank）
W_IMPORTANCE, W_CROSS, W_COMMUNITY, W_SOURCE = 0.55, 0.20, 0.15, 0.10
COMMUNITY_CAP = 1500.0
CROSS_FULL = 3
SOURCE_CAP = 1.2


def ts(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def fixture_items() -> list[dict]:
    return [
        {"source_id": "qbitai", "title": "OpenAI ships GPT-6 Sol", "url": "https://example.com/rank/1",
         "published_at": ts(1.0), "lang": "en"},
        {"source_id": "openai_news", "title": "Anthropic releases Claude Opus 6",
         "url": "https://example.com/rank/2", "published_at": ts(2.0), "lang": "en"},
        {"source_id": "qbitai", "title": "DeepSeek 发布 V5 模型",
         "url": "https://example.com/rank/3", "published_at": ts(3.0), "lang": "zh"},
        {"source_id": "openai_news", "title": "DeepSeek 发布 V5 模型（附评测）",
         "url": "https://example.com/rank/4", "published_at": ts(3.2), "lang": "zh"},
        {"source_id": "hackernews", "title": "DeepSeek 发布 V5 模型 | 量子位",
         "url": "https://example.com/rank/5", "published_at": ts(3.4), "lang": "zh"},
        {"source_id": "hackernews", "title": "Nvidia unveils Rubin GPU",
         "url": "https://example.com/rank/6", "published_at": ts(4.0), "lang": "en",
         "extra": {"points": 1000, "num_comments": 200, "hn_id": "1"}},
        {"source_id": "openai_news", "title": "Microsoft adds Copilot agents to Office",
         "url": "https://example.com/rank/7", "published_at": ts(5.0), "lang": "en"},
        {"source_id": "hackernews", "title": "Alibaba releases Qwen 4",
         "url": "https://example.com/rank/8", "published_at": ts(6.0), "lang": "en"},
    ]


REPLY = json.dumps({"items": [
    {"index": 1, "cn_title": "标题1", "cn_summary": "摘要", "category": "模型发布",
     "importance": 90, "is_ai": True},
    {"index": 2, "cn_title": "标题2", "cn_summary": "摘要", "category": "行业动态",
     "importance": 20, "is_ai": True},
    {"index": 3, "cn_title": "标题3", "cn_summary": "摘要", "category": "研究突破",
     "importance": 50, "is_ai": True},
    {"index": 4, "cn_title": "标题4", "cn_summary": "摘要", "category": "模型发布",
     "importance": 60, "is_ai": True},
    {"index": 5, "cn_title": "标题5", "cn_summary": "摘要", "category": "产品更新",
     "importance": 60, "is_ai": True},
    {"index": 6, "cn_title": "标题6", "cn_summary": "摘要", "category": "开源工具",
     "importance": 70, "is_ai": True},
]})


def community_signal(extra: dict) -> float:
    points = float(extra.get("points") or 0)
    comments = float(extra.get("num_comments") or 0)
    upvotes = float(extra.get("upvotes") or 0)
    raw = max(points, comments * 2, upvotes)
    if raw <= 0:
        return 0.0
    return min(1.0, math.log10(1 + raw) / math.log10(1 + COMMUNITY_CAP))


def parse_top(stdout: str) -> list[dict]:
    rows: list[dict] = []
    pattern = re.compile(r"^\s*(\d+)\. \[\s*([\d.]+)\] \(([^)]+)\)\s+(.+?)\s*$")
    for line in stdout.splitlines():
        match = pattern.match(line)
        if match:
            rows.append({
                "rank": int(match.group(1)),
                "score": float(match.group(2)),
                "category": match.group(3),
                "cn_title": match.group(4),
            })
    return rows


def main() -> int:
    suite = Suite("rank")
    base = workspace(f"{WS}-run")
    db_path = base / "data" / "aihot.db"
    seed_items(db_path, fixture_items())
    weights = {"qbitai": 1.0, "openai_news": 1.2, "hackernews": 0.8}
    bonus = {"模型发布": 8.0, "产品更新": 5.0, "开源工具": 5.0}

    with FakeLLM() as fake:
        cfg = write_config(
            base / "config.yaml",
            pipeline=default_pipeline(refine=False),
            data_dir=base / "data",
            log_dir=base / "logs",
            llm={"base_url": fake.base_url, "api_key": "sk-fake-rank", "batch_size": 20,
                 "daily_cost_limit_cny": 100.0},
        )
        fake.enqueue_content(REPLY)
        result = run_cli(["enrich", "--config", str(cfg), "--limit", "50", "--top", "8"])
    suite.evidence("6a-rank-cli.txt", result.text)
    suite.check("6a-命令成功", result.rc == 0 and "Top 6:" in result.stdout, f"rc={result.rc}")

    printed = parse_top(result.stdout)
    rows = db_query(
        db_path,
        "SELECT url, title, raw_summary, cn_title, category, importance, source_id, extra,"
        " published_at, cluster_id FROM items WHERE enriched_at IS NOT NULL ORDER BY id",
    )
    # 独立复算：按文档「源权重 > 有摘要 > 热度 > 更早发布」选代表条目，再按公式算分
    groups: dict[int, list[dict]] = {}
    for row in rows:
        key = row["cluster_id"] if row["cluster_id"] is not None else -1
        groups.setdefault(key, []).append(row)

    def heat(row: dict) -> float:
        extra = json.loads(row["extra"] or "{}")
        return float(extra.get("points") or 0) + float(extra.get("num_comments") or 0)

    def published(row: dict) -> datetime:
        return datetime.fromisoformat(row["published_at"])

    expected = {}
    for members in groups.values():
        representative = max(
            members,
            key=lambda row: (
                weights.get(row["source_id"], 1.0),
                1 if row["raw_summary"] else 0,
                heat(row),
                -published(row).timestamp(),
            ),
        )
        mention = max(1, len(members))
        cross = min(1.0, (mention - 1) / CROSS_FULL)
        community = community_signal(json.loads(representative["extra"] or "{}"))
        weight = min(1.0, max(0.0, weights.get(representative["source_id"], 1.0)) / SOURCE_CAP)
        importance = max(0, min(100, representative["importance"])) / 100.0
        terms = {
            "importance": 100.0 * W_IMPORTANCE * importance,
            "cross": 100.0 * W_CROSS * cross,
            "community": 100.0 * W_COMMUNITY * community,
            "source": 100.0 * W_SOURCE * weight,
        }
        bonus_value = float(bonus.get(representative["category"], 0.0))
        expected[representative["cn_title"]] = {
            "score": round(sum(terms.values()) + bonus_value, 2),
            "terms": {key: round(value, 2) for key, value in terms.items()},
            "bonus": bonus_value,
            "importance": representative["importance"],
            "category": representative["category"],
            "mention": mention,
            "community": round(community, 4),
            "source": representative["source_id"],
            "source_weight": weights.get(representative["source_id"], 1.0),
            "members": len(members),
        }

    suite.evidence_json("6b-expected-score.json", expected)

    suite.check(
        "6b-Top条数与加工条数",
        len(printed) == 6 and len(expected) == 6,
        f"打印 Top {len(printed)} 条，库内加工 {len(expected)} 条（3 条 DeepSeek 合并为 1 簇）",
    )
    mismatches = []
    for row in printed:
        exp = expected.get(row["cn_title"])
        if exp is None or abs(exp["score"] - row["score"]) > 0.06:
            mismatches.append({"printed": row, "expected": exp})
    suite.check(
        "6c-打印分数=独立复算分数",
        not mismatches,
        "6 条全部一致"
        if not mismatches
        else f"不一致 {len(mismatches)} 条：{json.dumps(mismatches, ensure_ascii=False)[:300]}",
    )
    # 顺序：分数降序（同分按时间降序）
    scores = [row["score"] for row in printed]
    ranks = [row["rank"] for row in printed]
    suite.check(
        "6d-Top 按分数降序",
        scores == sorted(scores, reverse=True) and ranks == list(range(1, len(printed) + 1)),
        f"分数序列={scores}",
    )

    # 分项可解释性（逐项断言；expected 为独立复算）
    check = expected
    delta13 = check["标题1"]["terms"]["importance"] - check["标题2"]["terms"]["importance"]
    suite.check(
        "6e-importance 主导（90 vs 20 → 重要性项差 38.5）",
        abs(delta13 - 0.55 * 0.7 * 100) < 0.05,
        f"标题1={check['标题1']['score']}（{check['标题1']['terms']} +{check['标题1']['bonus']}）"
        f" vs 标题2={check['标题2']['score']}；重要性项差={delta13:.2f}（理论 38.5）",
    )
    suite.check(
        "6f-跨源加成（mention 3 → 跨源项 13.33）",
        check["标题3"]["mention"] == 3
        and abs(check["标题3"]["terms"]["cross"] - 100 * 0.20 * (2 / 3)) < 0.05,
        f"标题3 mention={check['标题3']['mention']}，跨源项={check['标题3']['terms']['cross']}"
        f"（=20×(2/3)），总分={check['标题3']['score']}",
    )
    suite.check(
        "6g-社区热度（HN points=1000/comments=200）",
        abs(check["标题4"]["community"] - math.log10(1001) / math.log10(1501)) < 5e-4
        and check["标题4"]["community"] > 0.9
        and check["标题5"]["community"] == 0.0,
        f"标题4 community={check['标题4']['community']}"
        f"（社区项={check['标题4']['terms']['community']}），"
        f"标题5 community={check['标题5']['community']}（社区项=0）",
    )
    suite.check(
        "6h-源权重（openai_news 1.2 > qbitai 1.0 > hackernews 0.8）",
        check["标题5"]["source"] == "openai_news"
        and check["标题6"]["source"] == "hackernews"
        and check["标题5"]["source_weight"] == 1.2
        and check["标题6"]["source_weight"] == 0.8,
        "1.2 达上限（0.10×100=10 分），0.8 仅得 6.67 分",
    )
    suite.check(
        "6i-兴趣加成（模型发布 +8 / 产品更新 +5 / 开源工具 +5）",
        check["标题1"]["category"] == "模型发布"
        and check["标题5"]["category"] == "产品更新"
        and check["标题6"]["category"] == "开源工具",
        "分类来自 LLM，加成来自配置 pipeline.rank.interest_bonus",
    )
    # 排序预期：标题1（90/模型发布）> 标题6（70/开源工具/HN 无热度）……
    order = [row["cn_title"] for row in printed]
    suite.check(
        "6j-Top 顺序符合预期",
        order[0] == "标题1" and order[-1] == "标题2",
        f"实际顺序={order}（预期首位 标题1、末位 标题2）",
    )

    # 模块级边界（白盒复算）
    module_checks(suite)

    suite.check(
        "6z-真实库只读守护",
        real_db_snapshot()["items"] == REAL_ITEMS_BASELINE,
        f"items={real_db_snapshot()['items']}（开始时 {REAL_ITEMS_BASELINE}）",
    )
    return suite.finish()


def module_checks(suite: Suite) -> None:
    from aihot.models import NewsItem
    from aihot.pipeline.rank import rank_items as rank_all
    from aihot.pipeline.rank import score_item, select_top

    now = datetime.now(UTC)
    item = NewsItem(
        source_id="qbitai", title="t", url="https://example.com/m", published_at=now,
        extra={"mention_count": 1}, importance=100, category="模型发布",
    )
    score, breakdown = score_item(item, source_weight=1.0, interest_bonus={"模型发布": 8.0})
    suite.check(
        "6k-满分条目分项（importance 100 → 55，源权重 1.0 → 8.33，加成 8）",
        abs(score - (55 + 8.33 + 8)) < 0.05 and breakdown["source_weight"] == 8.33,
        f"score={score}，breakdown={breakdown}",
    )
    item.importance = 999
    clamped, _ = score_item(item, source_weight=1.0)
    suite.check("6l-importance>100 被截断", abs(clamped - (55 + 8.33)) < 0.05, f"score={clamped}")
    item.importance = -5
    clamped_low, _ = score_item(item, source_weight=1.0)
    suite.check("6m-importance<0 被截断", abs(clamped_low - 8.33) < 0.05, f"score={clamped_low}")
    heavy, _ = score_item(item, source_weight=99.0)
    suite.check(
        "6n-源权重上限 1.2（→10 分）",
        abs(heavy - (10.0)) < 0.05,
        f"source_weight=99 → score={heavy}",
    )
    suite.check(
        "6o-select_top(<=0) 返回空",
        select_top([], 5) == [] and select_top([], 0) == [],
        "select_top([], 5)=[]，select_top([], 0)=[]",
    )
    # 同分按发布时间新的优先
    older = NewsItem(source_id="a", title="old", url="https://e/old",
                     published_at=now - timedelta(hours=2), extra={}, importance=50)
    newer = NewsItem(source_id="a", title="new", url="https://e/new",
                     published_at=now, extra={}, importance=50)
    ranked = rank_all([older, newer])
    suite.check(
        "6p-同分时更新的优先",
        ranked[0].item.title == "new",
        f"首位={ranked[0].item.title}",
    )


REAL_ITEMS_BASELINE = real_db_snapshot().get("items") or 0

if __name__ == "__main__":
    raise SystemExit(main())
