"""只读检查真实数据库 data/aihot.db 的状态（不写入、不修改）。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_support import EVIDENCE, PROJECT  # noqa: E402

DB = PROJECT / "data" / "aihot.db"


def main() -> int:
    if not DB.exists():
        print("真实数据库不存在")
        return 0
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    lines: list[str] = []
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    lines.append(f"tables={sorted(tables)}")
    items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    unique = conn.execute(
        "SELECT COUNT(DISTINCT source_id || '|' || url_hash) FROM items"
    ).fetchone()[0]
    runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    lines.append(f"items={items} unique_keys={unique} runs={runs}")
    lines.append("by_source:")
    for row in conn.execute(
        "SELECT source_id, COUNT(*) AS n, MIN(published_at) AS mn, MAX(published_at) AS mx "
        "FROM items GROUP BY source_id ORDER BY n DESC"
    ):
        lines.append(f"  {row['source_id']}: {row['n']} ({row['mn']} → {row['mx']})")
    lines.append("runs_detail:")
    for row in conn.execute(
        "SELECT run_id, status, started_at, finished_at, stage_stats FROM runs "
        "ORDER BY started_at"
    ):
        stats = row["stage_stats"]
        try:
            parsed = json.loads(stats)
            stats = json.dumps(parsed, ensure_ascii=False)[:160]
        except json.JSONDecodeError:
            stats = f"<非法JSON>{stats[:80]}"
        lines.append(f"  {row['run_id']} {row['status']} {row['started_at']} stats={stats}")
    conn.close()
    text = "\n".join(lines)
    print(text)
    path = EVIDENCE / "real-db-readonly-state.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"\n[evidence] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
