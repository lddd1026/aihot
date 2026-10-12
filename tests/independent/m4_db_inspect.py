"""M4 独立验证：只读检查真实库状态（不修改 data/aihot.db）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path("data/aihot.db")


def main() -> int:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print("tables:", [row[0] for row in cur.fetchall()])

    cur.execute("SELECT COUNT(*) FROM items")
    print("items total:", cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM items WHERE enriched_at IS NULL")
    print("unenriched total:", cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*) FROM items "
        "WHERE enriched_at IS NULL AND published_at > datetime('now','-24 hours')"
    )
    print("unenriched last24h(utc):", cur.fetchone()[0])
    cur.execute('SELECT COUNT(*) FROM items WHERE extra LIKE \'%"kind": "paper"%\'')
    print("items kind=paper:", cur.fetchone()[0])

    cur.execute("SELECT run_id, status, stage_stats FROM runs ORDER BY rowid DESC LIMIT 15")
    print("runs (latest 15):")
    for run_id, status, stats in cur.fetchall():
        print(f"  {run_id} | {status} | {str(stats)[:200]}")

    cur.execute("SELECT COUNT(*) FROM digests")
    print("digests total:", cur.fetchone()[0])
    cur.execute("SELECT digest_date FROM digests ORDER BY digest_date DESC LIMIT 5")
    print("digest dates (latest 5):", [row[0] for row in cur.fetchall()])
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
