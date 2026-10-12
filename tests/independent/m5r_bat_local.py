"""M5 声明 8 独立验收（回归）：既有 bat 全链路 localhost 子集仍正常。

说明：`tests/independent/m4_bat.py` 的第 4 段会直连**真实公网源**（其通过前提是 Clash 代理可用），
与本轮"禁止访问公网"的硬约束冲突；因此本脚本复跑 m4_bat 的 localhost 断言子集：
- staged `run_daily.bat --dry-run`（假 RSS + 假 LLM + 假企微）成功路径；
- 配置损坏（YAML 语法 / 结构非法）→ rc=3；
- run_daily.bat 未被改动、真实库/存档未被触碰。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_bat_local.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402


def main() -> int:
    suite = m3.Suite("m5r_bat_local")
    m5.ensure_certs()
    before = m3.real_state_snapshot()
    entries = m5.rss_entries(8, "bat")

    # ---------- 1. staged bat --dry-run 成功路径 ----------
    with m3.FakeRSS(entries) as rss, m5.m4.FakeLLMProbe() as llm:
        llm.generator = m3.default_llm_generator
        ws = m3.workspace("m5r-bat-local")
        staged_root = m5.TEMPROOT / "m4-bat" / "m5r-bat-local"
        # 企微关闭：P1 修复后企微强制直连（忽略环境代理），开企微会直连真实域名
        raw = m5.build_raw(
            ws,
            proxy_url="",
            proxy_enabled=False,
            llm_base_url=llm.base_url,
            wecom_enabled=False,
            source_url=rss.url,
            data_dir=staged_root / "data",
            log_dir=staged_root / "logs",
        )
        raw["push"]["email"]["enabled"] = False
        root = m5.m4.stage_bat("m5r-bat-local", raw)
        suite.check(
            "8.1 staged run_daily.bat 与真实 bat 字节一致",
            (root / "run_daily.bat").read_bytes() == (m5.PROJECT / "run_daily.bat").read_bytes(),
            f"sha256={m5.m4.bat_sha256()[:16]}…",
        )
        env = m5.clean_proxy_env({"SSL_CERT_FILE": str(m3.CA_PEM)})
        result = m5.m4.run_bat(root, ["--dry-run"], timeout=900, env_extra=env)
        suite.evidence("1-bat-dryrun-success.txt", result["log"] + result["stdout"])
        log = result["log"]
        suite.check(
            "8.2 bat 成功路径退出码=0",
            result["rc"] == 0,
            f"rc={result['rc']}（{result['seconds']}s）",
        )
        suite.check(
            "8.3 日志含预检 + run 段 + exit=0",
            "preflight" in log and "run =====" in log and "exit=0" in log,
            "三段齐全",
        )
        preflight_part = log.split("run =====")[0]
        suite.check(
            "8.4 预检无 [ERR]（代理未启用 / LLM 假服务 OK）",
            "[ERR]" not in preflight_part and "[OK ] LLM API" in preflight_part,
            f"OK={preflight_part.count('[OK ]')} ERR={preflight_part.count('[ERR]')}",
            evidence=preflight_part[-1200:],
        )
        digests_dir = root / "data" / "digests"
        names = sorted(path.name for path in digests_dir.iterdir()) if digests_dir.exists() else []
        suite.check(
            "8.5 dry-run 产出日报三件套（md/html/jpg，不推送）",
            any(name.endswith(".md") for name in names)
            and any(name.endswith(".html") for name in names)
            and any(name.endswith(".jpg") for name in names),
            f"files={names}",
        )
        run_part = log.rsplit("run =====", 1)[-1]
        suite.check(
            "8.6 dry-run 未推送任何消息（日志明确「已跳过（dry-run）」）",
            "已跳过（dry-run）" in run_part and "[ERR]" not in run_part,
            "推送段=已跳过（dry-run）",
        )

    # ---------- 2. 配置损坏 ----------
    ws = m3.workspace("m5r-bat-local-broken")
    broken_yaml = ws / "broken-syntax.yaml"
    broken_yaml.write_text("app: [unclosed\n  window_hours: 24\n", encoding="utf-8")
    result = m5.m4.run_python(
        ["-m", "aihot", "run", "--dry-run", "--config", str(broken_yaml)], timeout=120
    )
    suite.evidence(
        "2-broken-syntax.txt", result["stdout"] + "\n--- stderr ---\n" + result["stderr"]
    )
    suite.check(
        "8.7 YAML 语法损坏 → rc=3 且不抛 traceback",
        result["rc"] == 3 and "Traceback" not in result["stdout"] + result["stderr"],
        f"rc={result['rc']}",
    )
    broken_struct = ws / "broken-struct.yaml"
    broken_struct.write_text(
        "app: {window_hours: 0}\nllm: {base_url: 'not-a-url', api_key: ''}\n"
        "push: {wecom: {enabled: true, webhook: 'http://bad'}, email: {enabled: false}}\n"
        "sources: []\n",
        encoding="utf-8",
    )
    result2 = m5.m4.run_python(
        ["-m", "aihot", "run", "--dry-run", "--config", str(broken_struct)], timeout=120
    )
    suite.evidence(
        "3-broken-struct.txt", result2["stdout"] + "\n--- stderr ---\n" + result2["stderr"]
    )
    suite.check(
        "8.8 结构非法（无源/坏 URL/无 key）→ rc=3",
        result2["rc"] == 3 and "配置校验未通过" in result2["stdout"],
        f"rc={result2['rc']}",
    )

    after = m3.real_state_snapshot()
    suite.check(
        "8.9 真实 data/digests 未被改动（文件名+大小+mtime）",
        before["digests"] == after["digests"],
        f"digests={sorted(after['digests'])}",
    )
    suite.check(
        "8.10 真实库快照前后一致（bat 只追加日志）",
        before["db"] == after["db"],
        "db 快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
