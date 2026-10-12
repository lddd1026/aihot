"""M1 验收项 9：`check --net` 源健康度 + 回归（真实配置只读探测）。

本地模拟源为主；最后一节对真实 config.yaml 做 check --net（仅 GET，不写库、不发企微消息）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    PROJECT,
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
    ago,
    db_counts,
    rss_xml,
    run_cli,
    source,
    write_config,
)

REAL_CONFIG = PROJECT / "config.yaml"
REAL_DB = PROJECT / "data" / "aihot.db"


def fresh_dir(name: str) -> Path:
    TEMPROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"aihot-m1-{name}-", dir=TEMPROOT))


def main() -> int:  # noqa: C901
    suite = Suite("check_net")
    server = LocalServer()
    server.routes["/good"] = lambda q: Payload(
        200,
        rss_xml([{"title": "ok", "link": "https://ok.example/1", "date": ago(hours=1)}]),
    )
    server.routes["/bad500"] = lambda q: Payload(500, b"boom")
    server.routes["/notfound"] = lambda q: Payload(404, b"nope")
    server.routes["/llm/models"] = lambda q: Payload(401, b'{"error":"no key"}')
    try:
        _local_checks(suite, server)
        _real_check(suite)
    finally:
        server.stop()
    return suite.finish()


def _local_checks(suite: Suite, server: LocalServer) -> None:
    workspace = fresh_dir("netmix")
    cfg = write_config(
        workspace,
        [
            source("good1", server.url("/good")),
            source("bad500", server.url("/bad500")),
            source("notfound", server.url("/notfound")),
            source("disabled-bad", server.url("/bad500"), enabled=False),
        ],
        llm_base_url=server.url("/llm"),
    )
    result = run_cli(["check", "--net", "--config", str(cfg)])
    stdout = result.stdout
    probe_lines = [
        line for line in stdout.splitlines() if line.strip().startswith(("[OK ] 源", "[ERR] 源"))
    ]
    ok = (
        result.rc == 1
        and "[OK ] 源 good1" in stdout
        and "[ERR] 源 bad500" in stdout
        and "[ERR] 源 notfound" in stdout
        and all("disabled-bad" not in line for line in probe_lines)
    )
    suite.check(
        "9a-逐源OK/ERR+失败退出码1",
        ok,
        f"rc={result.rc}（期望 1）好源 OK、坏源 ERR、未启用源不进入探测行"
        f"（探测 {len(probe_lines)} 行）",
        evidence=result.stdout,
    )

    workspace_ok = fresh_dir("netok")
    cfg_ok = write_config(
        workspace_ok, [source("good1", server.url("/good"))], llm_base_url=server.url("/llm")
    )
    result_ok = run_cli(["check", "--net", "--config", str(cfg_ok)])
    ok2 = result_ok.rc == 0 and "[OK ] 源 good1" in result_ok.stdout
    suite.check(
        "9b-全通过退出码0",
        ok2,
        f"rc={result_ok.rc}（期望 0）",
        evidence=result_ok.stdout,
    )

    # 坏源数量与退出码单调关系
    workspace_multi = fresh_dir("netmulti")
    cfg_multi = write_config(
        workspace_multi,
        [
            source("bad500", server.url("/bad500")),
            source("notfound", server.url("/notfound")),
        ],
        llm_base_url=server.url("/llm"),
    )
    result_multi = run_cli(["check", "--net", "--config", str(cfg_multi)])
    errors = [line for line in result_multi.stdout.splitlines() if "[ERR]" in line]
    suite.check(
        "9c-多坏源全部报告",
        result_multi.rc == 1 and len(errors) >= 2,
        f"rc={result_multi.rc} [ERR] 行数={len(errors)}",
        evidence=result_multi.stdout,
    )


def _real_check(suite: Suite) -> None:
    """真实配置 check --net：只读探测，验证 5 个启用源逐项报告 + 密钥不外泄。"""
    if not REAL_CONFIG.exists():
        suite.note("真实 config.yaml 不存在，跳过真实配置探测")
        return
    before = db_counts(REAL_DB)
    result = run_cli(["check", "--net", "--config", str(REAL_CONFIG)], timeout=180)
    after = db_counts(REAL_DB)
    source_lines = [
        line
        for line in result.stdout.splitlines()
        if line.strip().startswith(("[OK ] 源", "[ERR] 源"))
    ]
    expected_ids = ("qbitai", "infoq_cn", "techcrunch_ai", "openai_news", "hackernews")
    covered = [sid for sid in expected_ids if any(sid in line for line in source_lines)]
    suite.check(
        "9d-真实配置逐源健康度",
        result.rc == 0 and len(covered) == len(expected_ids),
        f"rc={result.rc}（期望 0）覆盖启用源={covered} 共 {len(source_lines)} 行",
        evidence=result.stdout,
    )
    suite.check(
        "9e-真实库只读未变",
        before == after,
        f"探测前后 items/runs 快照一致: {before['items']}/{before['runs']}",
    )
    keys = _real_secrets()
    leaked = [name for name, value in keys.items() if value and value in result.stdout]
    suite.check(
        "9f-check --net 输出不含真实密钥",
        not leaked,
        f"扫描 {len(keys)} 个真实密钥字段：泄漏 {leaked or '无'}",
    )


def _real_secrets() -> dict[str, str]:
    """从真实配置读取密钥（只用于内存比对，不打印）。"""
    import yaml

    raw = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    llm = raw.get("llm") or {}
    wecom = (raw.get("push") or {}).get("wecom") or {}
    email = (raw.get("push") or {}).get("email") or {}
    return {
        "llm.api_key": str(llm.get("api_key") or ""),
        "wecom.webhook": str(wecom.get("webhook") or ""),
        "email.password": str(email.get("password") or ""),
    }


if __name__ == "__main__":
    raise SystemExit(main())
