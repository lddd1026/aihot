"""M0 第 3 轮聚焦复验：D2-R（目录指向文件）、D6-R（代理地址脱敏）、D7-R（死默认值）。

全部黑盒执行 `python -m aihot check`，使用自造假密钥。
用法: .venv\\Scripts\\python.exe tests\\independent\\m0_round3_probe.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "reports" / "evidence" / "regression" / "round3"
PYTHON = sys.executable

FAKE_PASS = "M0PROXYPASS0123456789"
FAKE_KEY = "M0R3KEY0123456789abcdef"
FAKE_WEBHOOK = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={FAKE_KEY}"


def base_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "app": {"timezone": "Asia/Shanghai", "window_hours": 24, "top_n": 12},
        "llm": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "sk-r3fake-0123456789abcdef",
        },
        "proxy": {"enabled": False, "url": ""},
        "push": {"wecom": {"enabled": False}, "email": {"enabled": False}},
        "sources": [
            {"id": "a", "type": "rss", "url": "https://a.com/feed", "enabled": True, "weight": 1.0}
        ],
    }
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def run_check(cfg: dict[str, Any], work: Path, slug: str) -> tuple[int, str]:
    path = work / f"{slug}.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cmd = [PYTHON, "-m", "aihot", "check", "--config", str(path)]
    cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, timeout=120, check=False)
    output = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
    (EVIDENCE / f"{slug}.txt").write_text(
        f"COMMAND: {' '.join(cmd)}\nEXIT CODE: {cp.returncode}\n"
        "--- STDOUT+STDERR ---\n" + output + "\n",
        encoding="utf-8",
    )
    return cp.returncode, output


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="aihot-m0-r3-"))
    (work / "blocker-file").write_text("not a dir", encoding="utf-8")
    (work / "logs-ok").mkdir(exist_ok=True)

    # ---- D2-R：data_dir / log_dir 指向已存在文件 ----
    rc, out = run_check(
        base_cfg(app={"timezone": "Asia/Shanghai", "log_dir": "blocker-file"}),
        work,
        "d2r-log-dir-file",
    )
    record(
        "d2r-log-dir-is-file",
        rc == 3 and "app.log_dir 指向已存在的文件" in out and "Traceback" not in out,
        f"rc={rc}, 明确报错={'是' if 'app.log_dir 指向已存在的文件' in out else '否'}, "
        f"traceback={'有' if 'Traceback' in out else '无'}",
    )
    rc, out = run_check(
        base_cfg(app={"timezone": "Asia/Shanghai", "data_dir": "blocker-file"}),
        work,
        "d2r-data-dir-file",
    )
    record(
        "d2r-data-dir-is-file",
        rc == 3 and "app.data_dir 指向已存在的文件" in out and "Traceback" not in out,
        f"rc={rc}, 明确报错={'是' if 'app.data_dir 指向已存在的文件' in out else '否'}, "
        f"traceback={'有' if 'Traceback' in out else '无'}",
    )
    rc, out = run_check(
        base_cfg(app={"timezone": "Asia/Shanghai", "log_dir": "logs-ok"}),
        work,
        "d2r-existing-dir-ok",
    )
    record("d2r-existing-dir-ok", rc == 0, f"rc={rc}（期望 0，正常目录不受影响）")
    rc, out = run_check(
        base_cfg(app={"timezone": "Asia/Shanghai", "log_dir": "not-created-yet"}),
        work,
        "d2r-nonexistent-dir-ok",
    )
    record("d2r-nonexistent-dir-ok", rc == 0, f"rc={rc}（期望 0，待创建目录不受影响）")
    rc, out = run_check(
        base_cfg(app={"timezone": "Asia/Shanghai", "log_dir": "blocker-file/sub"}),
        work,
        "d2r-nested-under-file",
    )
    record(
        "d2r-nested-under-file-observation",
        True,
        f"rc={rc}, traceback={'有' if 'Traceback' in out else '无'}（嵌套路径观察项）",
    )

    # ---- D6-R：代理地址脱敏 ----
    rc, out = run_check(
        base_cfg(proxy={"enabled": True, "url": f"http://m0user:{FAKE_PASS}@127.0.0.1:7890?key={FAKE_KEY}"}),
        work,
        "d6r-credentials-and-key",
    )
    record(
        "d6r-credentials-and-key-masked",
        rc == 0
        and FAKE_PASS not in out
        and FAKE_KEY not in out
        and "m0user" not in out
        and "***@" in out
        and "key=***" in out,
        f"rc={rc}, 口令泄漏={'是' if FAKE_PASS in out else '否'}, "
        f"key 泄漏={'是' if FAKE_KEY in out else '否'}, ***@={'在' if '***@' in out else '不在'}",
    )
    rc, out = run_check(
        base_cfg(proxy={"enabled": True, "url": FAKE_WEBHOOK}), work, "d6r-webhook-url-in-proxy"
    )
    record(
        "d6r-webhook-url-in-proxy-masked",
        rc == 0 and FAKE_KEY not in out and "key=***" in out,
        f"rc={rc}, key 泄漏={'是' if FAKE_KEY in out else '否'}, "
        f"key=***={'在' if 'key=***' in out else '不在'}",
    )
    rc, out = run_check(
        base_cfg(proxy={"enabled": True, "url": f"not-a-url-{FAKE_KEY}"}),
        work,
        "d6r-bare-key-invalid-url",
    )
    record(
        "d6r-bare-key-invalid-url-observation",
        True,
        f"rc={rc}, 裸密钥回显={'是' if FAKE_KEY in out else '否'}（ConfigError 路径观察项）",
    )

    # ---- D7-R：llm 默认值单一来源 ----
    minimal = {
        "sources": [
            {"id": "a", "type": "rss", "url": "https://a.com/feed", "enabled": True, "weight": 1.0}
        ]
    }
    rc, out = run_check(minimal, work, "d7r-no-llm-section")
    record(
        "d7r-no-llm-section-errors",
        rc == 3 and "llm.base_url" in out and "llm.model" in out and "Traceback" not in out,
        f"rc={rc}, 提示 base_url={'是' if 'llm.base_url' in out else '否'}, "
        f"model={'是' if 'llm.model' in out else '否'}",
    )
    rc, out = run_check(base_cfg(), work, "d7r-valid-config-ok")
    record("d7r-valid-config-ok", rc == 0, f"rc={rc}（期望 0）")

    failed = [item for item in RESULTS if not item["ok"]]
    print(f"\n== SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    (EVIDENCE / "_summary.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
