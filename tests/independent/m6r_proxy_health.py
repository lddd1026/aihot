"""M6 声明 4 独立验收：代理功能性健康检查 net.proxy_healthy() 与 resolve_proxy() 双向正确。

关键点：
- 正例：假代理对探测请求返回 204 → proxy_healthy() True → resolve_proxy() 用代理（mode=proxy）；
- 反例（不许"永远成功"）：403 / 500 / 连接后断开 / 端口关闭 → False →
  resolve_proxy() 降级（mode=degraded）；
- 端口通但节点坏：proxy_reachable() True 而 proxy_healthy() False（这正是本轮要识别的场景）；
- 另外覆盖：未启用 / 空地址 → direct；check_health=False 退化为纯 TCP 探测（保留的逃生门）；
- `checks.probe_proxy` 在「端口通但节点坏」时给 fatal=False 的 [WARN]「端口可连接但节点异常」，
  在健康时给 [OK ]「可连接且节点健康」；采集层（fetch_stage）在健康时把 proxy_mode 记为 proxy。

注意（测试保真度说明）：`PROXY_HEALTH_URL` 固定为 https://www.gstatic.com/generate_204。
按本轮"只连 localhost"的约束，**正例**在本进程内把该常量临时指向本机假代理
（unittest.mock.patch.object，不改 src 文件）；**反例**与 CLI 用例使用真实常量，
因此失败方向是端到端真实的（假代理无法满足 https 隧道的健康探测 → 必须降级）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_proxy_health.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

import aihot.net as net  # noqa: E402
from aihot.checks import probe_proxy, run_quick_checks  # noqa: E402
from aihot.config import load_config  # noqa: E402
from aihot.net import (  # noqa: E402
    proxy_healthy,
    proxy_reachable,
    resolve_proxy,
)


def main() -> int:
    suite = m3.Suite("m6r_proxy_health")
    before = m3.real_state_snapshot()
    m6.clean_proxy_env()
    ws = m3.workspace("m6r-proxy-health")

    # ---------- 4.1 正例：假代理 204 → health True，resolve 用代理 ----------
    # 说明（P2-3 修复后）：健康检查已从单个 PROXY_HEALTH_URL 变为多探测点
    # PROXY_HEALTH_PROBES；这里把探测点收敛为一个"指向本机假代理"的探测点。
    single_probe = ((m6.HEALTH_PROBE, frozenset({200, 204})),)
    with (
        m6.FakeNodeProxy("healthy") as good,
        mock.patch.object(net, "PROXY_HEALTH_PROBES", single_probe),
    ):
        healthy = proxy_healthy(good.url)
        proxy, mode = resolve_proxy(True, good.url)
        cfg = load_config(
            m6.staged_config(
                ws,
                m6.raw_config(
                    ws,
                    sources=[m6.rss_source_payload("http://127.0.0.1:1/feed.xml")],
                    proxy_url=good.url,
                    proxy_enabled=True,
                ),
            )
        )
        result = probe_proxy(cfg)
        suite.check(
            "4.1 假代理返回 204 → proxy_healthy() True（且流量确实经过代理）",
            healthy is True and any(p.startswith("http") for p in good.absolute_uri_gets),
            f"healthy={healthy}；代理收到的绝对 URI={good.absolute_uri_gets[:2]}",
            evidence=str(good.requests[:3]),
        )
        suite.check(
            "4.2 健康代理 → resolve_proxy() 返回 (url, 'proxy')",
            mode == "proxy" and proxy == good.url,
            f"proxy={proxy}；mode={mode}",
        )
        suite.check(
            "4.3 健康代理 → checks.probe_proxy 为 [OK ]「可连接且节点健康」",
            result is not None
            and result.ok
            and result.name == "代理"
            and "节点健康" in result.detail,
            f"mark={result.mark if result else None}；"
            f"detail={result.detail if result else None}",
        )

        # 4.4 采集层：健康代理 + 目标端口关闭（只有经代理才可能成功）→ proxy_mode=proxy
        good.body = m3.rss_feed(m6.m5.rss_entries(3, "proxyhealth"))
        cfg2 = load_config(
            m6.staged_config(
                ws,
                m6.raw_config(
                    ws,
                    sources=[m6.rss_source_payload(m6.DEAD_FEED)],
                    proxy_url=good.url,
                    proxy_enabled=True,
                ),
                name="config-fetch.yaml",
            )
        )
        import aihot.runner as runner  # noqa: E402

        outcome = runner.fetch_stage(cfg2, dry_run=True)
        suite.check(
            "4.4 健康代理时 fetch_stage 走代理：proxy_mode=proxy 且目标端口关闭仍抓到 3 条",
            outcome.proxy_mode == "proxy"
            and len(outcome.new_items) == 3
            and all(stat.ok for stat in outcome.stats),
            f"proxy_mode={outcome.proxy_mode}；items={len(outcome.new_items)}；"
            f"stats={[(s.source_id, s.ok, s.count) for s in outcome.stats]}",
        )
        suite.evidence(
            "4.4-fetch-via-healthy-proxy.txt",
            "目标源=" + m6.DEAD_FEED + "\n代理收到的请求=\n"
            + "\n".join(str(row) for row in good.requests)
            + f"\n\nitems={[i.title for i in outcome.new_items]}",
        )

    # ---------- 4.5 反例：403 / 500 / 断开 / 端口关闭 → 必须降级 ----------
    for label, mode in [
        ("4.5 假代理一律返回 403（节点坏）", "http403"),
        ("4.6 假代理一律返回 500（上游故障）", "http500"),
        ("4.7 假代理接受连接后立即断开", "drop"),
    ]:
        with m6.FakeNodeProxy(mode) as bad:
            reachable = proxy_reachable(bad.url)
            healthy = proxy_healthy(bad.url)  # 用真实 gstatic 常量：必须失败
            proxy, resolved = resolve_proxy(True, bad.url)
            suite.check(
                f"{label} → proxy_healthy() False、resolve_proxy 降级（TCP 可连={reachable}）",
                healthy is False and resolved == "degraded" and proxy is None and reachable is True,
                f"reachable={reachable}；healthy={healthy}；mode={resolved}；proxy={proxy}",
                evidence=str(bad.requests[:3]),
            )

    closed = f"http://127.0.0.1:{m6.BAD_PORT}"
    suite.check(
        "4.8 端口关闭 → proxy_reachable False、resolve_proxy 降级（不是报错）",
        proxy_reachable(closed) is False
        and proxy_healthy(closed) is False
        and resolve_proxy(True, closed) == (None, "degraded"),
        f"resolve_proxy={resolve_proxy(True, closed)}",
    )

    # ---------- 4.9 未启用 / 空地址 / 逃生门 ----------
    suite.check(
        "4.9 enabled=False → direct（不做任何探测）",
        resolve_proxy(False, "http://127.0.0.1:1") == (None, "direct")
        and resolve_proxy(False, "") == (None, "direct"),
        "enabled=False → direct",
    )
    with m6.FakeNodeProxy("healthy") as good2:
        proxy, mode = resolve_proxy(True, "", check_health=False)
        suite.check(
            "4.10 空地址 → direct（不误用代理）",
            (proxy, mode) == (None, "direct"),
            f"proxy={proxy} mode={mode}",
        )
        proxy2, mode2 = resolve_proxy(True, good2.url, check_health=False)
        suite.check(
            "4.11 check_health=False 退化为纯 TCP 探测（逃生门：不发健康探测请求）",
            mode2 == "proxy" and proxy2 == good2.url and good2.requests == [],
            f"mode={mode2}；代理收到的 HTTP 请求={good2.requests}",
        )

    # ---------- 4.12 反证：健康检查不是"永远成功" ----------
    with m6.FakeNodeProxy("http403") as bad2:
        cfg_bad = load_config(
            m6.staged_config(
                ws,
                m6.raw_config(
                    ws,
                    sources=[m6.rss_source_payload("http://127.0.0.1:1/feed.xml")],
                    proxy_url=bad2.url,
                    proxy_enabled=True,
                ),
                name="config-bad-node.yaml",
            )
        )
        result = probe_proxy(cfg_bad)
        suite.check(
            "4.12 端口通但节点坏 → probe_proxy [WARN]「端口可连接但节点异常」、fatal=False",
            result is not None
            and not result.ok
            and result.mark == "[WARN]"
            and "端口可连接但节点异常" in result.detail
            and result.fatal is False,
            f"mark={result.mark if result else None}；"
            f"detail={result.detail if result else None}",
        )
        quick = run_quick_checks(cfg_bad)
        proxy_item = next(item for item in quick if item.name == "代理")
        suite.check(
            "4.13 run_quick_checks 中代理项为可降级 WARN（不是致命 ERR）",
            proxy_item.mark == "[WARN]" and proxy_item.fatal is False,
            f"items={[(i.name, i.mark) for i in quick]}",
        )

    # ---------- 4.14 采集层：节点坏 + 目标端口关闭 → 降级直连 → 全失败（不谎报） ----------
    with m6.FakeNodeProxy("http403") as bad3:
        cfg_allfail = load_config(
            m6.staged_config(
                ws,
                m6.raw_config(
                    ws,
                    sources=[m6.rss_source_payload(m6.DEAD_FEED)],
                    proxy_url=bad3.url,
                    proxy_enabled=True,
                ),
                name="config-allfail.yaml",
            )
        )
        import aihot.runner as runner2  # noqa: E402

        outcome = runner2.fetch_stage(cfg_allfail, dry_run=True)
        suite.check(
            "4.14 节点坏 + 源只能经代理到达 → proxy_mode=degraded 且源全失败（不谎报成功）",
            outcome.proxy_mode == "degraded" and outcome.all_failed and not outcome.new_items,
            f"proxy_mode={outcome.proxy_mode}；all_failed={outcome.all_failed}；"
            f"items={len(outcome.new_items)}",
        )

    # ---------- 4.15 真实 config.yaml 的代理只做 TCP 可连性核对（不请求第三方探测点）----------
    import yaml  # noqa: E402

    real_cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    real_proxy = str((real_cfg.get("proxy") or {}).get("url") or "")
    reachable = proxy_reachable(real_proxy) if real_proxy else False
    suite.note(
        f"本机真实代理 {real_proxy}：TCP 可连={reachable}。"
        "按约束未让 proxy_healthy() 去请求 gstatic（第三方域名），"
        "因此真实代理的『节点健康』未在本轮验证；见报告『测试保真度与风险』。"
    )

    m6.clean_proxy_env()
    suite.check(
        "4.16 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
