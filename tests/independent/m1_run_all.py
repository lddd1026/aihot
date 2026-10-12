"""第 2 轮回归：批量执行全部独立脚本，汇总到 reports/evidence/m1/regression2/。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
OUT = PROJECT / "reports" / "evidence" / "m1" / "regression2"

M1_SCRIPTS = [
    "m1_revision.py",
    "m1_real_db_state.py",
    "m1_recon.py",
    "m1_acceptance.py",
    "m1_adversarial.py",
    "m1_url_edges.py",
    "m1_net_store_units.py",
    "m1_contracts.py",
    "m1_check_net.py",
    "m1_proxy_bad_port_repro.py",
    "m1_socks_probe.py",
    "m1_slow_source.py",
    "m1_perf.py",
    "m1_regression.py",
]
M0_SCRIPTS = [
    "m0_blackbox.py",
    "m0_inprocess.py",
    "m0_secret_scan.py",
    "m0_yaml_error_probe.py",
    "m0_issue_redaction_probe.py",
    "m0_round3_probe.py",
]


def run(script: str, directory: Path, env: dict[str, str]) -> dict[str, object]:
    path = directory / script
    start = time.perf_counter()
    proc = subprocess.run(
        [str(PYTHON), str(path)],
        cwd=str(PROJECT),
        capture_output=True,
        timeout=1800,
        env=env,
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    seconds = time.perf_counter() - start
    if script.startswith("m1_"):
        match = re.search(r"== (\S+): (\d+) passed / (\d+) failed ==", output)
        passed = f"{match.group(2)}/{int(match.group(2)) + int(match.group(3))}" if match else "?"
        verdict = (
            "PASS" if match and match.group(3) == "0" else ("OBSERVE" if match is None else "FAIL")
        )
    else:
        match = re.search(r"== SUMMARY: (\d+)/(\d+) passed ==", output)
        passed = f"{match.group(1)}/{match.group(2)}" if match else "?"
        verdict = (
            "PASS"
            if match and match.group(1) == match.group(2)
            else ("?" if match is None else "CHECK")
        )
    return {
        "script": script,
        "exit": proc.returncode,
        "passed": passed,
        "verdict": verdict,
        "seconds": round(seconds, 1),
        "tail": "\n".join(output.strip().splitlines()[-6:]),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["AIHOT_M1_EVIDENCE"] = "m1/regression2"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    results: list[dict[str, object]] = []
    for script in M1_SCRIPTS + M0_SCRIPTS:
        row = run(script, PROJECT / "tests" / "independent", env)
        results.append(row)
        print(
            f"  {row['verdict']:<8} {script:<28} exit={row['exit']} "
            f"{row['passed']} ({row['seconds']}s)"
        )

    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}
    (OUT / "run-all-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"{r['verdict']:<8} {r['script']:<28} exit={r['exit']} {r['passed']} ({r['seconds']}s)"
        for r in results
    ]
    (OUT / "run-all-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = [r for r in results if r["verdict"] == "FAIL"]
    print(f"\n总计 {len(results)} 个脚本，FAIL {len(failed)} 个")
    print(f"摘要 → {OUT / 'run-all-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
