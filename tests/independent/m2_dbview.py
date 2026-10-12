"""只读查看任意 SQLite 库的状态（M2 测试助手，不导入产品代码）。

用法: .venv\\Scripts\\python.exe tests\\independent\\m2_dbview.py [db_path]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def view(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        out: dict = {"db": str(db_path)}
        for table in ("items", "clusters", "digests", "runs"):
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[f"{table}_count"] = row["n"]
        out["enriched"] = conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE enriched_at IS NOT NULL"
        ).fetchone()["n"]
        out["pending"] = conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE enriched_at IS NULL"
        ).fetchone()["n"]
        out["by_source"] = [
            dict(r)
            for r in conn.execute(
                "SELECT source_id, COUNT(*) AS n FROM items GROUP BY source_id ORDER BY source_id"
            )
        ]
        out["by_category"] = [
            dict(r)
            for r in conn.execute(
                "SELECT COALESCE(category,'<null>') AS category, COUNT(*) AS n FROM items "
                "GROUP BY category ORDER BY n DESC"
            )
        ]
        out["clusters_top"] = [
            dict(r)
            for r in conn.execute(
                "SELECT cluster_id, mention_count, source_ids, canonical_title FROM clusters "
                "ORDER BY mention_count DESC LIMIT 15"
            )
        ]
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT / "data" / "aihot.db"
    print(json.dumps(view(target), ensure_ascii=False, indent=2))
