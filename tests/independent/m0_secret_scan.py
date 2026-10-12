"""M0 密钥泄漏扫描：用真实 config.yaml 跑 CLI，扫描输出/日志/仓库中的明文密钥。

测试 Agent 专用；报告只输出掩码指纹，绝不打印密钥原文或片段。
用法: .venv\\Scripts\\python.exe tests\\independent\\m0_secret_scan.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import yaml

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "reports" / "evidence" / "secret-scan"
PYTHON = sys.executable
CONFIG = REPO / "config.yaml"
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
SKIP_FILES = {CONFIG.resolve()}
RESULTS: list[dict[str, Any]] = []
PARTIAL_WINDOW = 20
EVIDENCE_WINDOW = 12


def mask(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"<len={len(value)} sha1={digest}>"


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def load_real_secrets() -> tuple[list[str], list[str]]:
    """返回 (完整密钥值列表, 敏感片段列表)。敏感片段用于部分泄漏检测。"""
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    full: list[str] = []
    sensitive: list[str] = []
    api_key = str(data.get("llm", {}).get("api_key") or "").strip()
    webhook = str(data.get("push", {}).get("wecom", {}).get("webhook") or "").strip()
    password = str(data.get("push", {}).get("email", {}).get("password") or "").strip()
    if api_key:
        full.append(api_key)
        sensitive.append(api_key)
    if webhook:
        full.append(webhook)
        for key, value in parse_qsl(urlsplit(webhook).query):
            if key == "key" and len(value) >= 8:
                sensitive.append(value)
    if password:
        full.append(password)
        sensitive.append(password)
    return list(dict.fromkeys(full)), list(dict.fromkeys(sensitive))


def has_window(text: str, secret: str, window: int) -> bool:
    return any(
        secret[start : start + window] in text
        for start in range(0, max(0, len(secret) - window + 1))
    )


def window_hits(text: str, secret: str, window: int = PARTIAL_WINDOW) -> int:
    return sum(
        1
        for start in range(0, max(0, len(secret) - window + 1))
        if secret[start : start + window] in text
    )


def partial_report(text: str, sensitive: list[str]) -> dict[str, int]:
    return {
        mask(secret): window_hits(text, secret)
        for secret in sensitive
        if secret not in text and window_hits(text, secret) > 0
    }


def sanitize(text: str, secrets: list[str]) -> str:
    out = text
    for secret in secrets:
        out = out.replace(secret, "***")
    cleaned: list[str] = []
    for line in out.splitlines():
        if any(has_window(line, secret, EVIDENCE_WINDOW) for secret in secrets):
            cleaned.append("（该行含密钥片段，已整行脱敏）")
        else:
            cleaned.append(line)
    return "\n".join(cleaned)


def run_real_cli(
    slug: str, extra_args: list[str], secrets: list[str], sensitive: list[str]
) -> None:
    cmd = [PYTHON, "-m", "aihot", "check", *extra_args, "--config", str(CONFIG)]
    cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, timeout=120, check=False)
    stdout = cp.stdout.decode("utf-8", "replace")
    stderr = cp.stderr.decode("utf-8", "replace")
    combined = stdout + stderr
    leaks = [mask(secret) for secret in secrets if secret in combined]
    partial = partial_report(combined, sensitive)
    record(
        f"real-cli-{slug}-no-secret",
        not leaks,
        f"rc={cp.returncode}, 完整泄漏={leaks if leaks else '无'}",
    )
    record(
        f"real-cli-{slug}-no-partial-secret",
        not partial,
        f"部分泄漏(20字符窗口命中数)={partial if partial else '无'}",
    )
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"real-cli-{slug}.txt").write_text(
        f"COMMAND: {' '.join(cmd)}\nEXIT CODE: {cp.returncode}\n"
        "（以下为脱敏后的原始输出）\n--- STDOUT ---\n"
        + sanitize(stdout, secrets)
        + "\n--- STDERR ---\n"
        + sanitize(stderr, secrets)
        + "\n",
        encoding="utf-8",
    )


def scan_paths(secrets: list[str], sensitive: list[str]) -> None:
    hits: dict[str, dict[str, int]] = {}
    partial_hits_map: dict[str, dict[str, int]] = {}
    files_scanned = 0
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() in SKIP_FILES or any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files_scanned += 1
        found = {mask(secret): text.count(secret) for secret in secrets if secret in text}
        if found:
            hits[str(path.relative_to(REPO))] = found
        partial = partial_report(text, sensitive)
        if partial:
            partial_hits_map[str(path.relative_to(REPO))] = partial
    hits_text = json.dumps(hits, ensure_ascii=False) if hits else "无"
    record("repo-scan-no-plaintext", not hits, f"扫描 {files_scanned} 个文件；命中: {hits_text}")
    partial_text = json.dumps(partial_hits_map, ensure_ascii=False) if partial_hits_map else "无"
    record("repo-scan-no-partial-plaintext", not partial_hits_map, f"部分命中: {partial_text}")

    log_path = REPO / "logs" / "aihot.log"
    log_hits: dict[str, int] = {}
    log_partial: dict[str, int] = {}
    log_lines = 0
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        log_lines = len(text.splitlines())
        log_hits = {mask(secret): text.count(secret) for secret in secrets if secret in text}
        log_partial = partial_report(text, sensitive)
    record(
        "log-file-no-plaintext",
        not log_hits,
        f"logs/aihot.log 行数={log_lines}, 完整命中: {log_hits if log_hits else '无'}",
    )
    record(
        "log-file-no-partial-plaintext",
        not log_partial,
        f"部分命中: {log_partial if log_partial else '无'}",
    )


def check_example_config() -> None:
    data = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    problems: list[str] = []
    if str(data.get("llm", {}).get("api_key") or "").strip():
        problems.append("llm.api_key 非空")
    if str(data.get("push", {}).get("wecom", {}).get("webhook") or "").strip():
        problems.append("wecom.webhook 非空")
    if str(data.get("push", {}).get("email", {}).get("password") or "").strip():
        problems.append("email.password 非空")
    detail = "; ".join(problems) if problems else "三个密钥字段均为空"
    record("example-config-no-secrets", not problems, detail)


def check_malformed_yaml_leak(secrets: list[str], sensitive: list[str]) -> None:
    """坏 YAML：PyYAML 错误信息会回显行内容，验证 CLI 是否把密钥打印出来。"""
    original = CONFIG.read_text(encoding="utf-8")
    lines = original.splitlines()
    injected = False
    for index, line in enumerate(lines):
        if line.strip().startswith("webhook:"):
            lines[index] = line + ": oops"
            injected = True
            break
    if not injected:
        record("malformed-yaml-injected", False, "未找到 webhook 行，无法注入语法错误")
        return
    broken = Path(tempfile.mkdtemp(prefix="aihot-m0-broken-")) / "config.yaml"
    broken.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [PYTHON, "-m", "aihot", "check", "--config", str(broken)]
    cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, timeout=120, check=False)
    combined = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
    leaked = [mask(secret) for secret in secrets if secret in combined]
    partial = partial_report(combined, sensitive)
    record(
        "malformed-yaml-console-no-leak",
        not leaked,
        f"rc={cp.returncode}, 完整泄漏={leaked if leaked else '无'}",
    )
    record(
        "malformed-yaml-console-no-partial-leak",
        not partial,
        f"部分泄漏(20字符窗口命中数)={partial if partial else '无'}",
    )
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "malformed-yaml-output.txt").write_text(
        f"COMMAND: {' '.join(cmd)}\nEXIT CODE: {cp.returncode}\n"
        "（以下为脱敏后的原始输出）\n--- STDOUT+STDERR ---\n"
        + sanitize(combined, secrets)
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    secrets, sensitive = load_real_secrets()
    print(f"完整密钥数={len(secrets)}，指纹={[mask(secret) for secret in secrets]}")
    print(f"敏感片段数={len(sensitive)}，指纹={[mask(secret) for secret in sensitive]}")
    if not secrets:
        record("real-secrets-present", False, "config.yaml 未发现非空密钥，无法验证脱敏")
        return 1
    run_real_cli("plain", [], secrets, sensitive)
    run_real_cli("debug", ["--debug"], secrets, sensitive)
    run_real_cli("net", ["--net"], secrets, sensitive)
    scan_paths(secrets, sensitive)
    check_example_config()
    check_malformed_yaml_leak(secrets, sensitive)

    failed = [item for item in RESULTS if not item["ok"]]
    print(f"\n== SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    for item in failed:
        print(f"FAILED: {item['name']} :: {item['detail']}")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "_summary.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
