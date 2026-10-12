"""M7 独立验收 · 声明 4/5：组装层排除三分区 + 排序层负分。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_compose_rank.py
证据: reports/evidence/m7r/m7r_compose_rank/ 与 m7r_compose_rank.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m7r_support as m7  # noqa: E402

from aihot.pipeline.rank import rank_items, score_item  # noqa: E402
from aihot.presentation.compose import (  # noqa: E402
    EXCLUDED_CATEGORIES,
    compose_digest,
    is_publishable,
)

EXCLUDED = ("行业动态", "融资商业", "政策监管")


def main() -> int:
    suite = m3.Suite("m7r_compose_rank")
    ws = m3.workspace("m7r-compose-rank")
    _, cfg = m7.write_and_load(ws, pipeline=m7.base_pipeline_raw())
    bonus = cfg.pipeline.rank.interest_bonus

    # ---------- 声明 5：负分生效（同内容仅分类不同） ----------
    same_base = dict(importance=80)
    tech = m7.make_news(1, title="某团队发布开源推理框架", category="模型发布", **same_base)
    business = m7.make_news(2, title="某团队发布开源推理框架", category="融资商业", **same_base)
    tech_score, tech_break = score_item(tech, interest_bonus=bonus)
    biz_score, biz_break = score_item(business, interest_bonus=bonus)
    suite.check(
        "5.1 同内容下商业类分数显著低于技术类（负分生效）",
        tech_score - biz_score >= 39.0 and biz_break["interest_bonus"] < 0,
        f"模型发布={tech_score}（bonus={tech_break['interest_bonus']}）"
        f" vs 融资商业={biz_score}（bonus={biz_break['interest_bonus']}）"
        f"，差={tech_score - biz_score:.2f}",
    )
    strong_biz = m7.make_news(
        3, title="头部公司 99 分商业大新闻", category="融资商业", importance=99
    )
    mid_tech = m7.make_news(4, title="普通技术进展", category="研究突破", importance=60)
    sb_score, _ = score_item(strong_biz, interest_bonus=bonus)
    mt_score, _ = score_item(mid_tech, interest_bonus=bonus)
    suite.check(
        "5.2 高权重商业条目(99) 仍低于中等技术条目(60)（压沉底）",
        mt_score > sb_score,
        f"融资商业(99)={sb_score} < 研究突破(60)={mt_score}",
    )
    suite.evidence_json(
        "5-scores.json",
        {
            "interest_bonus": bonus,
            "pairs": [
                {
                    "tech": tech_score,
                    "business": biz_score,
                    "diff": round(tech_score - biz_score, 2),
                },
                {"business_99": sb_score, "tech_60": mt_score},
            ],
            "tech_breakdown": tech_break,
            "business_breakdown": biz_break,
        },
    )

    # ---------- 声明 4：组装层排除 ----------
    items = [
        m7.make_news(11, title="A 团队发布开源模型 Nova", category="模型发布", importance=70),
        m7.make_news(12, title="爆火开源工具 AgentDesk", category="开源工具", importance=90),
        m7.make_news(13, title="长上下文对齐新方法", category="研究突破", importance=60),
        m7.make_news(14, title="编辑器插件更新：AI 补全增强", category="产品更新", importance=50),
        m7.make_news(15, title="某公司 50 亿美元被收购", category="融资商业", importance=99),
        m7.make_news(16, title="显卡出货量回升带动产业链", category="行业动态", importance=99),
        m7.make_news(17, title="新监管条例征求意见", category="政策监管", importance=99),
        m7.make_news(
            18, title="硬件评测论文：新型互连", category="行业动态", importance=95, kind="paper"
        ),
        m7.make_news(
            19, title="技术论文：长上下文新方法", category="研究突破", importance=80, kind="paper"
        ),
    ]
    ranked = rank_items(items, weights=cfg.source_weights, interest_bonus=bonus)
    digest = compose_digest(ranked, digest_date=date(2026, 10, 12), top_n=5, brief_count=5)
    sections = {
        "top": [item.category for item in digest.top_items],
        "briefs": [item.category for item in digest.briefs],
        "papers": [item.category for item in digest.papers],
    }
    suite.evidence_json(
        "4-digest-sections.json",
        {
            "sections": sections,
            "titles": {
                "top": [item.title for item in digest.top_items],
                "briefs": [item.title for item in digest.briefs],
                "papers": [item.title for item in digest.papers],
            },
            "stats": digest.stats,
        },
    )
    flat = sections["top"] + sections["briefs"] + sections["papers"]
    leaked = [cat for cat in flat if cat in EXCLUDED]
    suite.check(
        "4.1 行业动态/融资商业/政策监管 不进任何区块（精选/简讯/论文）",
        not leaked,
        f"区块分类={sections} 泄漏={leaked}",
    )
    suite.check(
        "4.2 技术类分类正常进入（4 条新闻进精选 + 1 篇技术论文进论文）",
        sections["top"] == ["开源工具", "模型发布", "研究突破", "产品更新"]
        and sections["papers"] == ["研究突破"],
        f"top={sections['top']} papers={sections['papers']}",
    )
    expected_order = [item.title for item in digest.top_items]
    score_of = {entry.item.title: entry.score for entry in ranked}
    by_score = sorted(
        [
            entry
            for entry in ranked
            if entry.item.category not in EXCLUDED and entry.item.extra.get("kind") != "paper"
        ],
        key=lambda entry: entry.score,
        reverse=True,
    )
    suite.check(
        "4.3 同日混合时顺序正确：精选按综合分降序（商业高分沉底被排除）",
        expected_order == [entry.item.title for entry in by_score][: len(expected_order)],
        "顺序=" + " > ".join(f"{title}({score_of[title]})" for title in expected_order),
    )
    manual = {item.title: is_publishable(item) for item in items}
    manual_excluded_ok = all(not manual[item.title] for item in items if item.category in EXCLUDED)
    manual_tech_ok = all(
        manual[item.title]
        for item in items
        if item.category not in EXCLUDED and item.category != "其他"
    )
    suite.check(
        "4.4 is_publishable：三个排除分类均 False，技术类均 True",
        manual_excluded_ok and manual_tech_ok,
        f"EXCLUDED_CATEGORIES={sorted(EXCLUDED_CATEGORIES)}；逐条={manual}",
    )
    suite.check(
        "4.5 论文区同样受排他约束（硬件论文不进论文区）",
        all("硬件评测论文" not in item.title for item in digest.papers),
        f"papers={[item.title for item in digest.papers]}",
    )
    digest2 = compose_digest(ranked, digest_date=date(2026, 10, 12), top_n=2, brief_count=5)
    brief_categories = [item.category for item in digest2.briefs]
    suite.evidence_json(
        "4.6-briefs.json",
        {
            "top": [item.title for item in digest2.top_items],
            "briefs": [
                {"title": item.title, "category": item.category} for item in digest2.briefs
            ],
        },
    )
    suite.check(
        "4.6 简讯区非空时同样排除三分区（top_n=2 → 技术条目落入简讯，无排除分类）",
        bool(brief_categories) and all(category not in EXCLUDED for category in brief_categories),
        f"briefs={brief_categories}",
    )

    # 附加观察：interest_bonus 为「整体替换」，部分覆盖时未列分类加成归零
    partial_ws = m3.workspace("m7r-bonus-partial")
    partial_pipeline = m7.base_pipeline_raw()
    partial_pipeline.setdefault("rank", {})["interest_bonus"] = {"模型发布": 20}
    _, partial_cfg = m7.write_and_load(partial_ws, pipeline=partial_pipeline)
    partial_bonus = partial_cfg.pipeline.rank.interest_bonus
    suite.evidence_json("6-partial-bonus.json", {"loaded": partial_bonus})
    if len(partial_bonus) == 1 and "融资商业" not in partial_bonus:
        suite.note(
            "P3（语义观察）：pipeline.rank.interest_bonus 是整体替换而非与默认合并——"
            "只写一个分类时其余分类加成归零（本轮断言基于完整配置，不影响声明 5 判定）。"
        )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
