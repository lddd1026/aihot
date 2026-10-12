"""被测版本指纹：记录/比对 src 与配置文件哈希，防止"测试期间代码被改动"。"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_support import EVIDENCE, PROJECT  # noqa: E402


def fingerprint() -> dict[str, dict[str, object]]:
    targets: list[Path] = []
    targets.extend(sorted((PROJECT / "src").rglob("*.py")))
    for name in ("pyproject.toml", "config.example.yaml"):
        candidate = PROJECT / name
        if candidate.exists():
            targets.append(candidate)
    result: dict[str, dict[str, object]] = {}
    for path in targets:
        data = path.read_bytes()
        result[str(path.relative_to(PROJECT))] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        }
    return result


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    current = fingerprint()
    path = EVIDENCE / "revision-fingerprint.json"
    combined = json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True)
    if mode == "compare":
        if not path.exists():
            print("无 baseline 指纹，先执行 snapshot")
            return 1
        previous = json.loads(path.read_text(encoding="utf-8"))
        changed = [
            key
            for key in sorted(set(previous) | set(current))
            if previous.get(key, {}).get("sha256") != current.get(key, {}).get("sha256")
        ]
        print(f"对比结果: {len(changed)} 个文件发生变化")
        for key in changed:
            print(f"  CHANGED {key}")
        return 1 if changed else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(combined + "\n", encoding="utf-8")
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    print(f"已记录 {len(current)} 个文件指纹 → {path.name}")
    print(f"整体指纹 sha256={digest}")
    for key, meta in current.items():
        print(f"  {meta['sha256'][:12]}  {meta['size']:>6}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
