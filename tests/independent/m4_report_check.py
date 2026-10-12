"""第 2 轮报告完整性检查。"""

from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[2]
text = (root / "reports" / "M4-test-report.md").read_text(encoding="utf-8")
fence = chr(96) * 3
print("report bytes:", len(text.encode("utf-8")))
print("has R2 section:", "# 8. 回归验证（第 2 轮）" in text)
print("has new verdict:", "第 2 轮结论：PASS" in text)
print("fences:", text.count(fence), "balanced:", text.count(fence) % 2 == 0)
evidence = root / "reports" / "evidence" / "m4" / "regression2"
print("regression2 files:", len([p for p in evidence.rglob("*") if p.is_file()]))
