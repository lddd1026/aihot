"""M0 对抗探测（产品路径）：坏 YAML 时 `aihot check` 输出是否回显密钥。

对每个畸形配置真实运行 `python -m aihot check`，检查 stdout+stderr：
- 退出码必须为 3（配置错误）
- 输出不得包含完整假密钥，也不得包含任意 20 字符窗口
- YAML 错误提示应说明已隐藏原文

测试 Agent 专用，使用自造假密钥。
用法: .venv\\Scripts\\python.exe tests\\independent\\m0_yaml_error_probe.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "reports" / "evidence" / "regression"
PYTHON = sys.executable

KEY_SECRET = "M0YAMLKEY0123456789abcdef"
SK_SECRET = "sk-m0yamlfake-0123456789abcdef"
WEBHOOK = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={KEY_SECRET}"

BASE = (
    "app:\n"
    "  timezone: Asia/Shanghai\n"
    "llm:\n"
    "  base_url: https://api.deepseek.com\n"
    "  model: deepseek-chat\n"
    f"  api_key: {SK_SECRET}\n"
    "push:\n"
    "  wecom:\n"
    "    enabled: false\n"
    f'    webhook: "{WEBHOOK}"\n'
    "sources:\n"
    "  - id: a\n"
    "    type: rss\n"
    "    url: https://a.com/feed\n"
)

CASES: dict[str, str] = {
    "extra_colon": BASE.replace(f'"{WEBHOOK}"', f'"{WEBHOOK}": oops'),
    "unclosed_bracket": BASE.replace(f'"{WEBHOOK}"', f"[{WEBHOOK}"),
    "unclosed_quote": BASE.replace(f'"{WEBHOOK}"', f'"{WEBHOOK}'),
    "tab_indent": BASE + "\tbad: 1\n",
    "bad_anchor": BASE.replace(f'"{WEBHOOK}"', f"&{KEY_SECRET} [1,2"),
    "bad_alias": BASE.replace(f'"{WEBHOOK}"', f"*{KEY_SECRET}"),
}


def has_window(text: str, secret: str, window: int = 20) -> bool:
    return any(
        secret[start : start + window] in text
        for start in range(0, max(0, len(secret) - window + 1))
    )


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="aihot-m0-yaml-e2e-"))
    results: list[dict[str, object]] = []
    details: list[str] = []
    for name, text in CASES.items():
        path = work / f"{name}.yaml"
        path.write_text(text, encoding="utf-8")
        cmd = [PYTHON, "-m", "aihot", "check", "--config", str(path)]
        cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, timeout=120, check=False)
        output = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
        leaks = [needle for needle in (KEY_SECRET, SK_SECRET) if needle in output]
        partial = [
            needle
            for needle in (KEY_SECRET, SK_SECRET)
            if needle not in output and has_window(output, needle)
        ]
        traceback = "Traceback (most recent call last)" in output
        hidden_hint = "已隐藏原文以防泄露密钥" in output
        ok = cp.returncode == 3 and not leaks and not partial and not traceback
        results.append(
            {
                "case": name,
                "rc": cp.returncode,
                "leaks": leaks,
                "partial": partial,
                "traceback": traceback,
                "hidden_hint": hidden_hint,
                "ok": ok,
            }
        )
        flag = "PASS" if ok else "FAIL"
        print(
            f"[{flag}] {name}: rc={cp.returncode}, leaks={leaks or '无'}, "
            f"partial={partial or '无'}, traceback={traceback}, 隐藏提示={hidden_hint}"
        )
        details.append(f"=== {name}: {flag} ===\n{output.strip()}\n")
    (EVIDENCE / "yaml-e2e-probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVIDENCE / "yaml-e2e-probe-details.txt").write_text(
        "以下为畸形配置经 `python -m aihot check` 的真实输出（密钥均为假值）：\n\n"
        + "\n".join(details),
        encoding="utf-8",
    )
    failed = [item for item in results if not item["ok"]]
    print(f"\n== SUMMARY: {len(results) - len(failed)}/{len(results)} passed ==")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
