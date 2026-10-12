"""M6 第 2 轮（P2 修复复验）：P2-1 前缀排除 / P2-2 limit 语义 / P2-3 多探测点 / P2-4 文档。

- P2-1：/sponsors、/orgs、/settings、/login 等「非仓库两段路径」必须被丢弃；
  同时不能误伤（`sponsors-x/…`、仓库名恰为 `sponsors` 的 foo/sponsors 应当保留）。
- P2-2：limit 作用于**解析成功**的条目（首行坏行不再占用名额）：
  「坏行 + 2 条好行，limit=2 应出 2 条」。
- P2-3：多探测点健康检查：第一探测点失败、第二通过 → 健康；全部失败 → 降级；
  403 只在探测点的"可接受状态集"里才算可达；第一点通过时短路（不再探测第二点）；
  用真实常量 + 假节点（CONNECT 被拒）做反证：不会"永远健康"。
- P2-4：DESIGN.md §2.2 模块清单与 §4.4 难点表已更新且与实现一致。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_p2_fixes.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

# 本轮证据写 reports/evidence/m6r/round2/（必须在导入 m6r_support 之前设置）
os.environ.setdefault("AIHOT_M6R_EVIDENCE", "m6r/round2")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unittest import mock  # noqa: E402

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

import aihot.net as net  # noqa: E402
from aihot.checks import probe_proxy  # noqa: E402
from aihot.config import SourceConfig, load_config  # noqa: E402
from aihot.net import HttpClient, proxy_healthy, resolve_proxy  # noqa: E402
from aihot.sources.base import SourceError  # noqa: E402
from aihot.sources.github_trending import RESERVED_OWNERS, GithubTrendingAdapter  # noqa: E402

# --------------------------------------------------------------------------- 本轮新增夹具行

def _row(href: str, name: str, *, stars: int = 111, today: int = 11, desc: str = "row") -> str:
    return f"""
  <article class="Box-row">
    <h2 class="h3 lh-condensed"><a href="{href}">{name}</a></h2>
    <p class="col-9">{desc}</p>
    <div class="f6 color-fg-muted">
      <span itemprop="programmingLanguage">Go</span>
      <a href="{href}/stargazers">{stars:,}</a>
      <a href="{href}/forks">{stars // 10:,}</a>
      <span class="float-sm-right">{today:,} stars today</span>
    </div>
  </article>
"""


RESERVED_ROWS = [
    _row("/sponsors/acme-org", "Sponsor @acme-org"),
    _row("/orgs/acme-org", "acme-org (organization)"),
    _row("/settings/profile", "Settings"),
    _row("/login/oauth", "Log in"),
    _row("/Sponsors/Acme", "Sponsor (大小写变体)"),
]

KEPT_ROWS = [
    _row("/sponsors-x/real-repo", "sponsors-x / real-repo", stars=90, today=9),
    _row("/foo/sponsors", "foo / sponsors", stars=80, today=8),
    _row("/acme-org/model-lab", "acme-org / model-lab", stars=70, today=7),
]


class PathRoutedProxy:
    """按路径后缀路由的假代理（仅本轮新用例使用，不改既有夹具行为）。

    ``routes``: {路径后缀: 状态码}；未命中 → 502。所有请求（含 CONNECT）都记录。
    """

    def __init__(self, routes: dict[str, int], *, body: bytes = b"ok") -> None:
        self.routes = routes
        self.body = body
        self.requests: list[str] = []
        self._httpd = m6._QuietServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> PathRoutedProxy:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _reply(self, status: int, data: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append(self.path)
                for suffix, status in server.routes.items():
                    if self.path.endswith(suffix):
                        self._reply(status, server.body if status == 200 else b"x")
                        return
                self._reply(502, b"unrouted")

            def do_CONNECT(self) -> None:  # noqa: N802
                server.requests.append("CONNECT " + self.path)
                self._reply(502, b"no tunnel")

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


def adapter_for(url: str, *, limit: int = 12) -> GithubTrendingAdapter:
    return GithubTrendingAdapter(
        SourceConfig(
            id="github_trending_page",
            name="trending",
            type="github_trending",
            url=url,
            enabled=True,
            weight=0.9,
            extra={"period": "daily", "limit": limit},
        ),
        HttpClient(proxy=None, retries=0, timeout=10),
    )


def main() -> int:
    suite = m3.Suite("m6r_p2_fixes")
    before = m3.real_state_snapshot()
    m6.clean_proxy_env()
    ws = m3.workspace("m6r-round2")
    since = datetime.now(UTC) - timedelta(hours=24)

    # ===================== P2-1：保留前缀排除 =====================
    with m6.FakeTrendingSite(m6.trending_html([*RESERVED_ROWS, *KEPT_ROWS])) as site:
        adapter = adapter_for(site.url)
        items = adapter.fetch(since)
        adapter.http.close()
        titles = [item.title for item in items]
        suite.evidence_json("p21-titles.json", titles)
        suite.check(
            "R2-1.1 /sponsors、/orgs、/settings、/login（含大小写变体）全部被丢弃",
            set(titles) == {"sponsors-x/real-repo", "foo/sponsors", "acme-org/model-lab"},
            f"解析出的仓库={titles}",
        )
        suite.check(
            "R2-1.2 不误伤：sponsors-x/real-repo 与 foo/sponsors 保留（精确匹配 owner）",
            "sponsors-x/real-repo" in titles and "foo/sponsors" in titles,
            f"解析出的仓库={titles}",
        )
        suite.check(
            "R2-1.3 正常仓库照常解析（acme-org/model-lab）",
            "acme-org/model-lab" in titles,
            f"解析出的仓库={titles}",
        )

    # 反证：页面上只有保留前缀行 → 必须抛 SourceError（而不是产出脏数据）
    with m6.FakeTrendingSite(m6.trending_html(RESERVED_ROWS)) as site:
        adapter = adapter_for(site.url)
        raised = ""
        try:
            adapter.fetch(since)
        except SourceError as exc:
            raised = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            raised = f"{type(exc).__name__}(非 SourceError!)"
        finally:
            adapter.http.close()
        suite.check(
            "R2-1.4 反证：页面只剩保留前缀行 → 抛 SourceError（不产出脏数据）",
            raised.startswith("SourceError"),
            raised or "（未抛出，异常！）",
        )

    # ===================== P2-1 补：真实页面"误杀"核对 =====================
    # 用真实 Trending 页核对：页面上出现的 h2 链接里，owner 是否真的会撞上保留名单
    # （若撞上 → 说明有真实仓库被过滤掉，属误杀；否则保留名单在真实数据上零误杀）。
    live_html = ""
    try:
        client = HttpClient(proxy=None, retries=0, timeout=20)
        try:
            page = client.get("https://github.com/trending", params={"since": "daily"})
        finally:
            client.close()
        if page.ok:
            live_html = page.text
    except Exception as exc:  # noqa: BLE001
        suite.note(f"真实页面只读 GET 失败，跳过误杀核对：{type(exc).__name__}")
    if live_html:
        from bs4 import BeautifulSoup  # noqa: E402

        owners: list[str] = []
        for node in BeautifulSoup(live_html, "html.parser").select("article.Box-row h2 a[href]"):
            href = str(node.get("href") or "")
            match = re.match(r"^/([^/]+)/([^/]+)$", href)
            if match:
                owners.append(match.group(1))
        reserved_hits = sorted({owner for owner in owners if owner.lower() in RESERVED_OWNERS})
        suite.evidence(
            "p21-live-owners.txt",
            f"真实页面 h2 链接 owner（{len(owners)} 个）: {sorted(set(owners))}\n"
            f"撞上保留名单的 owner: {reserved_hits or '无'}\n"
            f"RESERVED_OWNERS={sorted(RESERVED_OWNERS)}",
        )
        suite.check(
            "R2-1.5 真实页面误杀核对：无任何真实 owner 撞上保留名单（零误杀）",
            not reserved_hits and len(owners) >= 5,
            f"owner 数={len(owners)}；撞名单={reserved_hits or '无'}",
        )

    # ===================== P2-2：limit 作用于解析成功的条目 =====================
    page_rows = [m6.ROW_DEEP_HREF, m6.ROW_FULL, m6.ROW_PARTIAL, m6.ROW_TAIL, m6.ROW_EXTRA]
    good_titles = [
        "acme-org/model-lab",
        "solo-dev/blobber",
        "tail-org/agent-desk",
        "extra-org/rag-notes",
    ]
    with m6.FakeTrendingSite(m6.trending_html(page_rows)) as site:
        for limit, expected in [(2, 2), (1, 1), (3, 3), (4, 4), (0, 4), (999, 4), (25, 4)]:
            adapter = adapter_for(site.url, limit=limit)
            got = [item.title for item in adapter.fetch(since)]
            adapter.http.close()
            suite.check(
                f"R2-2.{limit} limit={limit} → 解析成功的 {expected} 条"
                f"（旧语义下 limit=2 只会出 1 条）",
                len(got) == expected and got == good_titles[:expected],
                f"limit={limit} 得 {got}",
            )

    # 反证：全是坏行 → SourceError（limit 截断不掩盖故障）
    with m6.FakeTrendingSite(m6.trending_html([m6.ROW_DEEP_HREF, m6.ROW_ABSOLUTE_HREF])) as site:
        adapter = adapter_for(site.url, limit=2)
        raised = ""
        try:
            adapter.fetch(since)
        except SourceError as exc:
            raised = f"{type(exc).__name__}: {exc}"
        finally:
            adapter.http.close()
        suite.check(
            "R2-2.8 反证：页面全是坏行 → SourceError（不被 limit 掩盖）",
            raised.startswith("SourceError"),
            raised or "（未抛出，异常！）",
        )

    # ===================== P2-3：多探测点健康检查 =====================
    real_probes = net.PROXY_HEALTH_PROBES
    suite.check(
        "R2-3.1 真实探测点配置：gstatic 204 + api.github.com/rate_limit（403 也算可达）",
        real_probes[0] == ("https://www.gstatic.com/generate_204", frozenset({200, 204}))
        and real_probes[1][0] == "https://api.github.com/rate_limit"
        and 403 in real_probes[1][1]
        and len(real_probes) >= 2,
        f"PROXY_HEALTH_PROBES={real_probes}",
    )

    p1 = "http://127.0.0.1:9/probe-one"
    p2 = "http://127.0.0.1:9/probe-two"

    # 3.2 第一点失败、第二点通过 → 健康
    with mock.patch.object(
        net,
        "PROXY_HEALTH_PROBES",
        ((p1, frozenset({200, 204})), (p2, frozenset({200, 204}))),
    ):
        with PathRoutedProxy({"/probe-one": 502, "/probe-two": 204}) as proxy:
            healthy = proxy_healthy(proxy.url)
            resolved = resolve_proxy(True, proxy.url)
        suite.evidence_json("p23-requests-second-probe-rescues.json", proxy.requests)
        suite.check(
            "R2-3.2 第一探测点失败、第二点通过 → proxy_healthy True、resolve_proxy 用代理",
            healthy is True and resolved == (proxy.url, "proxy"),
            f"healthy={healthy}；resolve={resolved}；代理收到={proxy.requests}",
        )
        suite.check(
            "R2-3.3 两个探测点都被尝试且顺序正确（2 次健康检查 × 2 个探测点 = 4 次）",
            len(proxy.requests) == 4
            and proxy.requests[0].endswith("/probe-one")
            and proxy.requests[1].endswith("/probe-two")
            and proxy.requests[2].endswith("/probe-one")
            and proxy.requests[3].endswith("/probe-two"),
            f"代理收到={proxy.requests}",
        )

    rate_url = "http://127.0.0.1:9/rate-limit"
    # 3.4 403 只在探测点声明的可接受状态集里才算可达（反证：不是"任何响应都算健康"）
    with PathRoutedProxy({"/rate-limit": 403}) as proxy:
        with mock.patch.object(
            net, "PROXY_HEALTH_PROBES", ((rate_url, frozenset({200, 204, 403})),)
        ):
            lenient = proxy_healthy(proxy.url)
        with mock.patch.object(net, "PROXY_HEALTH_PROBES", ((rate_url, frozenset({200, 204})),)):
            strict = proxy_healthy(proxy.url)
            degraded = resolve_proxy(True, proxy.url)
        suite.check(
            "R2-3.4 403 语义：声明可接受(200/204/403) → True；严格集(200/204) → False 并降级",
            lenient is True and strict is False and degraded == (None, "degraded"),
            f"lenient={lenient}；strict={strict}；resolve={degraded}",
        )

    # 3.5 全部失败 → 降级
    with PathRoutedProxy({"/probe-one": 403, "/probe-two": 500}) as proxy:
        with mock.patch.object(
            net,
            "PROXY_HEALTH_PROBES",
            ((p1, frozenset({200, 204})), (p2, frozenset({200, 204}))),
        ):
            healthy = proxy_healthy(proxy.url)
            resolved = resolve_proxy(True, proxy.url)
        suite.check(
            "R2-3.5 全部探测点失败（403/500）→ proxy_healthy False、resolve_proxy 降级",
            healthy is False and resolved == (None, "degraded"),
            f"healthy={healthy}；resolve={resolved}；代理收到={proxy.requests}",
        )

    # 3.6 第一点通过 → 短路，不再探测第二点（成本控制）
    with PathRoutedProxy({"/probe-one": 204, "/probe-two": 204}) as proxy:
        with mock.patch.object(
            net,
            "PROXY_HEALTH_PROBES",
            ((p1, frozenset({200, 204})), (p2, frozenset({200, 204}))),
        ):
            healthy = proxy_healthy(proxy.url)
        suite.check(
            "R2-3.6 第一点通过即短路（只发 1 次探测）",
            healthy is True and len(proxy.requests) == 1,
            f"healthy={healthy}；代理收到={proxy.requests}",
        )

    # 3.7 反证（真实常量、不 patch）：假节点 CONNECT 被拒 → 必判定异常（不会"永远健康"）
    with m6.FakeNodeProxy("http403") as bad:
        started = time.perf_counter()
        healthy = proxy_healthy(bad.url)
        elapsed = time.perf_counter() - started
        resolved = resolve_proxy(True, bad.url)
        suite.evidence(
            "p23-real-const-bad-node.txt",
            f"healthy={healthy}\nresolve={resolved}\nrequests={bad.requests}\n"
            f"elapsed={elapsed:.2f}s（真实常量：两次探测都走代理）",
        )
        suite.check(
            "R2-3.7 反证：真实常量 + 坏节点 → False（两个探测点都被尝试、都失败）",
            healthy is False
            and resolved == (None, "degraded")
            and sum(1 for row in bad.requests if row["method"] == "CONNECT") >= 2,
            f"healthy={healthy}；resolve={resolved}；CONNECT 次数="
            f"{sum(1 for row in bad.requests if row['method'] == 'CONNECT')}；耗时={elapsed:.2f}s",
        )

    # 3.8 probe_proxy 文案：多探测点失败 → WARN 节点异常；成功 → OK 节点健康
    with PathRoutedProxy({"/probe-one": 502, "/probe-two": 204}) as good, PathRoutedProxy(
        {"/probe-one": 403, "/probe-two": 500}
    ) as broken:
        cfg_good = load_config(
            m6.staged_config(
                ws,
                m6.raw_config(
                    ws,
                    sources=[m6.rss_source_payload("http://127.0.0.1:1/feed.xml")],
                    proxy_url=good.url,
                    proxy_enabled=True,
                ),
                name="cfg-good.yaml",
            )
        )
        cfg_bad = load_config(
            m6.staged_config(
                ws,
                m6.raw_config(
                    ws,
                    sources=[m6.rss_source_payload("http://127.0.0.1:1/feed.xml")],
                    proxy_url=broken.url,
                    proxy_enabled=True,
                ),
                name="cfg-bad.yaml",
            )
        )
        probes_ok = ((p1, frozenset({200, 204})), (p2, frozenset({200, 204})))
        with mock.patch.object(net, "PROXY_HEALTH_PROBES", probes_ok):
            ok_result = probe_proxy(cfg_good)
            bad_result = probe_proxy(cfg_bad)
        suite.check(
            "R2-3.8 probe_proxy：多探测点通过 → [OK ]「可连接且节点健康」",
            ok_result is not None and ok_result.ok and "节点健康" in ok_result.detail,
            f"{ok_result.mark if ok_result else None} {ok_result.detail if ok_result else ''}",
        )
        suite.check(
            "R2-3.9 probe_proxy：多探测点全失败 → [WARN]「端口可连接但节点异常」(fatal=False)",
            bad_result is not None
            and bad_result.mark == "[WARN]"
            and "节点异常" in bad_result.detail
            and bad_result.fatal is False,
            f"{bad_result.mark if bad_result else None} {bad_result.detail if bad_result else ''}",
        )

    # ===================== P2-4：文档 =====================
    design = Path("DESIGN.md").read_text(encoding="utf-8")
    design_lines = design.splitlines()
    search_api_phrase = bool(re.search(r"改用\s*\*{0,2}Search API", design))
    module_prefix = "| | `github_trending`"
    trending_prefixes = ("| GitHub Trending ", "| GitHub Trending页")
    module_line = next((line for line in design_lines if line.startswith(module_prefix)), "")
    difficulty_line = next(
        (line for line in design_lines if line.startswith(trending_prefixes)), ""
    )
    suite.evidence(
        "p24-design-lines.txt",
        f"模块清单行: {module_line}\n难点表行: {difficulty_line}\n"
        f"仍含『改用 Search API』={search_api_phrase}",
    )
    suite.check(
        "R2-4.1 DESIGN.md §2.2 模块清单已加入 github_trending（与 registry 一致）",
        "`github_trending`" in module_line and "Trending" in module_line,
        module_line or "（未找到模块清单行）",
    )
    suite.check(
        "R2-4.2 DESIGN.md §4.4 已改为双源互补，且不再写『改用 Search API』",
        ("双源互补" in difficulty_line or "Trending 页解析" in difficulty_line)
        and not search_api_phrase,
        difficulty_line or "（未找到难点表行）",
    )
    suite.check(
        "R2-4.3 文档描述与实现一致（三个要点：当日热榜 / 重试+单源隔离 / 2026-10 重新引入）",
        all(
            key in difficulty_line
            for key in ["Search API", "Trending", "单源隔离"]
        ),
        difficulty_line or "（未找到难点表行）",
    )

    # ===================== 收尾 =====================
    m6.clean_proxy_env()
    suite.check(
        "R2-5 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
