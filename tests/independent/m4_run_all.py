"""M4 全量独立测试总入口：运行/汇总全部 m4_* 套件。

用法:
  .venv\\Scripts\\python.exe tests\\independent\\m4_run_all.py           # 仅汇总既有套件 JSON
  .venv\\Scripts\\python.exe tests\\independent\\m4_run_all.py --run     # 逐个执行后再汇总
输出: reports/evidence/m4/run-all-summary.json / .txt
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m4_support import EVIDENCE, PROJECT, PYTHON, RAW  # noqa: E402

SUITES = [
    "m4_net.py",
    "m4_adapters.py",
    "m4_checks.py",
    "m4_alert.py",
    "m4_drills.py",
    "m4_papers.py",
    "m4_bat.py",
    "m4_sources_live.py",
    "m4_probe_storage_error.py",
    "m4_toolchain.py",
]
# 已知且已定位的失败（不阻断；第 2 轮：仅剩「长图降级无告警」这一设计取舍项）
EXPECTED_FAILURE_PREFIXES: dict[str, list[str]] = {
    "m4_alert": ["4.13"],  # 长图降级（partial）不出告警：P2 遗留（内容仍送达）
}


def run_suite(script: str) -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    start = time.perf_counter()
    proc = subprocess.run(
        [str(PYTHON), str(PROJECT / "tests" / "independent" / script)],
        cwd=str(PROJECT),
        capture_output=True,
        timeout=3600,
        env=env,
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"runall-{script}.txt").write_text(output, encoding="utf-8")
    return {
        "script": script,
        "exit": proc.returncode,
        "seconds": round(time.perf_counter() - start, 1),
        "tail": "\n".join(output.strip().splitlines()[-3:]),
    }


def load_summary(script: str) -> dict | None:
    path = EVIDENCE / f"{script.removesuffix('.py')}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main() -> int:
    if "--run" in sys.argv:
        for script in SUITES:
            print(f"--- {script}", flush=True)
            row = run_suite(script)
            print(f"    exit={row['exit']} ({row['seconds']}s)", flush=True)

    results = []
    for script in SUITES:
        name = script.removesuffix(".py")
        summary = load_summary(script)
        if summary is None:
            results.append({"suite": name, "verdict": "MISSING", "passed": 0, "failed": 0})
            continue
        failed_checks = [c["item"] for c in summary.get("checks", []) if not c.get("ok")]
        prefixes = EXPECTED_FAILURE_PREFIXES.get(name, [])
        expected = bool(failed_checks) and all(
            any(item.startswith(prefix) for prefix in prefixes) for item in failed_checks
        )
        results.append(
            {
                "suite": name,
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "failed_checks": failed_checks,
                "expected_failure": expected,
                "verdict": "PASS"
                if summary.get("failed", 0) == 0
                else ("KNOWN(P1/P2)" if expected else "FAIL"),
            }
        )

    total_passed = sum(row["passed"] for row in results)
    total_failed = sum(row["failed"] for row in results)
    unexpected = [row for row in results if row["verdict"] == "FAIL"]
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_passed": total_passed,
        "total_failed": total_failed,
        "unexpected_fail_suites": [row["suite"] for row in unexpected],
        "results": results,
    }
    (EVIDENCE / "run-all-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"{row['verdict']:<12} {row['suite']:<26} "
        f"{row['passed']}/{row['passed'] + row['failed']}"
        + (f"  失败项={row['failed_checks']}" if row.get("failed_checks") else "")
        for row in results
    ]
    lines.append(
        f"\n合计 {total_passed} passed / {total_failed} failed；"
        f"非预期 FAIL 套件={report['unexpected_fail_suites']}"
    )
    (EVIDENCE / "run-all-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not unexpected else 1


if __name__ == "__main__":
    raise SystemExit(main())
