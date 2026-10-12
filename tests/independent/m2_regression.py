"""M2 验收 9：工具链与回归（ruff / pytest / M0+M1 独立脚本 / 密钥脱敏 / 真实库只读）。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_support import (  # noqa: E402
    EVIDENCE,
    PROJECT,
    PYTHON,
    REAL_CONFIG,
    Suite,
    real_db_snapshot,
)

M1_EVIDENCE = PROJECT / "reports" / "evidence" / "m1" / "regression2"
M0_LIVE = PROJECT / "reports" / "evidence"
SNAPSHOT_DIRS = ["cli", "logging", "secret-scan", "regression"]
OUT = EVIDENCE / "regression"

M0_SCRIPTS = [
    "m0_blackbox.py", "m0_inprocess.py", "m0_secret_scan.py",
    "m0_yaml_error_probe.py", "m0_issue_redaction_probe.py", "m0_round3_probe.py",
]
M1_SCRIPTS = [
    "m1_acceptance.py", "m1_adversarial.py", "m1_url_edges.py", "m1_net_store_units.py",
    "m1_contracts.py", "m1_check_net.py", "m1_proxy_bad_port_repro.py", "m1_socks_probe.py",
    "m1_slow_source.py", "m1_perf.py",
]
M1_INFO_SCRIPTS = ["m1_real_db_state.py", "m1_recon.py", "m1_revision.py"]

# M0 第 3 轮权威基线（reports/evidence/regression/*_r3.json）
_R3_SPECS: dict[str, tuple[str, str]] = {
    "m0_blackbox.py": ("regression/cli_summary_r3.json", "name"),
    "m0_inprocess.py": ("regression/logging_summary_r3.json", "name"),
    "m0_secret_scan.py": ("regression/secret_scan_summary_r3.json", "name"),
    "m0_round3_probe.py": ("regression/round3_summary.json", "name"),
}
_JSON_SPECS: dict[str, tuple[str, str]] = {
    "m0_blackbox.py": ("cli/_summary.json", "name"),
    "m0_inprocess.py": ("logging/_summary.json", "name"),
    "m0_secret_scan.py": ("secret-scan/_summary.json", "name"),
    "m0_round3_probe.py": ("regression/round3/_summary.json", "name"),
    "m0_issue_redaction_probe.py": ("regression/issue-redaction-probe.json", "case"),
}


def run(command: list[str], *, timeout: float = 900.0, env_extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    start = time.perf_counter()
    proc = subprocess.run(command, cwd=str(PROJECT), capture_output=True, timeout=timeout, env=env)
    return {
        "rc": proc.returncode,
        "output": (proc.stdout + proc.stderr).decode("utf-8", "replace"),
        "seconds": round(time.perf_counter() - start, 1),
    }


def snapshot(suite: Suite) -> None:
    target = EVIDENCE / "regression" / "pre-m2-snapshot"
    target.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT_DIRS:
        destination = target / name
        if destination.exists():
            continue
        source = M0_LIVE / name
        if source.exists():
            shutil.copytree(source, destination)
    suite.note(f"M0 证据快照: {target}（已存在则跳过；M0 脚本本轮会覆盖 live 目录）")


def m0_baseline(script: str) -> dict:
    spec = _R3_SPECS.get(script) or _JSON_SPECS.get(script)
    if spec is None:
        return {"passed": 5, "total": 6, "failed": {"bad_alias"}}
    relative, key = spec
    candidate = M1_EVIDENCE / relative
    if not candidate.exists():
        candidate = M1_EVIDENCE / "pre-m1-snapshot" / relative
    return summary_stats(candidate, key)


def summary_stats(path: Path, key: str) -> dict:
    if not path.exists():
        return {"passed": -1, "total": -1, "failed": {"<证据缺失>"}}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else []
    failed = {str(item.get(key) or item.get("name") or "?") for item in items if not item.get("ok")}
    return {"passed": len(items) - len(failed), "total": len(items), "failed": failed}


def current_stats(script: str) -> dict:
    if script == "m0_yaml_error_probe.py":
        text = (OUT / f"{script}.txt")
        if not text.exists():
            return {"passed": -1, "total": -1, "failed": {"<证据缺失>"}}
        content = text.read_text(encoding="utf-8")
        summary = re.search(r"== SUMMARY: (\d+)/(\d+) passed ==", content)
        failed = {
            match.group(1)
            for line in content.splitlines()
            if (match := re.match(r"\[FAIL\] (\S+):", line.strip()))
        }
        return {
            "passed": int(summary.group(1)) if summary else -1,
            "total": int(summary.group(2)) if summary else -1,
            "failed": failed,
        }
    relative, key = _JSON_SPECS[script]
    return summary_stats(M0_LIVE / relative, key)


def m1_baselines() -> dict[str, dict]:
    path = M1_EVIDENCE / "run-all-summary.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in data.get("results", []):
        passed = str(row.get("passed", "?"))
        match = re.match(r"(\d+)/(\d+)", passed)
        if match:
            out[row["script"]] = {
                "passed": int(match.group(1)),
                "total": int(match.group(2)),
                "verdict": row.get("verdict"),
            }
    return out


def main() -> int:
    suite = Suite("regression")
    OUT.mkdir(parents=True, exist_ok=True)
    snapshot(suite)

    # ---------- ruff ----------
    ruff = run([str(PYTHON), "-m", "ruff", "check", "."])
    (OUT / "ruff.txt").write_text(ruff["output"], encoding="utf-8")
    suite.check(
        "9a-ruff check . 零告警",
        ruff["rc"] == 0,
        f"exit={ruff['rc']}（{ruff['seconds']}s）{ruff['output'].strip()[:60]}",
    )

    # ---------- pytest ----------
    pytest_run = run([str(PYTHON), "-m", "pytest", "--color=no", "-v"])
    (OUT / "pytest.txt").write_text(pytest_run["output"], encoding="utf-8")
    passed = re.search(r"(\d+) passed", pytest_run["output"])
    failed = re.search(r"(\d+) failed", pytest_run["output"])
    errors = re.search(r"(\d+) error", pytest_run["output"])
    numbers = {
        "passed": int(passed.group(1)) if passed else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "seconds": pytest_run["seconds"],
        "rc": pytest_run["rc"],
    }
    (OUT / "pytest-numbers.json").write_text(
        json.dumps(numbers, ensure_ascii=False), encoding="utf-8"
    )
    suite.check(
        "9b-pytest 全绿",
        pytest_run["rc"] == 0 and numbers["failed"] == 0 and numbers["errors"] == 0
        and numbers["passed"] > 0,
        f"passed={numbers['passed']} failed={numbers['failed']} errors={numbers['errors']}"
        f"（{numbers['seconds']}s）",
    )

    # ---------- M0 脚本 ----------
    for script in M0_SCRIPTS:
        result = run([str(PYTHON), str(PROJECT / "tests" / "independent" / script)])
        (OUT / f"{script}.txt").write_text(result["output"], encoding="utf-8")
        baseline = m0_baseline(script)
        current = current_stats(script)
        new_failures = sorted(current["failed"] - baseline["failed"])
        ok = (
            current["total"] == baseline["total"]
            and not new_failures
            and current["passed"] >= baseline["passed"]
        )
        detail = (
            f"exit={result['rc']} passed={current['passed']}/{current['total']}"
            f"（M0 基线 {baseline['passed']}/{baseline['total']}，{result['seconds']}s）"
        )
        if new_failures:
            detail += f" 新增失败={new_failures}"
        if baseline["failed"]:
            detail += f" 已接受遗留={sorted(baseline['failed'])}"
        suite.check(f"9c-{script}", ok, detail)

    # ---------- M1 脚本 ----------
    baselines = m1_baselines()
    env = {"AIHOT_M1_EVIDENCE": "m2/regression/m1"}
    for script in M1_SCRIPTS:
        result = run([str(PYTHON), str(PROJECT / "tests" / "independent" / script)], env_extra=env)
        (OUT / f"{script}.txt").write_text(result["output"], encoding="utf-8")
        match = re.search(r"== (\S+): (\d+) passed / (\d+) failed ==", result["output"])
        current_passed = int(match.group(2)) if match else -1
        current_failed = int(match.group(3)) if match else -1
        base = baselines.get(script, {})
        total_current = current_passed + current_failed if match else -1
        ok = (
            match is not None
            and current_failed == 0
            and (not base or current_passed >= base.get("passed", 0))
        )
        detail = (
            f"exit={result['rc']} {current_passed}/{total_current}"
            f"（M1 基线 {base.get('passed', '?')}/{base.get('total', '?')}，{result['seconds']}s）"
        )
        if match is None:
            detail += f" 输出尾：{result['output'].strip()[-120:]}"
        suite.check(f"9d-{script}", ok, detail)

    for script in M1_INFO_SCRIPTS:
        result = run([str(PYTHON), str(PROJECT / "tests" / "independent" / script)], timeout=300)
        (OUT / f"{script}.txt").write_text(result["output"], encoding="utf-8")
        suite.check(
            f"9e-{script}（信息脚本）",
            result["rc"] == 0,
            f"exit={result['rc']}（{result['seconds']}s）",
        )

    # ---------- config.example 开箱 ----------
    result = run(
        [str(PYTHON), "-m", "aihot", "check", "--config", str(PROJECT / "config.example.yaml")],
        timeout=120,
    )
    (OUT / "example-config.txt").write_text(result["output"], encoding="utf-8")
    suite.check(
        "9f-config.example.yaml 开箱校验",
        result["rc"] == 0,
        f"rc={result['rc']}（期望 0）",
    )

    # ---------- 密钥脱敏不回退 ----------
    secrets = collect_real_secrets()
    leaked = scan_paths(secrets, [PROJECT / "reports" / "evidence" / "m2",
                                  PROJECT / "tests" / "independent"])
    suite.check(
        "9g-证据/测试脚本无真实密钥",
        not leaked,
        f"扫描 {len(secrets)} 个真实密钥模式，命中文件={leaked or '无'}",
    )
    cli_outputs = scan_paths(secrets, [OUT / "ruff.txt", OUT / "pytest.txt"])
    suite.check("9h-ruff/pytest 输出无真实密钥", not cli_outputs, f"命中={cli_outputs or '无'}")

    # ---------- 真实库只读 ----------
    snapshot_after = real_db_snapshot()
    (OUT / "real-db-after.json").write_text(
        json.dumps(snapshot_after, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    suite.check(
        "9z-真实库未被测试改动",
        snapshot_after["items"] == REAL_DB_BASELINE["items"]
        and snapshot_after["runs"] == REAL_DB_BASELINE["runs"]
        and snapshot_after["clusters"] == REAL_DB_BASELINE["clusters"]
        and snapshot_after["max_id"] == REAL_DB_BASELINE["max_id"],
        f"items={snapshot_after['items']} runs={snapshot_after['runs']} "
        f"clusters={snapshot_after['clusters']} max_id={snapshot_after['max_id']}"
        f"（开始时一致：{snapshot_after['items'] == REAL_DB_BASELINE['items']}）",
    )
    suite.evidence_json("9z-real-db-baseline.json", REAL_DB_BASELINE)
    return suite.finish()


def collect_real_secrets() -> list[str]:
    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    candidates = [
        (raw.get("llm") or {}).get("api_key"),
        ((raw.get("push") or {}).get("wecom") or {}).get("webhook"),
        ((raw.get("push") or {}).get("email") or {}).get("password"),
    ]
    return [str(value) for value in candidates if value and len(str(value)) >= 8]


def scan_paths(secrets: list[str], targets: list[Path]) -> list[str]:
    hits: list[str] = []
    for target in targets:
        if not target.exists():
            continue
        files = [target] if target.is_file() else [
            path for path in target.rglob("*") if path.is_file() and path.suffix in
            {".py", ".txt", ".json", ".md", ".yaml", ".log"}
        ]
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(secret in text for secret in secrets):
                hits.append(str(path.relative_to(PROJECT)))
    return hits


REAL_DB_BASELINE = real_db_snapshot()

if __name__ == "__main__":
    raise SystemExit(main())
