"""M3 验收项 9：工具链与回归（ruff / pytest / 密钥不泄漏 / 真实库未动）。

- `ruff check .` 必须 exit 0（零告警）
- `pytest` 记录实际通过数（期望 293）
- 密钥扫描：真实 LLM key / 企微 webhook key / 邮箱授权码 不得出现在
  ① M3 证据文件 ② 真实 logs/ 目录 ③ 临时工作区日志 ④ 控制台输出样本
  （含正向对照：扫描器能命中假密钥）
- config.example.yaml 三个密钥字段为空（M0 回归）
- 真实 data/aihot.db 与 data/digests 前后快照一致
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    EVIDENCE,
    PROJECT,
    PYTHON,
    REAL_CONFIG,
    REAL_LOGS,
    TEMPROOT,
    Suite,
    real_state_snapshot,
    run_cli,
    workspace,
    write_config,
)

sys.path.insert(0, str(PROJECT / "src"))
from aihot.config import load_config  # noqa: E402

FAKE_SECRETS = ["sk-fake-000000000000", "fake-auth-code", "fake-key-123456"]


def iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]


def scan_for(files: list[Path], needle: str) -> list[str]:
    """返回命中文件清单（只记录路径，不记录命中的密钥内容）。"""
    hits: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle in text:
            hits.append(str(path.relative_to(PROJECT)) if PROJECT in path.parents else str(path))
    return hits


def main() -> int:
    suite = Suite("m3_toolchain")
    before = real_state_snapshot()

    # ---------- 1. ruff ----------
    import subprocess

    ruff = subprocess.run(
        [str(PYTHON), "-m", "ruff", "check", "."],
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    suite.check(
        "9.1 ruff check . → exit 0（零告警）",
        ruff.returncode == 0,
        f"exit={ruff.returncode} stdout={ruff.stdout.strip()[:120]!r}",
    )
    suite.evidence("9-ruff.txt", f"exit={ruff.returncode}\n{ruff.stdout}\n{ruff.stderr}")

    # ---------- 2. pytest ----------
    pytest_proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", "--tb=short"],  # 配置里已有 -q，重复 -q 会隐藏汇总行
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = "\n".join(pytest_proc.stdout.strip().splitlines()[-3:])
    summary_line = ""
    for line in reversed(pytest_proc.stdout.strip().splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.strip()
            break
    suite.evidence("9-pytest.txt", f"exit={pytest_proc.returncode}\n{tail}\n")
    import re

    match = re.search(r"(\d+) passed", summary_line)
    passed = int(match.group(1)) if match else 0
    failed = re.search(r"(\d+) failed", summary_line)
    errors = re.search(r"(\d+) error", summary_line)
    suite.check(
        "9.2 pytest 全绿（记录实际通过数）",
        pytest_proc.returncode == 0 and passed > 0 and not failed and not errors,
        f"exit={pytest_proc.returncode} {summary_line}",
    )
    suite.check(
        "9.3 pytest 通过数与开发者声明（293）一致",
        passed == 293,
        f"实际 passed={passed} 期望=293",
    )

    # ---------- 3. 密钥扫描 ----------
    cfg = load_config(REAL_CONFIG)
    real_secrets = {
        "llm.api_key": cfg.llm.api_key,
        "wecom.webhook": cfg.push.wecom.webhook,
        "email.password": cfg.push.email.password,
    }
    suite.check(
        "9.4 真实配置三项密钥均已配置（扫描前提成立）",
        all(bool(value) for value in real_secrets.values()),
        f"配置齐全={ {key: bool(value) for key, value in real_secrets.items()} }",
    )

    evidence_files = iter_files(EVIDENCE, (".txt", ".json", ".html", ".md"))
    log_files = iter_files(REAL_LOGS, (".log", ".txt")) + iter_files(TEMPROOT / "work", (".log",))
    console_files = evidence_files

    scan_report: dict[str, object] = {
        "evidence_files": len(evidence_files),
        "log_files": len(log_files),
        "hits": {},
        "positive_control": {},
    }
    hits: dict[str, list[str]] = {}
    for name, value in real_secrets.items():
        hits[name] = scan_for(evidence_files + log_files, value)
    scan_report["hits"] = hits
    suite.check(
        "9.5 真实密钥未出现在 M3 证据/日志中",
        all(not paths for paths in hits.values()),
        f"hits={ {name: len(paths) for name, paths in hits.items()} }",
    )

    # 正向对照：扫描器能发现假密钥
    control_hits = {secret: scan_for(evidence_files, secret) for secret in FAKE_SECRETS}
    scan_report["positive_control"] = {secret: len(paths) for secret, paths in control_hits.items()}
    suite.check(
        "9.6 扫描器正向对照（能命中证据中的假密钥，证明扫描有效）",
        any(control_hits.values()),
        f"control_hits={ {secret: len(paths) for secret, paths in control_hits.items()} }",
    )

    # 邮件授权码专项：CLI 输出样本中不出现
    email_hits = scan_for(console_files, real_secrets["email.password"])
    suite.check(
        "9.7 邮件授权码未出现在任何 M3 证据/控制台样本",
        email_hits == [],
        f"hits={len(email_hits)}",
    )
    suite.evidence_json("9-secret-scan.json", scan_report)

    # ---------- 4. config.example.yaml 密钥字段为空（M0 回归） ----------
    example = (PROJECT / "config.example.yaml").read_text(encoding="utf-8")
    import yaml  # noqa: E402

    example_raw = yaml.safe_load(example)
    example_push = (example_raw or {}).get("push") or {}
    example_llm = (example_raw or {}).get("llm") or {}
    example_email = example_push.get("email") or {}
    example_wecom = example_push.get("wecom") or {}
    suite.check(
        "9.8 config.example.yaml 密钥字段为空（LLM key/webhook/password）",
        not str(example_llm.get("api_key") or "").strip()
        and not str(example_wecom.get("webhook") or "").strip()
        and not str(example_email.get("password") or "").strip(),
        "三个字段均为空",
    )

    # ---------- 5. 真实库 / 存档未改动 ----------
    after = real_state_snapshot()
    suite.check(
        "9.9 真实库与 data/digests 未改动（快照一致）",
        before == after,
        f"db={after['db']} digests={len(after['digests'])} 个文件",
    )

    # ---------- 6. check 命令不泄漏密钥（临时配置，避免真实网络探测） ----------
    root = workspace("toolchain")
    cfg_path = write_config(
        root / "config.yaml",
        data_dir=root / "data",
        log_dir=root / "logs",
        llm_api_key="sk-fake-000000000000",
        wecom_enabled=True,
        wecom_webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake-key-123456",
        email_enabled=True,
        email_overrides={
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "use_ssl": True,
            "username": "sender@example.com",
            "password": "fake-auth-code",
            "from_addr": "sender@example.com",
            "to_addrs": ["receiver@example.com"],
        },
    )
    check = run_cli(["check", "--config", str(cfg_path)], timeout=120)
    suite.check(
        "9.10 check 输出不泄漏假密钥（正向对照）",
        check.rc == 0
        and "fake-auth-code" not in check.combined
        and "fake-key-123456" not in check.combined
        and "sk-fake-000000000000" not in check.combined,
        f"rc={check.rc}",
    )

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
