"""M1 验收项 11：工具链门槛 + M0 回归（配置校验 / 密钥脱敏 / CLI 语义）。

- `ruff check .` 零告警
- `pytest` 全绿（记录真实数字）
- 重跑 M0 的 6 个独立脚本（重跑前先快照旧证据，保留历史）
- `config.example.yaml` 开箱校验回归
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    EVIDENCE,
    PROJECT,
    PYTHON,
    ROUND1,
    Suite,
    run_cli,
)

SNAPSHOT = ROUND1 / "regression" / "pre-m1-snapshot"
LIVE_EVIDENCE = PROJECT / "reports" / "evidence"

M0_SCRIPTS = [
    "m0_blackbox.py",
    "m0_inprocess.py",
    "m0_secret_scan.py",
    "m0_yaml_error_probe.py",
    "m0_issue_redaction_probe.py",
    "m0_round3_probe.py",
]
SNAPSHOT_DIRS = ["cli", "logging", "secret-scan", "regression"]


def main() -> int:
    suite = Suite("regression")
    workspace = EVIDENCE
    workspace.mkdir(parents=True, exist_ok=True)
    _snapshot_old_evidence(workspace)
    _ruff(suite, workspace)
    _pytest(suite, workspace)
    _m0_scripts(suite, workspace)
    _example_config(suite)
    return suite.finish()


def _snapshot_old_evidence(workspace: Path) -> None:
    """快照 M1 前旧证据（已存在则不覆盖，避免二次重跑把快照也覆盖掉）。"""
    target = workspace / "pre-m1-snapshot"
    target.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT_DIRS:
        destination = target / name
        if destination.exists():
            continue
        source = PROJECT / "reports" / "evidence" / name
        if source.exists():
            shutil.copytree(source, destination)
    print(f"  [NOTE] 旧证据快照: {target}（已存在则跳过）")


def _run(command: list[str], *, timeout: float = 900.0) -> tuple[int, str, float]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    start = time.perf_counter()
    proc = subprocess.run(
        command, cwd=str(PROJECT), capture_output=True, timeout=timeout, env=env
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return proc.returncode, output, time.perf_counter() - start


def _ruff(suite: Suite, workspace: Path) -> None:
    rc, output, seconds = _run([str(PYTHON), "-m", "ruff", "check", "."])
    (workspace / "ruff.txt").write_text(output, encoding="utf-8")
    suite.check(
        "11a-ruff零告警",
        rc == 0,
        f"exit={rc}（{seconds:.1f}s）输出={output.strip()[:80]!r}",
    )


def _pytest(suite: Suite, workspace: Path) -> None:
    rc, output, seconds = _run([str(PYTHON), "-m", "pytest", "--color=no", "-v"])
    (workspace / "pytest.txt").write_text(output, encoding="utf-8")
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    errors = re.search(r"(\d+) error", output)
    numbers = {
        "passed": int(passed.group(1)) if passed else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "seconds": round(seconds, 1),
    }
    (workspace / "pytest-numbers.json").write_text(
        json.dumps(numbers, ensure_ascii=False), encoding="utf-8"
    )
    tail = "\n".join(output.strip().splitlines()[-3:])
    suite.check(
        "11b-pytest全绿",
        rc == 0 and numbers["failed"] == 0 and numbers["errors"] == 0 and numbers["passed"] > 0,
        f"exit={rc} passed={numbers['passed']} failed={numbers['failed']} "
        f"errors={numbers['errors']}（{seconds:.1f}s）",
    )
    suite.evidence("pytest-tail.txt", tail)


def _m0_scripts(suite: Suite, workspace: Path) -> None:
    snapshot = workspace / "pre-m1-snapshot"
    for script in M0_SCRIPTS:
        path = PROJECT / "tests" / "independent" / script
        rc, output, seconds = _run([str(PYTHON), str(path)])
        (workspace / f"{script}.txt").write_text(output, encoding="utf-8")
        summary = re.search(r"== SUMMARY: (\d+)/(\d+) passed ==", output)
        baseline = _baseline_stats(script, snapshot)
        current = _current_stats(script, workspace)
        new_failures = sorted(current["failed"] - baseline["failed"])
        fixed = sorted(baseline["failed"] - current["failed"])
        ok = bool(summary) and current["total"] == baseline["total"] and not new_failures
        detail = (
            f"exit={rc} SUMMARY="
            f"{summary.group(1) + '/' + summary.group(2) if summary else '?'}"
            f"（M0 基线 {baseline['passed']}/{baseline['total']}，{seconds:.1f}s）"
        )
        if new_failures:
            detail += f" 新增失败={new_failures}"
        if baseline["failed"]:
            detail += f" M0 已接受遗留={sorted(baseline['failed'])}"
        if fixed:
            detail += f" 本轮已恢复={fixed}"
        suite.check(f"11c-{script}", ok, detail)


def _baseline_stats(script: str, snapshot: Path) -> dict[str, set[str] | int]:
    """M0 第 3 轮基线。

    优先使用 M0 保存的权威副本（`reports/evidence/regression/*_r3.json`），
    其次退回 M1 前的证据快照；yaml 探针不产 JSON，用报告文档化基线。
    """
    spec = _R3_SPECS.get(script) or _JSON_SPECS.get(script)
    if spec is None:
        # m0_yaml_error_probe.py：M0 报告 5/6，唯一失败为已接受 P2（bad_alias）
        return {"passed": 5, "total": 6, "failed": {"bad_alias"}}
    relative, key = spec
    candidate = SNAPSHOT / relative
    if not candidate.exists():
        candidate = snapshot / relative
    return _summary_stats(candidate, key)


def _current_stats(script: str, workspace: Path) -> dict[str, set[str] | int]:
    """本轮运行后的实际失败项（M0 脚本写入 reports/evidence/<模块>/ 下）。"""
    if script == "m0_yaml_error_probe.py":
        return _yaml_probe_current(workspace / f"{script}.txt")
    relative, key = _JSON_SPECS[script]
    return _summary_stats(LIVE_EVIDENCE / relative, key)


def _summary_stats(path: Path, key: str) -> dict[str, set[str] | int]:
    if not path.exists():
        return {"passed": -1, "total": -1, "failed": {"<证据缺失>"}}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else []
    failed = {str(item.get(key) or item.get("name") or "?") for item in items if not item.get("ok")}
    return {"passed": len(items) - len(failed), "total": len(items), "failed": failed}


def _yaml_probe_current(path: Path) -> dict[str, set[str] | int]:
    if not path.exists():
        return {"passed": -1, "total": -1, "failed": {"<证据缺失>"}}
    text = path.read_text(encoding="utf-8")
    summary = re.search(r"== SUMMARY: (\d+)/(\d+) passed ==", text)
    failed: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"\[FAIL\] (\S+):", line.strip())
        if match:
            failed.add(match.group(1))
    return {
        "passed": int(summary.group(1)) if summary else -1,
        "total": int(summary.group(2)) if summary else -1,
        "failed": failed,
    }


# 脚本 → (证据文件相对路径, 用例名字段)；本轮运行产物位于 reports/evidence/<模块>/
_JSON_SPECS: dict[str, tuple[str, str]] = {
    "m0_blackbox.py": ("cli/_summary.json", "name"),
    "m0_inprocess.py": ("logging/_summary.json", "name"),
    "m0_secret_scan.py": ("secret-scan/_summary.json", "name"),
    "m0_round3_probe.py": ("regression/round3/_summary.json", "name"),
    "m0_issue_redaction_probe.py": ("regression/issue-redaction-probe.json", "case"),
}

# M0 第 3 轮保存的权威基线副本（未被本轮重跑覆盖）
_R3_SPECS: dict[str, tuple[str, str]] = {
    "m0_blackbox.py": ("regression/cli_summary_r3.json", "name"),
    "m0_inprocess.py": ("regression/logging_summary_r3.json", "name"),
    "m0_secret_scan.py": ("regression/secret_scan_summary_r3.json", "name"),
    "m0_round3_probe.py": ("regression/round3_summary.json", "name"),
}


def _example_config(suite: Suite) -> None:
    example = PROJECT / "config.example.yaml"
    result = run_cli(["check", "--config", str(example)], timeout=60)
    suite.check(
        "11d-config.example开箱校验",
        result.rc == 0,
        f"rc={result.rc}（期望 0）",
        evidence=result.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
