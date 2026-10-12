"""M7 独立验收 · 一键运行器（内容范围调整为开发者向）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_run_all.py
产出: reports/evidence/m7r/run-<suite>.log + run-all-summary.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
EVIDENCE_ROOT = os.environ.get("AIHOT_M7R_EVIDENCE", "m7r")
EVIDENCE = PROJECT / "reports" / "evidence" / EVIDENCE_ROOT

SUITES: list[tuple[str, str]] = [
    ("m7r_prefilter", "m7r_prefilter.py"),
    ("m7r_compose_rank", "m7r_compose_rank.py"),
    ("m7r_e2e", "m7r_e2e.py"),
    ("m7r_docs", "m7r_docs.py"),
    ("m7r_regression", "m7r_regression.py"),
]


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, object]] = []
    for name, script in SUITES:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["AIHOT_M7R_EVIDENCE"] = EVIDENCE_ROOT
        env["AIHOT_M5R_SKIP_M4_ALERT"] = "1"
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [str(PYTHON), str(HERE / script)],
                cwd=str(PROJECT),
                capture_output=True,
                env=env,
                timeout=5400,
            )
            rc: int | str = proc.returncode
            out = proc.stdout + b"\n--- stderr ---\n" + proc.stderr
        except subprocess.TimeoutExpired as exc:
            rc = "timeout"
            out = (exc.stdout or b"") + b"\n[harness] timeout"
        text = out.decode("utf-8", "replace")
        (EVIDENCE / f"run-{name}.log").write_text(text, encoding="utf-8")
        record = {"suite": name, "rc": rc, "seconds": round(time.perf_counter() - start, 1)}
        summary.append(record)
        print(f"[{'PASS' if rc == 0 else 'FAIL'}] {name} rc={rc} ({record['seconds']}s)")
        sys.stdout.flush()
    (EVIDENCE / "run-all-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failed = [row for row in summary if row["rc"] != 0]
    print(f"TOTAL: {len(summary) - len(failed)} passed / {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
