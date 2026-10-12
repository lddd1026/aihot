"""M7 独立验收 · 声明 7：回归（pytest 406 用例 / ruff / m5r / m6r 套件）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m7r_regression.py
证据: reports/evidence/m7r/m7r_regression/ 与 reports/evidence/m7r/regression/
说明:
- m5r/m6r 套件通过各自的 run_all 脚本执行（证据重定向到 m7r/regression/<name>/）；
  若该目录已有 run-all-summary.json 则默认复用（设 AIHOT_M7R_REGRESSION_RERUN_SUITES=1 强制重跑）。
- m5r 的 m4_alert 回归会直连真实 qyapi 域名，按任务约束（只连 localhost）用
  AIHOT_M5R_SKIP_M4_ALERT=1 跳过，并在报告中说明。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m7r_support as m7  # noqa: E402

PROJECT = m3.PROJECT
PYTHON = m3.PYTHON
REGRESSION = m7.EVIDENCE / "regression"
PYTEST_PASSED = re.compile(r"(\d+) passed")
PYTEST_FAILED = re.compile(r"(\d+) failed")


def run_cmd(
    args: list[str], *, timeout: float, env_extra: dict[str, str] | None = None
) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            args, cwd=str(PROJECT), capture_output=True, timeout=timeout, env=env
        )
        rc: int | str = proc.returncode
        out = proc.stdout.decode("utf-8", "replace") + "\n--- stderr ---\n" + proc.stderr.decode(
            "utf-8", "replace"
        )
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out = (exc.stdout or b"").decode("utf-8", "replace") + "\n[harness] 超时"
    return {
        "cmd": " ".join(args),
        "rc": rc,
        "seconds": round(time.perf_counter() - start, 1),
        "output": out,
    }


def collect_suite_checks(evidence_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not evidence_root.exists():
        return rows
    for path in sorted(evidence_root.glob("*.json")):
        if path.name.startswith("run-"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(payload, dict) and "checks" in payload:
            rows.append(
                {
                    "suite": payload.get("suite") or path.stem,
                    "passed": payload.get("passed"),
                    "failed": payload.get("failed"),
                    "file": path.name,
                }
            )
    return rows


def external_suites(suite: m3.Suite, name: str, script: str, env_extra: dict[str, str]) -> None:
    summary_path = REGRESSION / name / "run-all-summary.json"
    force = os.environ.get("AIHOT_M7R_REGRESSION_RERUN_SUITES") == "1"
    result: dict[str, Any] | None = None
    if not summary_path.exists() or force:
        REGRESSION.mkdir(parents=True, exist_ok=True)
        result = run_cmd(
            [str(PYTHON), str(PROJECT / "tests" / "independent" / script)],
            timeout=3600,
            env_extra=env_extra,
        )
        (REGRESSION / f"{name}-stdout.txt").write_text(result["output"], encoding="utf-8")
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else []
    suites = collect_suite_checks(REGRESSION / name)
    total_pass = sum(int(row["passed"] or 0) for row in suites)
    total_fail = sum(int(row["failed"] or 0) for row in suites)
    detail = (
        f"套件 {len(summary)} 个；rc={[row.get('rc') for row in summary]}；"
        f"检查项 {total_pass} passed / {total_fail} failed"
    )
    if result is not None:
        detail += f"；本轮重跑耗时 {result['seconds']}s（rc={result['rc']}）"
    else:
        detail += "；复用既有 summary（未重跑）"
    index = "7.4" if name == "m5r" else "7.5"
    suite.check(
        f"{index} {name} 套件回归全部 rc=0",
        bool(summary)
        and all(row.get("rc") == 0 for row in summary)
        and total_fail == 0,
        detail,
    )
    suite.evidence_json(f"{name}-suites.json", suites)
    suite.evidence(f"{name}-summary.json", json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    suite = m3.Suite("m7r_regression")
    before = m3.real_state_snapshot()

    # 注意：不再额外传 -q（pyproject addopts 已含 -q），保证末尾能解析出 "N passed"
    pytest_result = run_cmd(
        [str(PYTHON), "-m", "pytest"], timeout=1800
    )
    suite.evidence("pytest.log", pytest_result["output"])
    passed = PYTEST_PASSED.search(pytest_result["output"])
    failed = PYTEST_FAILED.search(pytest_result["output"])
    suite.check(
        "7.1 pytest 全绿（rc=0，无 failed）",
        pytest_result["rc"] == 0 and failed is None and passed is not None,
        f"rc={pytest_result['rc']}；passed={passed.group(1) if passed else '?'}"
        f"；failed={failed.group(1) if failed else '0'}；{pytest_result['seconds']}s",
    )
    suite.check(
        "7.2 pytest 用例数与任务书一致（P3 修复后 408）",
        passed is not None and int(passed.group(1)) == 408,
        f"实际 passed={passed.group(1) if passed else '?'}（P3 复验声明 408）",
    )

    ruff_result = run_cmd([str(PYTHON), "-m", "ruff", "check", "."], timeout=600)
    suite.evidence("ruff.log", ruff_result["output"])
    suite.check(
        "7.3 ruff check . 零告警",
        ruff_result["rc"] == 0 and "All checks passed!" in ruff_result["output"],
        f"rc={ruff_result['rc']}；输出={ruff_result['output'].strip().splitlines()[0][:80]}",
    )

    if os.environ.get("AIHOT_M5R_SKIP_M4_ALERT") != "1":
        suite.note("未设置 AIHOT_M5R_SKIP_M4_ALERT=1：m5r 会执行 m4_alert 回归（直连真实域名）")
    external_suites(
        suite,
        "m5r",
        "m5r_run_all.py",
        {"AIHOT_M5R_EVIDENCE": "m7r/regression/m5r", "AIHOT_M5R_SKIP_M4_ALERT": "1"},
    )
    external_suites(
        suite,
        "m6r",
        "m6r_run_all.py",
        {"AIHOT_M6R_EVIDENCE": "m7r/regression/m6r"},
    )

    after = m3.real_state_snapshot()
    suite.check(
        "7.6 真实库 / 真实日报存档未被本轮全部测试改动",
        before == after,
        f"db 条目={after.get('db', {}).get('items')} digests={len(after.get('digests', {}))}",
    )
    suite.note(
        "适配说明：既有 m5r/m6r 夹具未使用「行业动态」分类造日报（该分类本轮起不可发布），"
        "套件零改动；m5r 的 m4_alert 回归按 localhost-only 约束跳过。"
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
