"""M3 验收项 1：日报组装（Top N + 简讯切分、噪音排除、统计自洽）。

判定依据：
- 纯函数夹具验证（compose_digest / is_publishable 行为）
- 临时库 + build_digest_from_db 的端到端验证（与 preview/resend 同源）
- 真实库只读快照（前后一致）
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    Suite,
    db_one,
    make_item,
    real_state_snapshot,
    seed_enriched_items,
    workspace,
    write_config,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.config import load_config  # noqa: E402
from aihot.pipeline.rank import rank_items  # noqa: E402
from aihot.presentation.compose import compose_digest, is_publishable  # noqa: E402
from aihot.runner import build_digest_from_db  # noqa: E402


def build_mixed_ranked():
    """构造混合输入：正常条目 + 过滤 + 低分其他 + 空标题。"""
    items = []
    # 8 条正常（重要性递减，保证排序稳定）
    for index in range(1, 9):
        items.append(make_item(index, title=f"正常条目 {index}", importance=95 - index))
    # 3 条被预筛标记为「过滤」
    for index in range(20, 23):
        items.append(make_item(index, title=f"过滤条目 {index}", category="过滤", importance=90))
    # 2 条「其他」低分噪音（<=15 应排除）
    items.append(make_item(30, title="低分其他 10", category="其他", importance=10))
    items.append(make_item(31, title="低分其他 15", category="其他", importance=15))
    # 2 条「其他」但有价值（>15 应保留）
    items.append(make_item(32, title="其他-16", category="其他", importance=16))
    items.append(make_item(33, title="其他-40", category="其他", importance=40))
    # 1 条空标题（cn_title 与 title 均为空 → 排除）
    blank = make_item(40, title="")
    blank.cn_title = ""
    blank.title = ""
    items.append(blank)
    return rank_items(items)


def main() -> int:
    suite = Suite("m3_compose")
    before = real_state_snapshot()

    # ---------- 1. is_publishable 单点行为 ----------
    checks = [
        ("过滤类排除", not is_publishable(make_item(1, category="过滤")), True),
        (
            "其他-importance10 排除",
            not is_publishable(make_item(2, category="其他", importance=10)),
            True,
        ),
        (
            "其他-importance15 排除（边界）",
            not is_publishable(make_item(3, category="其他", importance=15)),
            True,
        ),
        (
            "其他-importance16 保留",
            is_publishable(make_item(4, category="其他", importance=16)),
            True,
        ),
        ("正常分类保留", is_publishable(make_item(5, category="模型发布", importance=50)), True),
    ]
    blank = make_item(6)
    blank.cn_title = ""
    blank.title = ""
    checks.append(("空标题排除", not is_publishable(blank), True))
    for name, actual, expected in checks:
        suite.check(f"1.1 {name}", actual is expected, f"actual={actual}")

    # ---------- 2. Top N / 简讯切分 ----------
    ranked = build_mixed_ranked()
    digest = compose_digest(
        ranked, digest_date=date(2026, 9, 24), top_n=5, summary="综述", brief_count=3
    )
    top_titles = [item.cn_title or item.title for item in digest.top_items]
    brief_titles = [item.cn_title or item.title for item in digest.briefs]
    publishable = [entry.item for entry in ranked if is_publishable(entry.item)]
    expected_top = [item.cn_title or item.title for item in publishable[:5]]
    expected_brief = [item.cn_title or item.title for item in publishable[5:8]]

    suite.check("1.2 Top N 条数与顺序", top_titles == expected_top, f"top={top_titles}")
    suite.check("1.3 简讯切分紧随 Top N", brief_titles == expected_brief, f"briefs={brief_titles}")
    suite.check(
        "1.4 排除项未进入日报",
        not any(t.startswith(("过滤条目", "低分其他")) for t in top_titles + brief_titles),
        f"top+briefs={top_titles + brief_titles}",
    )
    suite.check(
        "1.5 统计自洽（selected/briefs）",
        digest.stats["selected"] == len(digest.top_items) == 5
        and digest.stats["briefs"] == len(digest.briefs) == 3,
        f"stats={digest.stats}",
    )
    suite.check(
        "1.6 「其他-16/40」在可发布池中（未被误杀）",
        all(
            any((item.cn_title or item.title) == name for item in publishable)
            for name in ("其他-16", "其他-40")
        ),
        f"publishable={[item.cn_title or item.title for item in publishable]}",
    )

    # ---------- 3. 边界：top_n 超出可发布数 / brief_count=0 / top_n=0 ----------
    small = compose_digest(ranked, digest_date=date(2026, 9, 24), top_n=99, brief_count=12)
    suite.check(
        "1.7 top_n 超界 → 全部进 Top、简讯为空",
        len(small.top_items) == len(publishable) and small.briefs == [],
        f"top={len(small.top_items)} publishable={len(publishable)} briefs={len(small.briefs)}",
    )
    zero_brief = compose_digest(ranked, digest_date=date(2026, 9, 24), top_n=5, brief_count=0)
    suite.check(
        "1.8 brief_count=0 → 无简讯",
        zero_brief.briefs == [] and len(zero_brief.top_items) == 5,
        f"briefs={len(zero_brief.briefs)}",
    )
    zero_top = compose_digest(ranked, digest_date=date(2026, 9, 24), top_n=0, brief_count=4)
    suite.check(
        "1.9 top_n=0 → 无精选、简讯取前 4",
        zero_top.top_items == [] and len(zero_top.briefs) == 4,
        f"top={len(zero_top.top_items)} briefs={len(zero_top.briefs)}",
    )

    # ---------- 4. 端到端：临时库 → build_digest_from_db ----------
    root = workspace("compose-db")
    data_dir = root / "data"
    cfg_path = write_config(root / "config.yaml", data_dir=data_dir, log_dir=root / "logs", top_n=5)
    db_path = data_dir / "aihot.db"
    seed_rows = []
    for index in range(1, 13):
        seed_rows.append(
            {
                "source_id": "qbitai",
                "title": f"DB 正常 {index}",
                "url": f"https://example.com/db/{index}",
                "cn_title": f"DB 正常 {index}",
                "cn_summary": f"摘要 {index}",
                "category": "模型发布",
                "importance": 95 - index,
                "cluster_id": index,
            }
        )
    for index in range(13, 17):
        seed_rows.append(
            {
                "source_id": "qbitai",
                "title": f"DB 过滤 {index}",
                "url": f"https://example.com/db/{index}",
                "cn_title": f"DB 过滤 {index}",
                "category": "过滤",
                "importance": 99,
                "cluster_id": index,
            }
        )
    seed_rows.append(
        {
            "source_id": "qbitai",
            "title": "DB 低分其他",
            "url": "https://example.com/db/other-low",
            "cn_title": "DB 低分其他",
            "category": "其他",
            "importance": 5,
            "cluster_id": 20,
        }
    )
    seed_rows.append(
        {
            "source_id": "qbitai",
            "title": "DB 高分其他",
            "url": "https://example.com/db/other-high",
            "cn_title": "DB 高分其他",
            "category": "其他",
            "importance": 55,
            "cluster_id": 21,
        }
    )
    seed_enriched_items(db_path, seed_rows)
    cfg = load_config(cfg_path)
    digest = build_digest_from_db(cfg, top_n=5, summary="库内综述")
    titles = [item.cn_title or item.title for item in digest.top_items] + [
        item.cn_title or item.title for item in digest.briefs
    ]
    suite.check(
        "1.10 端到端：Top5 来自库内可发布条目（排除过滤/低分其他）",
        len(digest.top_items) == 5
        and not any(t.startswith(("DB 过滤", "DB 低分其他")) for t in titles),
        f"titles={titles}",
    )
    suite.check(
        "1.11 端到端：高分「其他」进入候选（top 或 brief）",
        "DB 高分其他" in titles,
        f"titles={titles}",
    )
    suite.check(
        "1.12 端到端：统计与库内数据自洽",
        digest.stats["scanned"] == 14
        and digest.stats["kept"] == 14
        and digest.stats["clusters"] == 14
        and digest.stats["selected"] == 5
        and digest.stats["briefs"] == len(digest.briefs) == 8
        and len(titles) == 13,
        f"stats={digest.stats} titles={len(titles)}",
    )
    suite.check(
        "1.13 端到端：过滤条目未写入日报 payload",
        db_one(db_path, "SELECT COUNT(*) FROM items WHERE category='过滤'") == 4,
        f"db 过滤条数={db_one(db_path, "SELECT COUNT(*) FROM items WHERE category='过滤'")}",
    )
    suite.evidence_json(
        "1-compose-result.json",
        {
            "top": titles[:5],
            "briefs": titles[5:],
            "stats": digest.stats,
            "publishable_count": len(publishable),
        },
    )

    after = real_state_snapshot()
    suite.check("1.14 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
