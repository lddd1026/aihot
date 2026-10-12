"""M6 声明 5 独立验收：端到端降级（Clash 端口开着但节点坏）。

场景：config.proxy 指向本机「假节点」——TCP 端口可连，但对任何请求（含 https 健康探测的
CONNECT）都返回错误。此时：
- `aihot fetch --dry-run` 仍能抓到本地假 RSS 的条目（说明没有因为坏节点整体失败）；
- 日志/输出含"降级为直连"；
- `aihot check --quick` 退出码 0、代理项为 [WARN] 且含"节点异常"；
- 反证：假 LLM 关闭时 `check --quick` 仍 rc=1（预检不会因为本改动变成"永远成功"）；
- 回归：代理端口彻底关闭时也是 [WARN]「连接失败」，不阻断。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_degrade_e2e.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

FETCH_COUNT = re.compile(r"\[OK \]\s+(\S+)\s+(\d+) 条")
PROBE_LINE = re.compile(r"^\s*(\[(?:OK |WARN|ERR)\])\s+([^:]+):\s(.*)$")


def probe_line(output: str, name: str) -> str:
    for line in output.splitlines():
        found = PROBE_LINE.match(line)
        if found and found.group(2).strip() == name:
            return line.strip()
    return "（未找到）"


def fetch_counts(output: str) -> dict[str, int]:
    return {source_id: int(count) for source_id, count in FETCH_COUNT.findall(output)}


def main() -> int:
    suite = m3.Suite("m6r_degrade_e2e")
    before = m3.real_state_snapshot()
    ws = m3.workspace("m6r-degrade")
    entries_a = m6.m5.rss_entries(5, "degrade-a")
    entries_b = m6.m5.rss_entries(4, "degrade-b")

    with (
        m6.FakeNodeProxy("http403") as bad_node,
        m3.FakeRSS(entries_a) as rss_a,
        m3.FakeRSS(entries_b) as rss_b,
        m6.m4.FakeLLMProbe() as llm,
    ):
        raw = m6.raw_config(
            ws,
            sources=[
                m6.rss_source_payload(rss_a.url, source_id="fake_feed"),
                m6.rss_source_payload(rss_b.url, source_id="fake_feed_b"),
            ],
            proxy_url=bad_node.url,
            proxy_enabled=True,
            llm_base_url=llm.base_url,
        )
        cfg = m6.staged_config(ws, raw)

        # ---------- 5.1 采集端到端降级 ----------
        result = m3.run_cli(
            ["fetch", "--dry-run", "--config", str(cfg)],
            env_extra=m6.clean_proxy_env(),
            timeout=180,
        )
        suite.evidence("1-fetch-degraded-bad-node.txt", result.text)
        counts = fetch_counts(result.stdout)
        suite.check(
            "5.1 节点坏（端口通）时 fetch --dry-run rc=0 且两个本地源都抓到条目",
            result.rc == 0 and counts.get("fake_feed") == 5 and counts.get("fake_feed_b") == 4,
            f"rc={result.rc}；抓取分布={counts}",
        )
        suite.check(
            "5.2 输出含「降级为直连」（用户可知情，不静默）",
            "降级为直连" in result.combined,
            next(
                (line.strip() for line in result.combined.splitlines() if "降级" in line),
                "（未出现降级日志）",
            ),
        )
        suite.check(
            "5.3 降级日志点名了坏代理地址与「节点异常」",
            bad_node.url in result.combined and "节点异常" in result.combined,
            next(
                (
                    line.strip()
                    for line in result.combined.splitlines()
                    if "节点异常" in line or "代理" in line
                ),
                "（无相关行）",
            ),
        )
        suite.check(
            "5.4 dry-run 未创建数据库（仍不落库）",
            not (ws / "data" / "aihot.db").exists(),
            f"db exists={(ws / 'data' / 'aihot.db').exists()}",
        )
        suite.check(
            "5.5 假节点确实收到了探测请求（说明体检真的做了，不是跳过）",
            any(row["method"] == "CONNECT" for row in bad_node.requests),
            f"假节点收到的请求={bad_node.requests[:3]}",
        )
        suite.evidence("1b-bad-node-requests.json", m6.dump_ok("bad_node", bad_node.requests))

        # ---------- 5.6 check --quick：端口通但节点坏 → WARN ----------
        quick = m3.run_cli(
            ["check", "--quick", "--config", str(cfg)],
            env_extra=m6.clean_proxy_env(),
            timeout=180,
        )
        suite.evidence("2-check-quick-bad-node.txt", quick.text)
        proxy_line = probe_line(quick.stdout, "代理")
        suite.check(
            "5.6 节点坏时 check --quick rc=0（可降级失败不阻断）",
            quick.rc == 0,
            f"rc={quick.rc}",
        )
        suite.check(
            "5.7 代理项为 [WARN] 且提示「端口可连接但节点异常」",
            proxy_line.startswith("[WARN]") and "节点异常" in proxy_line,
            proxy_line,
        )
        suite.check(
            "5.8 LLM 项仍 [OK ]（直连本地假 LLM，未被坏节点牵连）",
            probe_line(quick.stdout, "LLM API").startswith("[OK ]"),
            probe_line(quick.stdout, "LLM API"),
        )

        # ---------- 5.9 反证：LLM 关闭 → rc=1（预检不是"永远成功"） ----------
        llm.stop()
        quick_fail = m3.run_cli(
            ["check", "--quick", "--config", str(cfg)],
            env_extra=m6.clean_proxy_env(),
            timeout=180,
        )
        suite.evidence("3-check-quick-llm-down.txt", quick_fail.text)
        suite.check(
            "5.9 反证：假 LLM 关闭后 check --quick rc=1 且 LLM 项 [ERR]",
            quick_fail.rc == 1 and probe_line(quick_fail.stdout, "LLM API").startswith("[ERR]"),
            f"rc={quick_fail.rc}；{probe_line(quick_fail.stdout, 'LLM API')}",
        )

    # ---------- 5.10 回归：代理端口彻底关闭 → WARN「连接失败」，不阻断 ----------
    closed_ws = m3.workspace("m6r-degrade-closed")
    with (
        m3.FakeRSS(entries_a) as rss_c,
        m6.m4.FakeLLMProbe() as llm2,
    ):
        raw_closed = m6.raw_config(
            closed_ws,
            sources=[m6.rss_source_payload(rss_c.url, source_id="fake_feed")],
            proxy_url=f"http://127.0.0.1:{m6.BAD_PORT}",
            proxy_enabled=True,
            llm_base_url=llm2.base_url,
        )
        cfg_closed = m6.staged_config(closed_ws, raw_closed)
        quick_closed = m3.run_cli(
            ["check", "--quick", "--config", str(cfg_closed)],
            env_extra=m6.clean_proxy_env(),
            timeout=180,
        )
        suite.evidence("4-check-quick-port-closed.txt", quick_closed.text)
        line = probe_line(quick_closed.stdout, "代理")
        suite.check(
            "5.10 端口关闭：rc=0、代理项 [WARN]「连接失败」且含「降级」",
            quick_closed.rc == 0
            and line.startswith("[WARN]")
            and "连接失败" in line
            and "降级" in line,
            line,
        )
        fetched = m3.run_cli(
            ["fetch", "--dry-run", "--config", str(cfg_closed)],
            env_extra=m6.clean_proxy_env(),
            timeout=180,
        )
        suite.evidence("5-fetch-port-closed.txt", fetched.text)
        suite.check(
            "5.11 端口关闭：fetch --dry-run rc=0 且抓到 5 条（老行为不回退）",
            fetched.rc == 0 and fetch_counts(fetched.stdout).get("fake_feed") == 5,
            f"rc={fetched.rc}；抓取={fetch_counts(fetched.stdout)}",
        )

    m6.clean_proxy_env()
    suite.check(
        "5.12 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
