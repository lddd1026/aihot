"""M0 回归探测：配置错误/摘要输出中的密钥模式是否被 CLI 脱敏。

场景：用户把 webhook / API key 误填到其它字段：
- ConfigError 回显字段值时（rc=3），`key=...` / `sk-...` 模式必须脱敏；
- 合法配置摘要中的代理地址不得明文回显密钥。

测试 Agent 专用，使用自造假密钥。
用法: .venv\\Scripts\\python.exe tests\\independent\\m0_issue_redaction_probe.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "reports" / "evidence" / "regression"
PYTHON = sys.executable

FAKE_WEBHOOK_KEY = "M0WRONGFIELD0123456789abcdef"
FAKE_SK_KEY = "sk-m0wrongfield-0123456789abcdef"
FAKE_WEBHOOK_URL = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={FAKE_WEBHOOK_KEY}"

CASES: dict[str, tuple[dict[str, object], int]] = {
    "webhook_in_timezone": (
        {
            "app": {"timezone": FAKE_WEBHOOK_URL},
            "llm": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            "sources": [{"id": "a", "type": "rss", "url": "https://a.com/feed"}],
        },
        3,
    ),
    "sk_key_in_source_url": (
        {
            "llm": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            "sources": [{"id": "a", "type": "rss", "url": FAKE_SK_KEY}],
        },
        3,
    ),
    "webhook_in_proxy_url": (
        {
            "llm": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            "proxy": {"enabled": True, "url": "not-a-url-" + FAKE_WEBHOOK_KEY},
            "sources": [{"id": "a", "type": "rss", "url": "https://a.com/feed"}],
        },
        3,
    ),
    "webhook_url_in_proxy_url": (
        {
            "llm": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            "proxy": {"enabled": True, "url": FAKE_WEBHOOK_URL},
            "push": {"wecom": {"enabled": False}, "email": {"enabled": False}},
            "sources": [{"id": "a", "type": "rss", "url": "https://a.com/feed"}],
        },
        0,
    ),
}


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="aihot-m0-issue-redact-"))
    results: list[dict[str, object]] = []
    for name, (cfg, expect_rc) in CASES.items():
        path = work / f"{name}.yaml"
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        cmd = [PYTHON, "-m", "aihot", "check", "--config", str(path)]
        cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, timeout=120, check=False)
        output = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
        leaks = [needle for needle in (FAKE_WEBHOOK_KEY, FAKE_SK_KEY) if needle in output]
        masked = "key=***" in output or "sk-***" in output
        ok = cp.returncode == expect_rc and not leaks
        results.append(
            {
                "case": name,
                "expect_rc": expect_rc,
                "rc": cp.returncode,
                "leaks": leaks,
                "masked": masked,
                "ok": ok,
            }
        )
        flag = "PASS" if ok else "FAIL"
        print(
            f"[{flag}] {name}: rc={cp.returncode}（期望 {expect_rc}）, "
            f"leaks={leaks or '无'}, 脱敏标记={masked}"
        )
        for line in output.splitlines():
            if "实际:" in line or "代理" in line:
                print("    ", line.strip())
    (EVIDENCE / "issue-redaction-probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failed = [item for item in results if not item["ok"]]
    print(f"\n== SUMMARY: {len(results) - len(failed)}/{len(results)} passed ==")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
