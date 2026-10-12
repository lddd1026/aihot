"""M5 修复独立验收 · 一键运行器（顺序执行各套件并汇总）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_run_all.py
产出: reports/evidence/m5r/run-<suite>.log + run-all-summary.json
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
EVIDENCE_ROOT = os.environ.get("AIHOT_M5R_EVIDENCE", "m5r")
EVIDENCE = PROJECT / "reports" / "evidence" / EVIDENCE_ROOT

SUITES: list[tuple[str, str, str | None]] = [
    ("m5r_preflight", "m5r_preflight.py", None),
    ("m5r_fetch", "m5r_fetch.py", None),
    ("m5r_skip", "m5r_skip.py", None),
    ("m5r_alert", "m5r_alert.py", None),
    ("m5r_report", "m5r_report.py", None),
    ("m5r_render_alert", "m5r_render_alert.py", None),
    ("m5r_bat_local", "m5r_bat_local.py", None),
    ("m5r_round2_proxy", "m5r_round2_proxy.py", None),
    ("m4_alert_regression", "m4_alert.py", f"{EVIDENCE_ROOT}/regression"),
]


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    suites = list(SUITES)
    # 既有 m4_alert.py 的告警截获依赖「代理引流量」，P1 修复后会把告警直连真实
    # qyapi.weixin.qq.com（假 key 被拒），与"只连 localhost"冲突；默认仍执行一次，
    # 需要严格本地运行时用 AIHOT_M5R_SKIP_M4_ALERT=1 跳过。
    if os.environ.get("AIHOT_M5R_SKIP_M4_ALERT") == "1":
        suites = [item for item in suites if item[0] != "m4_alert_regression"]
        print("[NOTE] 按 AIHOT_M5R_SKIP_M4_ALERT=1 跳过 m4_alert 回归（会直连真实域名）")
    summary: list[dict[str, object]] = []
    for name, script, evidence_root in suites:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if evidence_root:
            env["AIHOT_M4_EVIDENCE"] = evidence_root
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [str(PYTHON), str(HERE / script)],
                cwd=str(PROJECT),
                capture_output=True,
                env=env,
                timeout=3600,
            )
            rc: int | str = proc.returncode
            out = proc.stdout + b"\n--- stderr ---\n" + proc.stderr
        except subprocess.TimeoutExpired as exc:
            rc = "timeout"
            out = (exc.stdout or b"") + b"\n[harness] timeout"
        text = out.decode("utf-8", "replace")
        (EVIDENCE / f"run-{name}.log").write_text(text, encoding="utf-8")
        record = {
            "suite": name,
            "rc": rc,
            "seconds": round(time.perf_counter() - start, 1),
        }
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
