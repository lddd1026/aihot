"""打印指定回归汇总的判定统计与失败项（默认第 2 轮）。

用法: python tests/independent/m4_regression_view.py [汇总 JSON 路径]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
default = ROOT / "reports" / "evidence" / "m4" / "regression2" / "10-m0m3-summary.json"
path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
summary = json.loads(path.read_text(encoding="utf-8"))
print("generated:", summary["generated"], "| real_db_unchanged:", summary["real_db_unchanged"])
counts = Counter(row["verdict"] for row in summary["results"])
print("verdicts:", dict(counts), "| total:", len(summary["results"]))
for row in summary["results"]:
    if row["verdict"] not in ("PASS", "OBSERVE", "INFO", "FAIL(P2已知)", "FAIL(P2漂移)"):
        print(f"  {row['verdict']:<6} {row['script']:<28} {row['passed']:<8} exit={row['exit']}")
