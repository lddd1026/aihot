"""汇总本轮回归中各独立脚本的 FAIL 明细（供分诊）。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "reports" / "evidence" / "m3" / "logs"

for path in sorted(LOGS.glob("*.txt")):
    text = path.read_text(encoding="utf-8", errors="replace")
    fails = [line.strip() for line in text.splitlines() if "[FAIL]" in line]
    if not fails:
        continue
    print(f"===== {path.name} ({len(fails)} FAIL)")
    for line in fails:
        print("   " + line[:400])
