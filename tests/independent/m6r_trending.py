"""M6 声明 1+2+3 独立验收：新增信息源 github_trending_page（GitHub 当日热榜，网页解析）。

声明 1：夹具页面能正确解析：仓库名 / URL / 语言 / 总 star / 今日 star / 描述 → RawItem 与
         raw_summary；extra.kind == "repo"；period / limit / 坏行跳过等边界。
声明 2：容错——无条目 / 结构改版 / HTTP 403 → SourceError；且不影响其他源（单源隔离）。
声明 3：不越界——只产出 RawItem（不写库 / 不推送 / 不调 LLM）；配置校验接受
         github_trending、拒绝未知类型；真实 config.yaml / config.example.yaml 配置合法。
（可选）对 https://github.com/trending 做一次只读 GET，验证真实页面仍能解析
        （直连不可用时改经本机配置代理；均不可用时记 NOTE）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_trending.py
"""

from __future__ import annotations

import dataclasses
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

from aihot.config import ConfigError, SourceConfig, load_config, validate  # noqa: E402
from aihot.models import RawItem  # noqa: E402
from aihot.net import HttpClient  # noqa: E402
from aihot.sources.base import SourceError  # noqa: E402
from aihot.sources.github_trending import GithubTrendingAdapter  # noqa: E402
from aihot.sources.registry import ADAPTER_TYPES, build_adapters, collect_all  # noqa: E402


def make_adapter(
    url: str,
    *,
    period: str = "daily",
    limit: int = 12,
    adapter_type: str = "github_trending",
) -> GithubTrendingAdapter:
    source = SourceConfig(
        id="github_trending_page",
        name="GitHub 当日热榜",
        type=adapter_type,
        url=url,
        enabled=True,
        weight=0.9,
        extra={"period": period, "limit": limit},
    )
    return GithubTrendingAdapter(source, HttpClient(proxy=None, retries=0, timeout=10))


def check_row(suite: m3.Suite, items: list[RawItem], index: int) -> None:
    expected = m6.expected_rows()[index]
    item = items[index]
    suite.check(
        f"1.{index + 1}a 第{index + 1}行 title/url 正确",
        item.title == expected["title"] and item.url == expected["url"],
        f"title={item.title!r} url={item.url!r}",
    )
    suite.check(
        f"1.{index + 1}b 第{index + 1}行语言/star/今日star/fork 进入 extra",
        item.extra.get("language") == expected["language"]
        and item.extra.get("stars") == expected["stars"]
        and item.extra.get("stars_today") == expected["stars_today"]
        and item.extra.get("forks") == expected["forks"],
        f"extra={{language:{item.extra.get('language')!r}, stars:{item.extra.get('stars')},"
        f" stars_today:{item.extra.get('stars_today')}, forks:{item.extra.get('forks')}}}",
    )
    suite.check(
        f"1.{index + 1}c 第{index + 1}行描述进入 raw_summary、kind=repo",
        (
            expected["description"] in item.raw_summary
            if expected["description"]
            else item.raw_summary
        )
        and item.extra.get("kind") == "repo"
        and item.extra.get("source_page") == "trending"
        and item.extra.get("trending_period") == "daily"
        and item.extra.get("upvotes") == expected["stars"],
        f"raw_summary={item.raw_summary!r}；kind={item.extra.get('kind')!r}；"
        f"upvotes={item.extra.get('upvotes')}",
    )


