"""最终核对：run-all 汇总 + 真实库/存档最终快照。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import real_state_snapshot  # noqa: E402

EVID = Path(__file__).resolve().parents[2] / "reports" / "evidence" / "m3"
report = json.loads((EVID / "run-all-summary.json").read_text(encoding="utf-8"))
print(f"真实库未变={report['real_db_unchanged']}")
counts: dict[str, int] = {}
for row in report["results"]:
    key = row["verdict"].split("(")[0]
    counts[key] = counts.get(key, 0) + 1
print("判定分布:", counts)
for row in report["results"]:
    if row["verdict"] not in {"PASS", "OBSERVE", "INFO"}:
        print(f"  非 PASS: {row['verdict']:<12} {row['script']:<24} {row['passed']}")

print("\n最终真实库快照:", json.dumps(real_state_snapshot(), ensure_ascii=False))
