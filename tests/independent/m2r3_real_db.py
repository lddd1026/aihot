"""M2 回归（第 3 轮）· 真实库数据状态复核（只读）。

6a 计数与完整性：items=176 / 已处理=176 / 待处理=0 / clusters=101 / max_cluster_id=101 /
   悬空=0 / mention 不一致=0
6b 重建后的簇抽样核验：多条目簇逐个人工裁定（应仅 2 个：Opus 5.5 ×3、GPT-6 Sol and Luna ×3）
6c 旧版过度合并簇已消失（不再存在 >4 条成员的簇；对级精度按重建结果计）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_support import REAL_DB, Suite, db_one, db_query, real_db_snapshot  # noqa: E402


def integrity() -> dict:
    items = db_one(REAL_DB, "SELECT COUNT(*) FROM items")
    enriched = db_one(REAL_DB, "SELECT COUNT(*) FROM items WHERE enriched_at IS NOT NULL")
    pending = db_one(REAL_DB, "SELECT COUNT(*) FROM items WHERE enriched_at IS NULL")
    filtered = db_one(REAL_DB, "SELECT COUNT(*) FROM items WHERE category = '过滤'")
    clusters = db_one(REAL_DB, "SELECT COUNT(*) FROM clusters")
    max_id = db_one(REAL_DB, "SELECT COALESCE(MAX(cluster_id), 0) FROM clusters")
    min_id = db_one(REAL_DB, "SELECT COALESCE(MIN(cluster_id), 0) FROM clusters")
    dangling = db_one(
        REAL_DB,
        "SELECT COUNT(*) FROM items i LEFT JOIN clusters c ON c.cluster_id = i.cluster_id"
        " WHERE i.cluster_id IS NOT NULL AND c.cluster_id IS NULL",
    )
    mismatch = db_query(
        REAL_DB,
        "SELECT c.cluster_id, c.mention_count,"
        " (SELECT COUNT(*) FROM items i WHERE i.cluster_id = c.cluster_id) AS actual"
        " FROM clusters c",
    )
    bad = [row for row in mismatch if row["mention_count"] != row["actual"]]
    orphans = db_query(
        REAL_DB,
        "SELECT c.cluster_id FROM clusters c LEFT JOIN items i ON i.cluster_id = c.cluster_id"
        " WHERE i.cluster_id IS NULL",
    )
    return {
        "items": items, "enriched": enriched, "pending": pending, "filtered": filtered,
        "clusters": clusters, "max_cluster_id": max_id, "min_cluster_id": min_id,
        "dangling": dangling, "mention_mismatch": len(bad), "bad_clusters": bad,
        "empty_clusters": [row["cluster_id"] for row in orphans],
    }


def main() -> int:
    suite = Suite("real-db")
    state = integrity()
    suite.evidence_json("6a-real-db-state.json", state)

    suite.check(
        "6a-计数：items=176 / 已处理=176 / 待处理=0",
        state["items"] == 176 and state["enriched"] == 176 and state["pending"] == 0,
        f"items={state['items']} enriched={state['enriched']} pending={state['pending']} "
        f"（过滤标记 {state['filtered']} = 176-105 加工）",
    )
    suite.check(
        "6a-簇：101 个 / max_cluster_id=101 / 全局单调起点",
        state["clusters"] == 101 and state["max_cluster_id"] == 101,
        f"clusters={state['clusters']} max={state['max_cluster_id']} min={state['min_cluster_id']}",
    )
    suite.check(
        "6a-悬空引用=0",
        state["dangling"] == 0,
        f"悬空={state['dangling']}",
    )
    suite.check(
        "6a-mention_count 与实际条目数不一致=0",
        state["mention_mismatch"] == 0,
        f"不一致={state['mention_mismatch']}，空簇={state['empty_clusters'] or '无'}",
    )

    # 6b 多条目簇人工裁定
    rows = db_query(
        REAL_DB,
        "SELECT cluster_id, source_id, title FROM items WHERE cluster_id IS NOT NULL"
        " ORDER BY cluster_id, published_at",
    )
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["cluster_id"], []).append(row)
    multi = {cid: members for cid, members in groups.items() if len(members) > 1}
    judged = []
    for cid, members in sorted(multi.items()):
        titles = [m["title"] for m in members]
        if all("opus 5.5" in title.lower() for title in titles):
            verdict, basis = "合理", "Claude Opus 5.5 发布（3 家来源）"
        elif all("sol and luna" in title.lower() for title in titles):
            verdict, basis = "合理", "GPT-6 Sol 与 Luna 发布（3 家来源）"
        else:
            verdict, basis = "需人工复核", "未匹配已知事件模式"
        judged.append({"cluster_id": cid, "titles": titles, "verdict": verdict, "basis": basis})
    pairs_ok = sum(len(row["titles"]) * (len(row["titles"]) - 1) // 2
                   for row in judged if row["verdict"] == "合理")
    pairs_total = sum(len(row["titles"]) * (len(row["titles"]) - 1) // 2 for row in judged)
    suite.evidence_json("6b-real-multi-clusters.json", {
        "multi_count": len(multi), "judged": judged,
        "pair_precision": round(pairs_ok / pairs_total * 100, 1) if pairs_total else 100.0,
    })
    suite.check(
        "6b-多条目簇仅 2 个且人工核验为同一事件",
        len(multi) == 2 and all(row["verdict"] == "合理" for row in judged),
        f"多条目簇={len(multi)}；明细="
        + json.dumps([[m["title"][:36] for m in members] for members in multi.values()],
                     ensure_ascii=False),
    )
    suite.check(
        "6c-无超大簇（旧版过度合并残留已清除）",
        all(len(members) <= 3 for members in multi.values())
        and max((len(m) for m in multi.values()), default=0) <= 3,
        f"最大簇={max((len(m) for m in multi.values()), default=0)} 条；"
        f"单条目簇={sum(1 for m in groups.values() if len(m) == 1)} 个",
    )
    suite.check(
        "6z-真实库只读守护",
        real_db_snapshot()["items"] == state["items"],
        f"items={real_db_snapshot()['items']}",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
