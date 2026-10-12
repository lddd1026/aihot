"""M4 真实库/存档快照（前后对比用）。

证据目录由 AIHOT_M4_EVIDENCE 控制（默认 m4；第 2 轮为 m4/regression2）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AIHOT_M4_EVIDENCE", "m4")

from m4_support import RAW, real_state_snapshot  # noqa: E402


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else "before"
    snapshot = real_state_snapshot()
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"00-real-state-{tag}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot({tag}) -> {path}")
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
