"""被测产品代码指纹（用于证明测试期间产品代码未变更）。

用法:
  .venv\\Scripts\\python.exe tests\\independent\\m2_revision.py capture
  .venv\\Scripts\\python.exe tests\\independent\\m2_revision.py compare
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_support import EVIDENCE, PROJECT

TARGETS = ["src", "config.yaml", "config.example.yaml", "pyproject.toml"]
STORED = EVIDENCE / "revision-fingerprint.json"


def collect() -> dict:
    files: dict[str, str] = {}
    for target in TARGETS:
        path = PROJECT / target
        if path.is_file():
            files[target] = _sha256(path)
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix in {".py", ".yaml", ".toml"}:
                    files[str(child.relative_to(PROJECT)).replace("\\", "/")] = _sha256(child)
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
        "count": len(files),
        "overall_sha256": digest,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "capture"
    current = collect()
    if mode == "capture":
        STORED.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"capture: {current['count']} 文件 overall={current['overall_sha256']}")
        print(f"→ {STORED}")
        return 0
    if not STORED.exists():
        print("compare: 缺少指纹文件，请先 capture")
        return 1
    stored = json.loads(STORED.read_text(encoding="utf-8"))
    changed = sorted(
        key for key in set(stored["files"]) | set(current["files"])
        if stored["files"].get(key) != current["files"].get(key)
    )
    same = stored["overall_sha256"] == current["overall_sha256"]
    print(f"compare: 文件数 {stored['count']} → {current['count']}；整体一致={same}")
    print(f"captured_at={stored['captured_at']}；变更文件={changed or '无'}")
    (EVIDENCE / "revision-compare.txt").write_text(
        f"captured_at={stored['captured_at']}\nfiles={stored['count']}→{current['count']}\n"
        f"overall_same={same}\nstored={stored['overall_sha256']}\ncurrent={current['overall_sha256']}\n"
        f"changed={changed or 'none'}\n",
        encoding="utf-8",
    )
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
