"""M3 全量独立测试总入口：M3 全部套件 + M0/M1/M2 回归脚本，汇总证据。

用法: .venv\\Scripts\\python.exe tests\\independent\\m3_run_all.py
输出: reports/evidence/m3/run-all-summary.json / .txt，各脚本日志 reports/evidence/m3/logs/

已知 P2（不阻断，报告登记）：
- m3_wecom 4.9 极端内容下企微条数 >20（无客户端限流器；真实内容不可达）
- m3_wecom 4.20 图片消息发送失败会阻断同轮文字推送（可 resend 恢复）
- m3_cli 8.3/8.4 `--date` 非法值触发 traceback（rc=1，非 rc=2）
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

from m3_support import EVIDENCE, PROJECT, PYTHON, real_state_snapshot  # noqa: E402

OUT = EVIDENCE

M3_SUITES = [
    "m3_infra_probe.py",
    "m3_compose.py",
    "m3_render_text.py",
    "m3_render_image.py",
    "m3_render_image_edges.py",
    "m3_wecom.py",
    "m3_email.py",
    "m3_e2e.py",
    "m3_degrade.py",
    "m3_idempotent.py",
    "m3_cli.py",
    "m3_toolchain.py",
    "m3_fixes.py",
    "m3_dryrun_db.py",
]
M0_SCRIPTS = [
    "m0_blackbox.py",
    "m0_inprocess.py",
    "m0_secret_scan.py",
    "m0_yaml_error_probe.py",
    "m0_issue_redaction_probe.py",
    "m0_round3_probe.py",
]
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
M2_SCRIPTS = [
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

KNOWN_P2 = {
    "m3_wecom": ["4.9", "4.20"],
    "m3_cli": ["8.3", "8.4"],
}
# 已知 P2（回归侧）：M1 验收检查 6（dry-run 空库文件，见报告 D4）
KNOWN_P2_REGRESSION = {"m1_acceptance.py": "6-dry-run"}
# 已知 P2（契约漂移）：M2 脚本断言基于「enrich 只有逐条加工」的旧契约，
# M3 在 enrich_stage 增加 1 次综述调用后其调用数/成本断言过时（见报告 D5）
KNOWN_DRIFT = {
    "m2_cache.py": ("4a",),
    "m2_cost.py": ("5a", "5b", "5c"),
    "m2r2_d1.py": ("1a",),
    "m2r2_p2.py": ("4c",),
    "m2_regression.py": ("9d",),  # 9d 复跑 m1_acceptance（D4 级联）
}


def suite_failures(script: str) -> list[str]:
    """从套件 JSON 中取失败检查项（用于已知 P2 归类）。"""
    summary_path = OUT / f"{script.removesuffix('.py')}.json"
    if not summary_path.exists():
        return []
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [str(check["item"]) for check in data.get("checks", []) if not check.get("ok")]


def m1_suite_failures(script: str) -> list[str]:
    """读取 M1 套件 JSON（reports/evidence/m1/<suite>.json）中的失败检查项。"""
    suite_name = {
        "m1_acceptance.py": "acceptance",
        "m1_regression.py": "regression",
    }.get(script)
    if not suite_name:
        return []
    path = PROJECT / "reports" / "evidence" / "m1" / f"{suite_name}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [str(check["item"]) for check in data.get("checks", []) if not check.get("ok")]


def m2_suite_failures(script: str) -> list[str]:
    """读取 M2 套件 JSON（reports/evidence/m2/<suite>.json）中的失败检查项。"""
    suite_name = {
        "m2_cache.py": "cache",
        "m2_cost.py": "cost",
        "m2r2_d1.py": "d1",
        "m2r2_p2.py": "p2",
        "m2_regression.py": "regression",
    }.get(script)
    if not suite_name:
        return []
    path = PROJECT / "reports" / "evidence" / "m2" / f"{suite_name}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [str(check["item"]) for check in data.get("checks", []) if not check.get("ok")]


def classify(script: str, output: str, returncode: int) -> tuple[str, str]:
    """返回 (verdict, passed 文本)。"""
    if script.startswith("m3_"):
        match = re.search(r"== (\S+): (\d+) passed / (\d+) failed ==", output)
        if not match:
            return ("?", "?")
        passed, failed = int(match.group(2)), int(match.group(3))
        if failed == 0:
            return ("PASS", f"{passed}/{passed}")
        failures = suite_failures(script)
        known = KNOWN_P2.get(script.removesuffix(".py"), [])
        if known and all(any(item.startswith(prefix) for prefix in known) for item in failures):
            return ("FAIL(P2已知)", f"{passed}/{passed + failed}")
        return ("FAIL", f"{passed}/{passed + failed}")
    if script.startswith("m1_"):
        match = re.search(r"== (\S+): (\d+) passed / (\d+) failed ==", output)
        passed = (
            f"{match.group(2)}/{int(match.group(2)) + int(match.group(3))}" if match else "?"
        )
        if match and match.group(3) == "0":
            return ("PASS", passed)
        known_prefix = KNOWN_P2_REGRESSION.get(script)
        failures = m1_suite_failures(script)
        if known_prefix and failures and all(item.startswith(known_prefix) for item in failures):
            return ("FAIL(P2已知)", passed)
        return ("FAIL" if match else "OBSERVE", passed)
    if script.startswith("m2"):  # m2_*.py / m2r2_*.py / m2r3_*.py
        if script in INFO_SCRIPTS:
            return ("INFO" if returncode == 0 else "ERROR", "-")
        match = re.search(r"== \S+: (\d+) passed / (\d+) failed ==", output)
        if match:
            passed, failed = int(match.group(1)), int(match.group(2))
            if failed == 0:
                return ("PASS", f"{passed}/{passed}")
            failures = m2_suite_failures(script)
            drift = KNOWN_DRIFT.get(script)
            if drift and failures and all(
                any(item.startswith(prefix) for prefix in drift) for item in failures
            ):
                return ("FAIL(P2漂移)", f"{passed}/{passed + failed}")
            return ("FAIL", f"{passed}/{passed + failed}")
        return ("?", "?")
    match = re.search(r"== SUMMARY: (\d+)/(\d+) passed ==", output)
    if match:
        passed, total = match.group(1), match.group(2)
        return ("PASS" if passed == total else "FAIL", f"{passed}/{total}")
    return ("?", "?")


def run(script: str, env: dict[str, str], timeout: int) -> dict:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(PYTHON), str(PROJECT / "tests" / "independent" / script)],
            cwd=str(PROJECT),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", "replace") + f"\n[harness] 超时 {timeout}s"
        returncode = -999
    seconds = round(time.perf_counter() - start, 1)
    verdict, passed = classify(script, output, returncode)
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "logs" / f"{script}.txt").write_text(output, encoding="utf-8")
    return {
        "script": script,
        "exit": returncode,
        "passed": passed,
        "verdict": verdict,
        "seconds": seconds,
        "tail": "\n".join(output.strip().splitlines()[-4:]),
    }


def reclassify() -> int:
    """按当前规则重算既有 run-all-summary.json 的判定（不重跑脚本，仅重读日志）。"""
    summary_path = OUT / "run-all-summary.json"
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    changed = 0
    for row in report["results"]:
        log_path = OUT / "logs" / f"{row['script']}.txt"
        if not log_path.exists():
            continue
        output = log_path.read_text(encoding="utf-8")
        verdict, passed = classify(row["script"], output, row["exit"])
        if verdict != row["verdict"] or passed != row["passed"]:
            print(f"  {row['script']}: {row['verdict']} {row['passed']} → {verdict} {passed}")
            row["verdict"], row["passed"] = verdict, passed
            changed += 1
    report["reclassified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"{row['verdict']:<12} {row['script']:<26} exit={row['exit']:<4} "
        f"{row['passed']:<9} {row['seconds']}s"
        for row in report["results"]
    ]
    (OUT / "run-all-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"重分类完成：{changed} 项判定更新")
    return 0


def main() -> int:
    if "--reclassify" in sys.argv:
        return reclassify()
    before = real_state_snapshot()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["AIHOT_M3_EVIDENCE"] = "m3"
    results: list[dict] = []
    print(f"真实库快照（前）: {json.dumps(before, ensure_ascii=False)}", flush=True)

    groups = [
        ("M3", M3_SUITES, 3600),
        ("M0", M0_SCRIPTS, 1800),
        ("M1", M1_SCRIPTS, 1800),
        ("M2", M2_SCRIPTS, 1800),
        ("INFO", INFO_SCRIPTS, 600),
    ]
    for group, scripts, timeout in groups:
        print(f"\n--- {group} ---", flush=True)
        for script in scripts:
            row = run(script, env, timeout)
            row["group"] = group
            results.append(row)
            print(
                f"  {row['verdict']:<12} {script:<26} exit={row['exit']:<4} "
                f"{row['passed']:<9} {row['seconds']}s",
                flush=True,
            )

    after = real_state_snapshot()
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
        f"{row['verdict']:<12} {row['script']:<26} exit={row['exit']:<4} "
        f"{row['passed']:<9} {row['seconds']}s"
        for row in results
    ]
    (OUT / "run-all-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hard_fail = [row for row in results if row["verdict"] in {"FAIL", "ERROR", "?", "OBSERVE"}]
    print(
        f"\n总脚本 {len(results)}；硬失败 {len(hard_fail)}；"
        f"真实库未变={report['real_db_unchanged']}"
    )
    if hard_fail:
        print("硬失败清单:")
        for row in hard_fail:
            print(f"  - {row['script']}: {row['tail']}")
    print(f"摘要 → {OUT / 'run-all-summary.json'}")
    return 0 if not hard_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
