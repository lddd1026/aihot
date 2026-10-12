"""M5 声明 3 + 4 独立验收（M6 适配版）：代理可用时真的走代理；代理不可用时降级直连而非跳过。

★ 本轮（M6）适配说明：
  `net.resolve_proxy()` 新增了**功能性健康检查**（经代理请求探测点）。第 2 轮起健康检查为
  **多探测点**（`net.PROXY_HEALTH_PROBES`：gstatic 204 → api.github.com/rate_limit，
  任一通过即健康），
  健康探测目标是 https 页面，假代理无法在「只连 localhost」的前提下完成 TLS 隧道，因此：
  - 声明 3（代理可用 → 采集走代理）改为**进程内**验证，并在进程内把
    `net.PROXY_HEALTH_PROBES` 临时指向本机假代理（unittest.mock.patch.object，不改 src）。
    断言从「端口可连即用代理」
    —— 那正是本轮被修掉的行为 —— 改为「健康检查通过才用代理」。
  - 新增 3.4：同一夹具下（假代理只能服务 http、无法服务 https 健康探测）走 CLI 的真实常量时，
    代理会被判定为不可用 → 降级直连 → 只经代理可达的源失败（rc=1）。这是本轮刻意引入的行为变更，
    由 m6r_degrade_e2e.py / m6r_proxy_health.py 做更完整的覆盖。

- 声明 3：起本地"假 HTTP 代理"，config.proxy 指向它；采集源指向一个**关闭的本地端口**
  （http://127.0.0.1:1/feed.xml）。只有"请求真的经过假代理"才可能抓到 8 条，
  假代理同时记录绝对 URI 请求行 → 双重证据。
- 声明 4：config.proxy 指向坏端口；源用本地假 RSS（直连可达）。
  `aihot fetch --dry-run` 必须成功抓取（不是跳过、不是全失败），并出现"降级为直连"日志。
- 反证：代理坏端口 + 源也不可达 → 必须 all_failed（rc=1），不能谎报成功。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_fetch.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402

import aihot.net as net  # noqa: E402
import aihot.runner as runner  # noqa: E402
from aihot.config import load_config  # noqa: E402

BAD_PROXY = f"http://127.0.0.1:{m5.BAD_PORT}"
DEAD_FEED = f"http://127.0.0.1:{m5.BAD_PORT}/feed.xml"
FETCH_COUNT = re.compile(r"\[OK \]\s+(\S+)\s+(\d+) 条")


def first_ok_count(text: str, source_id: str) -> int | None:
    for found_id, count in FETCH_COUNT.findall(text):
        if found_id == source_id:
            return int(count)
    return None


def main() -> int:
    suite = m3.Suite("m5r_fetch")
    before = m3.real_state_snapshot()
    entries = m5.rss_entries(8, "fetch")

    # ---------- 声明 3（M6 适配版）：健康检查通过 → 采集走代理 ----------
    with m5.FakeHttpProxy(m3.rss_feed(entries)) as proxy:
        ws = m3.workspace("m5r-fetch-proxy")
        cfg_path = ws / "config.yaml"
        raw = m5.build_raw(
            ws,
            proxy_url=proxy.url,
            llm_base_url="http://127.0.0.1:1/v1",
            source_url=DEAD_FEED,  # 目标端口是关闭的
        )
        m5.m4.dump_config(cfg_path, raw)
        cfg = load_config(cfg_path)
        # M6 第 2 轮：健康检查已从 PROXY_HEALTH_URL 改为多探测点 PROXY_HEALTH_PROBES，
        # 这里把探测点收敛为一个"指向本机假代理"的探测点。
        with mock.patch.object(
            net,
            "PROXY_HEALTH_PROBES",
            ((f"{proxy.url}/generate_204", frozenset({200, 204})),),
        ):
            outcome = runner.fetch_stage(cfg, dry_run=True)
        m5.write_json("1-fake-proxy-requests.json", proxy.requests)
        suite.evidence(
            "1-fetch-via-proxy.txt",
            f"proxy_mode={outcome.proxy_mode}\n"
            f"stats={[(s.source_id, s.ok, s.count) for s in outcome.stats]}\n"
            f"items={[item.title for item in outcome.new_items]}\n"
            f"proxy.requests={proxy.requests[:3]}",
        )
        count = next(
            (stat.count for stat in outcome.stats if stat.source_id == "fake_feed"), None
        )
        suite.check(
            "3.1 健康检查通过（假代理 204）时代理被使用：proxy_mode=proxy",
            outcome.proxy_mode == "proxy",
            f"proxy_mode={outcome.proxy_mode}",
        )
        suite.check(
            "3.2 目标端口关闭仍抓到 8 条（只可能经假代理）",
            count == 8 and len(outcome.new_items) == 8,
            f"fake_feed 抓取条数={count}（目标 {DEAD_FEED} 是关闭端口）",
        )
        dead_prefix = f"http://127.0.0.1:{m5.BAD_PORT}"
        suite.check(
            "3.3 假代理记录了绝对 URI 形式的 GET（证明流量确实经代理）",
            any(path.startswith(dead_prefix) for path in proxy.proxied_get_paths),
            f"proxied_get_paths={proxy.proxied_get_paths[:3]}",
            evidence=str(proxy.requests[:5]),
        )

    # ---------- 3.4（M6 新行为）：https 健康探测失败 → 降级直连，不谎报 ----------
    with m5.FakeHttpProxy(m3.rss_feed(entries)) as proxy2:
        ws = m3.workspace("m5r-fetch-proxy-health-fail")
        cfg_path = ws / "config.yaml"
        raw = m5.build_raw(
            ws,
            proxy_url=proxy2.url,
            llm_base_url="http://127.0.0.1:1/v1",
            source_url=DEAD_FEED,
        )
        m5.m4.dump_config(cfg_path, raw)
        result = m3.run_cli(
            ["fetch", "--dry-run", "--config", str(cfg_path)],
            env_extra=m5.clean_proxy_env(),
            timeout=120,
        )
        suite.evidence("1b-fetch-proxy-health-check-fails.txt", result.text)
        suite.check(
            "3.4（行为变更）健康探测无法通过时不再使用该代理：只经代理可达的源失败、rc=1",
            result.rc == 1
            and "降级为直连" in result.combined
            and "全部信息源采集失败" in result.combined
            and proxy2.proxied_get_paths == [],
            f"rc={result.rc}；代理收到的绝对 URI={proxy2.proxied_get_paths[:2]}",
        )

    # ---------- 声明 4：代理不可用 → 降级直连（本地假 RSS） ----------
    with m3.FakeRSS(entries) as rss:
        ws = m3.workspace("m5r-fetch-degraded")
        cfg = ws / "config.yaml"
        raw = m5.build_raw(
            ws, proxy_url=BAD_PROXY, llm_base_url="http://127.0.0.1:1/v1", source_url=rss.url
        )
        m5.m4.dump_config(cfg, raw)
        result = m3.run_cli(
            ["fetch", "--dry-run", "--config", str(cfg)],
            env_extra=m5.clean_proxy_env(),
            timeout=120,
        )
        suite.evidence("2-fetch-degraded-direct.txt", result.text)
        count = first_ok_count(result.stdout, "fake_feed")
        suite.check(
            "4.1 代理坏端口时 fetch --dry-run rc=0（不跳过、不整体失败）",
            result.rc == 0,
            f"rc={result.rc}",
            evidence=result.text,
        )
        suite.check(
            "4.2 直连可达的源真的抓到了 8 条",
            count == 8 and rss.hits >= 1,
            f"条数={count}；假 RSS 命中={rss.hits}",
        )
        suite.check(
            "4.3 输出含'降级为直连'日志（用户可知情）",
            "降级为直连" in result.combined,
            next(
                (line.strip() for line in result.combined.splitlines() if "降级" in line),
                "（未出现降级日志）",
            ),
        )
        suite.check(
            "4.4 非 dry-run 时才落库（dry-run 未创建数据库文件）",
            not (ws / "data" / "aihot.db").exists(),
            f"db exists={(ws / 'data' / 'aihot.db').exists()}",
        )

    # ---------- 反证：代理坏 + 源也坏 → 必须失败 ----------
    ws = m3.workspace("m5r-fetch-allfail")
    cfg = ws / "config.yaml"
    raw = m5.build_raw(
        ws, proxy_url=BAD_PROXY, llm_base_url="http://127.0.0.1:1/v1", source_url=DEAD_FEED
    )
    m5.m4.dump_config(cfg, raw)
    result = m3.run_cli(
        ["fetch", "--dry-run", "--config", str(cfg)],
        env_extra=m5.clean_proxy_env(),
        timeout=120,
    )
    suite.evidence("3-fetch-all-failed.txt", result.text)
    suite.check(
        "4.5 降级不等于谎报：源也不可达时 rc=1 且提示全部失败",
        result.rc == 1 and "全部信息源采集失败" in result.combined,
        f"rc={result.rc}",
        evidence=result.text,
    )

    suite.check(
        "4.6 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
