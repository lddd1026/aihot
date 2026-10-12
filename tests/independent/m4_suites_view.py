"""打印 reports/evidence/m4 下各套件 JSON 的通过/失败与失败项。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "reports" / "evidence" / "m4"
for path in sorted(ROOT.glob("m4_*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    failed = [c["item"] for c in data.get("checks", []) if not c.get("ok")]
    ok = data.get("failed", 0) == 0
    print(
        f"{'PASS' if ok else 'FAIL'} {path.stem:<28} "
        f"{data.get('passed')}/{data.get('passed', 0) + data.get('failed', 0)}"
        + (f"  失败={failed}" if failed else "")
    )
