"""M3 汇总统计（报告用）：各套件通过数 + 真实长图规格 + 关键数字。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import EVIDENCE, REAL_DIGESTS, jpeg_info  # noqa: E402

total_passed = 0
total_failed = 0
print("== 各套件结果 ==")
for path in sorted(EVIDENCE.glob("m3_*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    passed, failed = data["passed"], data["failed"]
    total_passed += passed
    total_failed += failed
    print(f"{data['suite']:<24} {passed}/{passed + failed}")

print(f"\n合计: {total_passed} passed / {total_failed} failed")

print("\n== 真实日报长图规格（只读）==")
for path in sorted(REAL_DIGESTS.glob("*.jpg")):
    info = jpeg_info(path)
    print(f"{path.name}: {info}")

print("\n== 极端内容 markdown 分片 ==")
wecom = json.loads((EVIDENCE / "m3_wecom.json").read_text(encoding="utf-8"))
for check in wecom["checks"]:
    if check["item"].startswith(("4.5", "4.7", "4.8", "4.9")):
        print(f"{check['item']}: {check['detail']}")

print("\n== e2e 指标 ==")
metrics = json.loads((EVIDENCE / "m3_e2e" / "e2e-metrics.json").read_text(encoding="utf-8"))
print(json.dumps(metrics, ensure_ascii=False, indent=2))

print("\n== 图片指标 ==")
images = json.loads(
    (EVIDENCE / "m3_render_image" / "3-image-metrics.json").read_text(encoding="utf-8")
)
print(
    json.dumps(
        {
            "normal": {k: images["normal"][k] for k in ("bytes", "width", "height")},
            "mega_real_limit_sizes": [i["bytes"] for i in images["mega_real_limit"]["infos"]],
            "mega_real_limit_heights": [i["height"] for i in images["mega_real_limit"]["infos"]],
            "forced_split_threshold": images["mega_forced_split"]["threshold"],
            "forced_split_sizes": [i["bytes"] for i in images["mega_forced_split"]["parts"]],
            "timings": images["timings"],
        },
        ensure_ascii=False,
        indent=2,
    )
)
