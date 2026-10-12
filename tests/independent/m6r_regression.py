"""M6 声明 6 独立验收：回归（pytest 全绿 / ruff 零告警 / 上一轮 m5r_* 套件适配后仍通过）。

内容：
- `pytest -q`（项目根）：rc=0 且用例数 ≥ 390（记录精确数字）；
- `ruff check .`：rc=0 且无输出；
- 上一轮 m5r_* 套件（已适配本轮健康检查，见 m5r_fetch.py 头部说明）：
  调 `m5r_run_all.py`（AIHOT_M5R_SKIP_M4_ALERT=1、证据写 m5r/round3）→ 全部 rc=0；
- 单跑一次适配后的 `m5r_fetch.py`（关键改动点）留证据。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_regression.py [--no-m5r]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

PROJECT = m6.PROJECT
PYTHON = m6.PYTHON
PYTEST_COUNT = re.compile(r"(\d+) passed")
PYTEST_FAILED = re.compile(r"(\d+) (?:failed|error)")


def run(
    args: list[str], *, timeout: float = 1800.0, env_extra: dict[str, str] | None = None
) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            args, cwd=str(PROJECT), capture_output=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired as exc:
        return -999, (exc.stdout or b"").decode("utf-8", "replace") + f"\n[harness] 超时 {timeout}s"
    return proc.returncode, proc.stdout.decode("utf-8", "replace") + "\n" + proc.stderr.decode(
        "utf-8", "replace"
    )


def main() -> int:
    with_m5r = "--no-m5r" not in sys.argv
    suite = m3.Suite("m6r_regression")
    before = m3.real_state_snapshot()
    m6.clean_proxy_env()

    # ---------- 6.1 pytest ----------
    # 注意：pyproject 已配置 addopts="-q"，再传 -q 会变成 -qq 而**抑制汇总行**，
    # 因此这里不再额外传 -q，保证能解析出 "N passed"。
    rc, out = run([str(PYTHON), "-m", "pytest"])
    suite.evidence("1-pytest.txt", f"rc={rc}\n{out}")
    passed = int(found.group(1)) if (found := PYTEST_COUNT.search(out)) else -1
    failed = int(found.group(1)) if (found := PYTEST_FAILED.search(out)) else 0
    suite.check(
        "6.1 pytest 全绿（rc=0，0 失败，用例数 ≥390）",
        rc == 0 and failed == 0 and passed >= 390,
        f"rc={rc}；passed={passed}；failed={failed}",
    )

    # ---------- 6.2 ruff ----------
    rc_ruff, out_ruff = run([str(PYTHON), "-m", "ruff", "check", "."], timeout=600)
    suite.evidence("2-ruff.txt", f"rc={rc_ruff}\n{out_ruff}")
    suite.check(
        "6.2 ruff check . 零告警（rc=0 且无输出）",
        rc_ruff == 0 and not out_ruff.strip().replace("All checks passed!", "").strip(),
        f"rc={rc_ruff}；输出={out_ruff.strip()[:200] or '（空）'}",
    )

    # ---------- 6.3 适配后的 m5r_fetch（关键改动点）单跑 ----------
    if with_m5r:
        rc_fetch, out_fetch = run(
            [str(PYTHON), str(m6.PROJECT / "tests/independent/m5r_fetch.py")],
            timeout=900,
            # 证据写 m5r/round3，避免覆盖 M5 轮的历史证据（reports/evidence/m5r/*）
            env_extra={"AIHOT_M5R_EVIDENCE": "m5r/round3"},
        )
        suite.evidence("3-m5r_fetch.txt", f"rc={rc_fetch}\n{out_fetch}")
        tail = out_fetch.strip().splitlines()[-1][:160] if out_fetch.strip() else ""
        suite.check(
            "6.3 适配后的 m5r_fetch 通过（代理健康检查语义已更新，无旧断言残留）",
            rc_fetch == 0,
            f"rc={rc_fetch}；尾部={tail}",
        )

        # ---------- 6.4 全量 m5r 套件（跳过会连真实域名的 m4_alert 回归） ----------
        rc_all, out_all = run(
            [str(PYTHON), str(m6.PROJECT / "tests/independent/m5r_run_all.py")],
            timeout=2400,
            env_extra={"AIHOT_M5R_SKIP_M4_ALERT": "1", "AIHOT_M5R_EVIDENCE": "m5r/round3"},
        )
        suite.evidence("4-m5r-run-all.txt", f"rc={rc_all}\n{out_all}")
        summary_path = PROJECT / "reports/evidence/m5r/round3/run-all-summary.json"
        summary: list[dict] = []
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        suite.check(
            "6.4 m5r 全量套件全部 rc=0（含 preflight/fetch/round2 等 8 套件）",
            rc_all == 0 and bool(summary) and all(row["rc"] == 0 for row in summary),
            f"rc={rc_all}；套件结果={[(row['suite'], row['rc']) for row in summary]}",
        )
        suite.evidence(
            "4b-m5r-run-all-summary.json", json.dumps(summary, ensure_ascii=False, indent=2)
        )

    suite.check(
        "6.5 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
