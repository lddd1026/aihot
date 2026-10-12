"""M3 回归调查：`fetch --dry-run` 是否创建 data/aihot.db（M1 验收项 6 失败根因）。

复现 M1 acceptance 检查 6 的场景：空 workspace + 本地假源 → `fetch --dry-run` →
观察 DB 文件是否存在、是否含行；随后真实 fetch 观察落库。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import FakeRSS, Suite, rfc822, run_cli, workspace, write_config  # noqa: E402


def db_state(db: Path) -> dict:
    if not db.exists():
        return {"exists": False}
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        items = (
            conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] if "items" in tables else None
        )
        runs = (
            conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] if "runs" in tables else None
        )
    finally:
        conn.close()
    return {
        "exists": True,
        "bytes": db.stat().st_size,
        "tables": tables,
        "items": items,
        "runs": runs,
    }


def main() -> int:
    suite = Suite("m3_dryrun_db")
    root = workspace("dryrun-db")
    entries = [
        {
            "title": f"dry-run 数据库测试 AI 新闻 {index}",
            "link": f"https://example.com/dryrun/{index}",
            "description": "AI",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 3)
    ]
    with FakeRSS(entries) as rss:
        cfg = write_config(
            root / "config.yaml",
            data_dir=root / "data",
            log_dir=root / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
        )
        db = root / "data" / "aihot.db"
        before = db_state(db)
        dry = run_cli(["fetch", "--config", str(cfg), "--dry-run"], timeout=300)
        after = db_state(db)
        real = run_cli(["fetch", "--config", str(cfg)], timeout=300)
        final = db_state(db)

    suite.check("probe.1 dry-run 前 DB 不存在", before == {"exists": False}, f"before={before}")
    suite.check(
        "probe.2 dry-run 后 DB 文件是否被创建",
        True,  # 观测项，恒真，仅记录
        f"after={after}（M1 验收脚本期望 exists=False）",
    )
    suite.check(
        "probe.3 dry-run 未写入任何行",
        after.get("items") == 0 and after.get("runs") == 0,
        f"items={after.get('items')} runs={after.get('runs')}",
    )
    suite.check(
        "probe.4 真实 fetch 正常落库",
        real.rc == 0 and final.get("items") == 2 and final.get("runs") == 1,
        f"final={final} rc={real.rc}",
    )
    suite.evidence(
        "dryrun-db-probe.txt",
        f"dry-run rc={dry.rc}\n--- dry-run stdout ---\n{dry.stdout}\n"
        f"before={before}\nafter={after}\nfinal={final}\n",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