def main() -> int:
    suite = m3.Suite("m6r_trending")
    before = m3.real_state_snapshot()
    ws = m3.workspace("m6r-trending")

    # ---------- 声明 1：夹具解析 ----------
    with m6.FakeTrendingSite() as site:
        adapter = make_adapter(site.url)
        since = datetime.now(UTC) - timedelta(hours=24)
        items = adapter.fetch(since)
        adapter.http.close()
        suite.evidence_json(
            "1-items.json",
            [
                dataclasses.asdict(item)
                | {
                    "published_at": item.published_at.isoformat(),
                    "fetched_at": item.fetched_at.isoformat(),
                    "url_hash": item.url_hash,
                }
                for item in items
            ],
        )
        suite.check(
            "1.1 页面 6 行（2 行 href 不可用）→ 只产出 4 条 RawItem",
            len(items) == 4 and all(isinstance(item, RawItem) for item in items),
            f"条数={len(items)}；类型={[type(i).__name__ for i in items]}",
        )
        suite.check(
            "1.2 请求带 since=daily（period 生效）",
            any("since=daily" in path for path in site.query_of_requests),
            f"请求={site.query_of_requests}",
        )
        for index in range(min(4, len(items))):
            check_row(suite, items, index)
        suite.check(
            "1.5 描述被清洗（去标签、还原实体）",
            items[0].raw_summary.startswith("A tiny inference engine & toolkit for LLMs"),
            items[0].raw_summary,
        )
        suite.check(
            "1.6 published 用发现时间（在窗口内，非页面数据）",
            (datetime.now(UTC) - items[0].published_at) < timedelta(minutes=5),
            f"published={items[0].published_at.isoformat()}",
        )
        suite.check(
            "1.7 raw_summary 含元信息（语言/star/今日/fork 中文标注）",
            all(
                bit in items[0].raw_summary
                for bit in ["语言 Python", "star 124,887", "今日 +25,599", "fork 8,123"]
            )
            and "star 5" in items[1].raw_summary
            and "今日 +7" in items[1].raw_summary,
            items[0].raw_summary,
        )
        suite.evidence("1-raw-summaries.txt", "\n".join(item.raw_summary for item in items))

    # ---------- 声明 1：limit / period 边界 ----------
    with m6.FakeTrendingSite() as site:
        for limit, expected_count, label in [
            (2, 2, "limit=2"),
            (3, 3, "limit=3（P2-2 修复后：坏行不占额度，出 3 条）"),
            (0, 4, "limit=0 归一为默认 12（非 1）"),
            (25, 4, "limit=25 上限内不越界"),
            (999, 4, "limit=999 被夹到 25（页面只有 4 条有效行）"),
        ]:
            adapter = make_adapter(site.url, limit=limit)
            got = len(adapter.fetch(datetime.now(UTC) - timedelta(hours=24)))
            adapter.http.close()
            suite.check(
                f"1.8 {label} → {expected_count} 条",
                got == expected_count,
                f"limit={limit} 得 {got} 条",
            )
        adapter = make_adapter(site.url, period="weekly")
        adapter.fetch(datetime.now(UTC) - timedelta(hours=24))
        adapter.http.close()
        suite.check(
            "1.9 period=weekly 传递为 since=weekly",
            any("since=weekly" in path for path in site.query_of_requests),
            f"请求={site.query_of_requests}",
        )
        adapter = make_adapter(site.url, period="hourly")  # 非法 → 回退 daily
        adapter.fetch(datetime.now(UTC) - timedelta(hours=24))
        adapter.http.close()
        suite.check(
            "1.10 非法 period 回退 daily（不崩溃）",
            any("since=daily" in path for path in site.query_of_requests),
            f"请求={site.query_of_requests}",
        )

    # ---------- 声明 2：容错 ----------
    cases = [
        ("2.1 页面无条目（空页）", m6.trending_html([], wrap=True), 200, "未匹配到趋势条目"),
        (
            "2.2 结构改版（article 类名变了）",
            m6.trending_html(['<article class="TrendCard"><a href="/a/b">a/b</a></article>']),
            200,
            "未匹配到趋势条目",
        ),
        (
            "2.3 条目全不可解析（href 全是多级路径）",
            m6.trending_html([m6.ROW_DEEP_HREF, m6.ROW_DEEP_HREF]),
            200,
            "全部解析失败",
        ),
        ("2.4 HTTP 403（被拦截）", m6.trending_html(), 403, "请求失败"),
    ]
    for label, html, status, expect_text in cases:
        with m6.FakeTrendingSite(html, status=status) as site:
            adapter = make_adapter(site.url)
            raised = ""
            detail = ""
            try:
                adapter.fetch(datetime.now(UTC) - timedelta(hours=24))
            except SourceError as exc:
                raised = type(exc).__name__
                detail = str(exc)
            except Exception as exc:  # noqa: BLE001 - 非 SourceError 属于缺陷
                raised = f"{type(exc).__name__}(非 SourceError!)"
                detail = str(exc)
            finally:
                adapter.http.close()
            suite.check(
                f"{label} → 抛 SourceError 且信息可诊断",
                raised == "SourceError" and expect_text in detail,
                f"raised={raised or '（未抛出，异常！）'}；detail={detail[:90]}",
            )

    # ---------- 观察项（P2）：sponsors 等「非仓库」两段路径会不会被误当仓库 ----------
    with m6.FakeTrendingSite(m6.trending_html([m6.ROW_SPONSOR_HREF])) as sponsor_site:
        adapter = make_adapter(sponsor_site.url)
        sponsor_items: list[RawItem] = []
        sponsor_error = ""
        try:
            sponsor_items = adapter.fetch(datetime.now(UTC) - timedelta(hours=24))
        except SourceError as exc:
            sponsor_error = str(exc)
        finally:
            adapter.http.close()
    suite.evidence_json(
        "2b-sponsors-link-probe.json",
        {
            "items": [
                {"title": item.title, "url": item.url, "kind": item.extra.get("kind")}
                for item in sponsor_items
            ],
            "error": sponsor_error,
        },
    )
    if sponsor_items:
        suite.note(
            "P2-1 观察（异常）：h2 链接为 /sponsors/xxx 时仍被当作仓库 "
            f"（得到 {sponsor_items[0].title!r}）——修复未生效？"
        )
    else:
        suite.note(
            "P2-1 已修复：/sponsors/xxx（两段非仓库路径）被 RESERVED_OWNERS 排除，"
            f"未产出条目（{sponsor_error or '无条目'}）"
        )

    # ---------- 声明 2b：单源隔离（一个好源 + 一个坏源） ----------
    with m6.FakeTrendingSite(m6.trending_html(), status=403) as bad, m3.FakeRSS(
        m6.m5.rss_entries(6, "m6r")
    ) as rss:
        raw = m6.raw_config(
            ws,
            sources=[
                m6.trending_source_payload(bad.url),
                m6.rss_source_payload(rss.url),
            ],
        )
        cfg = load_config(m6.staged_config(ws, raw))
        http = HttpClient(proxy=None, retries=0, timeout=10)
        try:
            adapters, skipped = build_adapters(cfg, http)
            collected = collect_all(adapters, datetime.now(UTC) - timedelta(hours=24))
        finally:
            http.close()
        suite.check(
            "2.5 build_adapters 识别 github_trending 类型（不落 skipped）",
            len(adapters) == 2 and not skipped,
            f"adapters={[a.id for a in adapters]}；skipped={skipped}",
        )
        stats = {stat.source_id: stat for stat in collected.stats}
        suite.check(
            "2.6 坏源失败被隔离：另一个源照常产出 6 条",
            stats["github_trending_page"].ok is False
            and "403" in stats["github_trending_page"].error
            and stats["fake_feed"].ok is True
            and stats["fake_feed"].count == 6
            and len(collected.items) == 6,
            f"trending.ok={stats['github_trending_page'].ok}"
            f" err={stats['github_trending_page'].error[:60]}；"
            f"rss.ok={stats['fake_feed'].ok} count={stats['fake_feed'].count}",
            evidence=m6.dump_ok("stats", [dataclasses.asdict(s) for s in collected.stats]),
        )
        suite.evidence_json("2-isolation-items.json", [item.title for item in collected.items])

        # 端到端（进程内 fetch_stage）：部分失败 → 仍产出条目
        import aihot.runner as runner  # noqa: E402

        outcome = runner.fetch_stage(cfg, dry_run=True)
        suite.check(
            "2.7 fetch_stage（dry-run）部分失败仍返回条目，proxy_mode=direct",
            outcome.failed and len(outcome.new_items) == 6 and outcome.proxy_mode == "direct",
            f"failed={[s.source_id for s in outcome.failed]}；items={len(outcome.new_items)}；"
            f"proxy_mode={outcome.proxy_mode}",
        )
        suite.check(
            "2.8 失败源错误信息进入统计（可诊断，不是静默）",
            "SourceError" in stats["github_trending_page"].error,
            stats["github_trending_page"].error,
        )

    # ---------- 声明 3：不越界（不写库 / 不推送 / 不调 LLM） ----------
    with (
        m6.FakeTrendingSite() as site,
        m6.m4.FakeLLMProbe() as llm,
        m6.m5.RecordedWeComProbe("ok") as wecom,
    ):
        data_dir = ws / "data-adapter-only"
        source = SourceConfig(
            id="github_trending_page",
            name="trending",
            type="github_trending",
            url=site.url,
            enabled=True,
            weight=0.9,
            extra={"period": "daily", "limit": 12},
        )
        adapter = GithubTrendingAdapter(source, HttpClient(proxy=None, retries=0, timeout=10))
        items = adapter.fetch(datetime.now(UTC) - timedelta(hours=24))
        adapter.http.close()
        logs_dir = ws / "logs"
        stray_files = sorted(p.name for p in data_dir.rglob("*")) if data_dir.exists() else []
        stray_files += sorted(p.name for p in logs_dir.rglob("*")) if logs_dir.exists() else []
        suite.check(
            "3.1 适配器独立运行只产出 RawItem，且不创建数据库/日志/任何文件",
            len(items) == 4 and not stray_files,
            f"items={len(items)}；data_adapter_only 文件={stray_files or '无'}；"
            f"db exists={(data_dir / 'aihot.db').exists()}",
        )
        suite.check(
            "3.2 适配器运行期间未调用 LLM（假 LLM 0 请求）",
            llm.request_count == 0 and llm.models_probes == 0,
            f"POST={llm.request_count}，/models={llm.models_probes}",
        )
        suite.check(
            "3.3 适配器运行期间未推送企微（假企微 0 请求）",
            not wecom.text_requests and not wecom.image_requests and not wecom.markdown_requests,
            f"text={len(wecom.text_requests)} image={len(wecom.image_requests)}",
        )
        source_text = Path("src/aihot/sources/github_trending.py").read_text(encoding="utf-8")
        forbidden = re.findall(
            r"(?:from|import)\s+(aihot\.(?:store|delivery|pipeline|checks)[\w.]*)", source_text
        )
        suite.check(
            "3.4 适配器源码不导入 存储/推送/LLM 模块（静态核对）",
            not forbidden,
            f"命中导入={forbidden or '无'}",
        )

    # ---------- 声明 3b：配置校验 ----------
    ok_ws = m3.workspace("m6r-trending-cfg-ok")
    with m6.FakeTrendingSite() as site:
        raw_ok = m6.raw_config(ok_ws, sources=[m6.trending_source_payload(site.url)])
        cfg_ok = load_config(m6.staged_config(ok_ws, raw_ok))
    suite.check(
        "3.5 配置校验接受 type=github_trending（并保留 extra）",
        cfg_ok.sources[0].type == "github_trending"
        and cfg_ok.sources[0].extra.get("period") == "daily"
        and cfg_ok.sources[0].extra.get("limit") == 12,
        f"type={cfg_ok.sources[0].type}；extra={cfg_ok.sources[0].extra}",
    )
    suite.check(
        "3.6 ADAPTER_TYPES 注册了 github_trending",
        ADAPTER_TYPES.get("github_trending") is GithubTrendingAdapter,
        f"ADAPTER_TYPES={sorted(ADAPTER_TYPES)}",
    )

    bad_ws = m3.workspace("m6r-trending-cfg-bad")
    raw_bad = m6.raw_config(bad_ws, sources=[m6.trending_source_payload("http://127.0.0.1:1/x")])
    raw_bad["sources"][0]["type"] = "github_trending_v2"
    errors, _warnings = validate(raw_bad)
    raised = ""
    try:
        load_config(m6.staged_config(bad_ws, raw_bad))
    except ConfigError:
        raised = "ConfigError"
    suite.check(
        "3.7 未知类型被拒绝（ConfigError，且报错列出可选类型）",
        raised == "ConfigError"
        and any("github_trending_v2" in err and "github_trending" in err for err in errors),
        f"raised={raised}；errors={errors}",
    )

    # ---------- 声明 1c：真实配置文件（只读） ----------
    import yaml  # noqa: E402

    def _source_entry(conf: dict, source_id: str) -> dict | None:
        return next((s for s in conf.get("sources", []) if s.get("id") == source_id), None)

    example = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    example_errors, _ = validate(example)
    example_source = _source_entry(example, "github_trending_page")
    suite.check(
        "1.11 config.example.yaml 的新源合法（enabled/period/limit 齐全，整体校验无错）",
        example_source is not None
        and example_source.get("type") == "github_trending"
        and example_source.get("enabled") is True
        and example_source.get("extra", {}).get("period") == "daily"
        and example_source.get("extra", {}).get("limit") == 12
        and not example_errors,
        f"errors={example_errors}；entry={example_source}",
    )
    real = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    real_source = _source_entry(real, "github_trending_page")
    real_errors, _ = validate(real)
    suite.check(
        "1.12 真实 config.yaml 已启用该源且整体校验无错（未修改文件）",
        real_source is not None
        and real_source.get("type") == "github_trending"
        and real_source.get("enabled") is True
        and real_source.get("url") == "https://github.com/trending"
        and not real_errors,
        f"errors={real_errors}；entry={real_source}",
    )

    # ---------- 声明 1d（可选）：真实页面只读 GET ----------
    real_page = Path("src/aihot/sources/github_trending.py")
    live_html = ""
    live_route = ""

    def try_live(proxy: str | None) -> tuple[str, str]:
        client = HttpClient(proxy=proxy, retries=0, timeout=25)
        try:
            page = client.get("https://github.com/trending", params={"since": "daily"})
        finally:
            client.close()
        return (page.text, page.error) if page.ok else ("", page.error or f"HTTP {page.status}")

    direct_html, direct_err = try_live(None)
    if direct_html:
        live_html, live_route = direct_html, "直连"
    else:
        proxy_url = ""
        try:
            real_cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
            proxy_url = str((real_cfg.get("proxy") or {}).get("url") or "")
        except Exception as exc:  # noqa: BLE001
            suite.note(f"读取 config.yaml 代理配置失败，跳过代理实时核对：{type(exc).__name__}")
        if proxy_url:
            proxied_html, proxied_err = try_live(proxy_url)
            if proxied_html:
                live_html, live_route = proxied_html, f"本机代理 {proxy_url}"
            else:
                suite.note(
                    f"直连 github.com/trending 不可用（{direct_err[:80]}）；"
                    f"经本机代理也只读 GET 失败（{proxied_err[:80]}）→ 声明 1 以夹具解析为准"
                )
        else:
            suite.note(
                f"直连 github.com/trending 不可用（{direct_err[:80]}）且未配置本机代理 "
                f"→ 声明 1 以夹具解析为准"
            )

    if live_html:
        with m6.FakeTrendingSite(live_html) as site:
            adapter = make_adapter(site.url)
            live_items = adapter.fetch(datetime.now(UTC) - timedelta(hours=24))
            adapter.http.close()
        suite.evidence_json(
            f"4-live-github-trending-{live_route.split(' ')[0]}.json",
            [
                {
                    "title": item.title,
                    "url": item.url,
                    "raw_summary": item.raw_summary,
                    "extra": item.extra,
                }
                for item in live_items
            ],
        )
        suite.check(
            f"1.13 真实 https://github.com/trending 只读解析成功（{live_route}，≥5 条，字段齐全）",
            len(live_items) >= 5
            and all(
                item.url.startswith("https://github.com/")
                and item.extra.get("kind") == "repo"
                and item.title.count("/") == 1
                and item.extra.get("stars_today") > 0
                for item in live_items
            ),
            f"条数={len(live_items)}；样例={[i.title for i in live_items[:3]]}",
        )
    else:
        suite.note(f"真实页面只读 GET 不可用，声明 1 以上述夹具解析为准；文件={real_page.name}")

    suite.check(
        "3.8 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
