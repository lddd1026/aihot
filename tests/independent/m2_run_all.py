"""M2 全量独立测试总入口：按顺序执行全部 m2_* 脚本并汇总证据。

用法: .venv\\Scripts\\python.exe tests\\independent\\m2_run_all.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_support import EVIDENCE, PROJECT, PYTHON, real_db_snapshot

OUT = EVIDENCE

SUITES = [
    "m2_prefilter.py",
    "m2_dedup.py",
    "m2_llm_robust.py",
    "m2_cache.py",
    "m2_cost.py",
    "m2_rank.py",
    "m2_cli.py",
    "m2_real_llm.py",
    "m2_perf.py",
    "m2r2_d1.py",
    "m2r2_accuracy.py",
    "m2r2_d2.py",
    "m2r2_p2.py",
    "m2r3_p2.py",
    "m2r3_real_db.py",
    "m2_regression.py",
]
INFO_SCRIPTS = ["m2_translation_sample.py", "m2_dbview.py", "m2_dedup_probe.py"]


def run(script: str, env: dict[str, str]) -> dict:
    start = time.perf_counter()
    proc = subprocess.run(
        [str(PYTHON), str(PROJECT / "tests" / "independent" / script)],
        cwd=str(PROJECT), capture_output=True, timeout=3600, env=env,
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    seconds = round(time.perf_counter() - start, 1)
    match = re.search(r"== (\S+): (\d+) passed / (\d+) failed ==", output)
    if script in INFO_SCRIPTS:
        verdict = "INFO" if proc.returncode == 0 else "ERROR"
        passed = "-"
    elif match:
        passed = f"{match.group(2)}/{int(match.group(2)) + int(match.group(3))}"
        verdict = "PASS" if match.group(3) == "0" else "FAIL"
    else:
        passed = "?"
        verdict = "?"
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "logs" / f"{script}.txt").write_text(output, encoding="utf-8")
    return {
        "script": script, "exit": proc.returncode, "passed": passed, "verdict": verdict,
        "seconds": seconds,
        "tail": "\n".join(output.strip().splitlines()[-5:]),
    }


def main() -> int:
    before = real_db_snapshot()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    results: list[dict] = []
    print(f"真实库快照（前）: {json.dumps(before, ensure_ascii=False)}")
    for script in SUITES + INFO_SCRIPTS:
        row = run(script, env)
        results.append(row)
        print(f"  {row['verdict']:<6} {script:<24} exit={row['exit']} "
              f"{row['passed']:<8} {row['seconds']}s")
    after = real_db_snapshot()
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "real_db_before": before,
        "real_db_after": after,
        "real_db_unchanged": before == after,
        "results": results,
    }
    (OUT / "run-all-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"{row['verdict']:<6} {row['script']:<24} exit={row['exit']} {row['passed']:<8} "
        f"{row['seconds']}s"
        for row in results
    ]
    (OUT / "run-all-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = [row for row in results if row["verdict"] not in {"PASS", "INFO"}]
    print(f"\n总脚本 {len(results)}；FAIL/异常 {len(failed)}；"
          f"真实库未变={report['real_db_unchanged']}")
    print(f"摘要 → {OUT / 'run-all-summary.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
