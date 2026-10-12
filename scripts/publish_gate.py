"""发布闸门：扫描"即将提交"的文件，确认不含真实密钥与个人邮箱。

用途：本仓库是公开仓库（见 PLAN.md 附录 B），提交前执行本脚本，
确保 config.yaml 里的真实密钥与个人邮箱**永远不会**进入版本库。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe scripts\\publish_gate.py

退出码：0 = 干净；1 = 发现命中（禁止提交，先脱敏）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 正常环境一定有 PyYAML
    print("[ERR] 缺少 PyYAML，无法读取 config.yaml")
    raise SystemExit(1) from None

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 明显的测试/占位邮箱：不视为个人隐私（真实地址由上面的 targets 精确比对兜底）
TEST_DOMAINS = frozenset({"example.com", "example.org", "example.net", "test.com", "localhost"})
TEST_LOCAL_PARTS = frozenset(
    {
        "test", "tests", "testuser", "sender", "receiver", "user", "admin", "demo",
        "example", "noreply", "no-reply", "a", "b", "c", "foo", "bar", "baz",
        "alice", "bob", "someone",
    }
)
FORBIDDEN_PATHS = ("config.yaml", ".env")
# 运行产物目录（只允许保留 .gitkeep；测试证据里的 *.log 属有意留档，不在此列）
FORBIDDEN_PREFIXES = ("logs/", "data/")
ALLOWED_EXCEPTIONS = frozenset({"logs/.gitkeep", "data/.gitkeep"})
SKIP_SUFFIXES = (".db", ".db-wal", ".db-shm", ".pyc")


def _is_test_email(address: str) -> bool:
    """测试/占位邮箱不算隐私（真实地址由 targets 精确比对兜底）。"""
    local, _, domain = address.lower().partition("@")
    return domain in TEST_DOMAINS or local in TEST_LOCAL_PARTS


def collect_targets() -> dict[str, str]:
    """从真实配置里取出需要防泄漏的值（config.yaml 不在版本库中）。"""
    targets: dict[str, str] = {}
    if not CONFIG.exists():
        return targets
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    wecom = (data.get("push") or {}).get("wecom") or {}
    llm = data.get("llm") or {}
    email = (data.get("push") or {}).get("email") or {}

    webhook = str(wecom.get("webhook") or "").strip()
    if webhook:
        targets["企业微信 webhook"] = webhook
        if "key=" in webhook:
            targets["企业微信 key"] = webhook.split("key=", 1)[1]
    if llm.get("api_key"):
        targets["LLM API key"] = str(llm["api_key"]).strip()
    if email.get("password"):
        targets["SMTP 授权码"] = str(email["password"]).strip()
    if email.get("username"):
        targets["邮箱账号"] = str(email["username"]).strip()
    for address in email.get("to_addrs") or []:
        targets[f"收件人 {address}"] = str(address).strip()
    return {name: value for name, value in targets.items() if len(value) >= 5}


def staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    targets = collect_targets()
    files = staged_files()
    print(f"发布闸门：检查 {len(files)} 个待提交文件")
    print(f"防护目标：{', '.join(targets) if targets else '（未找到 config.yaml，仅做邮箱检查）'}")

    hits: list[str] = []
    pii_hits: list[str] = []

    for rel in files:
        path = ROOT / rel
        forbidden = (
            rel in FORBIDDEN_PATHS
            or rel.endswith(SKIP_SUFFIXES)
            or (rel.startswith(FORBIDDEN_PREFIXES) and rel not in ALLOWED_EXCEPTIONS)
        )
        if forbidden:
            hits.append(f"{rel}（禁止入库的路径/类型）")
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, value in targets.items():
            if value and value in text:
                hits.append(f"{rel} ← {name}")
        for address in EMAIL_RE.findall(text):
            if _is_test_email(address):
                continue
            pii_hits.append(f"{rel} ← {address}")

    hits = sorted(set(hits))
    pii_hits = sorted(set(pii_hits))

    if hits:
        print("\n[ERR] 命中真实密钥/禁止文件（禁止提交）：")
        for item in hits:
            print(f"  - {item}")
    if pii_hits:
        print("\n[ERR] 命中非占位邮箱（公开仓库需要脱敏）：")
        for item in pii_hits:
            print(f"  - {item}")
    if hits or pii_hits:
        print("\n提示：脱敏后再提交（例如把邮箱替换为 example@qq.com）。")
        return 1

    print("\n[OK] 未发现真实密钥与个人邮箱，可以提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
