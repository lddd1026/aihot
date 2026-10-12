"""M2 验收 2 辅助：导出真实库副本的规则聚类结果，供人工核验（一次运行，只读源库）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_support import (  # noqa: E402
    REAL_DB,
    copy_real_db,
    db_query,
    default_pipeline,
    reset_enrichment,
    run_cli,
    workspace,
    write_config,
)


def main() -> int:
    base = workspace("dedup-probe")
    db_path = copy_real_db(base / "data" / "aihot.db")
    reset_enrichment(db_path)
    cfg = write_config(
        base / "config.yaml",
        pipeline=default_pipeline(),
        data_dir=base / "data",
        log_dir=base / "logs",
    )
    result = run_cli(["enrich", "--no-llm", "--config", str(cfg), "--limit", "400", "--top", "1"])
    print(result.stdout)
    rows = db_query(
        db_path,
        "SELECT cluster_id, source_id, title, published_at FROM items "
        "WHERE enriched_at IS NOT NULL AND category != '过滤' ORDER BY cluster_id, published_at",
    )
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["cluster_id"] or -1, []).append(row)
    multi = {cid: members for cid, members in groups.items() if len(members) > 1}
    print(f"\n簇总数={len(groups)}，多条目簇={len(multi)}")
    for cid, members in sorted(multi.items(), key=lambda kv: -len(kv[1])):
        print(f"\n--- cluster {cid} ({len(members)}) ---")
        for m in members:
            print("   ", m["source_id"], "|", m["title"])
    Path(base / "clusters.json").write_text(
        json.dumps({str(k): v for k, v in groups.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n写入 {base / 'clusters.json'}")
    print("真实库:", REAL_DB)

    # 生产库（开发者真实运行 + LLM 精修）的簇
    prod = db_query(
        REAL_DB,
        "SELECT cluster_id, source_id, title FROM items WHERE cluster_id IS NOT NULL "
        "ORDER BY cluster_id, published_at",
    )
    prod_groups: dict[int, list[dict]] = {}
    for row in prod:
        prod_groups.setdefault(row["cluster_id"], []).append(row)
    print("\n=== 生产库（含 LLM 精修）多条目簇 ===")
    for cid, members in sorted(prod_groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) > 1:
            print(f"\n--- prod cluster {cid} ({len(members)}) ---")
            for m in members:
                print("   ", m["source_id"], "|", m["title"])
    Path(base / "prod-clusters.json").write_text(
        json.dumps({str(k): v for k, v in prod_groups.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
