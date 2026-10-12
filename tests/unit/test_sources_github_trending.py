"""GitHub 当日热榜适配器单元测试（合成 HTML，结构复刻 2026-10 实测页面）。"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from aihot.config import SourceConfig
from aihot.net import HttpClient
from aihot.sources.base import SourceError
from aihot.sources.github_trending import GithubTrendingAdapter

SINCE = datetime(2026, 10, 12, tzinfo=UTC)
URL = "https://github.com/trending"

ROW_TEMPLATE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a class="Link" href="{href}">
      <span class="text-normal">{owner} /</span>{repo}
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 tmp-pr-4">{description}</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="tmp-mr-3 d-inline-block ml-0 tmp-ml-0">
      <span class="repo-language-color" style="background-color: #3178c6"></span>
      <span itemprop="programmingLanguage">{language}</span>
    </span>
    <a class="tmp-mr-3 Link Link--muted d-inline-block" href="{href}/stargazers">{stars}</a>
    <a class="tmp-mr-3 Link Link--muted d-inline-block" href="{href}/forks">{forks}</a>
    <span class="d-inline-block float-sm-right">{today} stars today</span>
  </div>
</article>
"""


def page(*rows: str) -> str:
    body = "".join(rows)
    return f"<html><body><div class='Box'>{body}</div></body></html>"


ROW_REA = ROW_TEMPLATE.format(
    href="/morluto/rea",
    owner="morluto",
    repo="rea",
    description="Reverse engineer anything with agents, from app behavior down to native binaries.",
    language="TypeScript",
    stars="124,887",
    forks="28,064",
    today="25,599",
)
ROW_AIRLLM = ROW_TEMPLATE.format(
    href="/lyogavin/airllm",
    owner="lyogavin",
    repo="airllm",
    description="AirLLM 70B inference with single 4GB GPU",
    language="Jupyter Notebook",
    stars="36,861",
    forks="3,834",
    today="548",
)
ROW_BAD_HREF = ROW_TEMPLATE.format(
    href="/login",
    owner="ghost",
    repo="",
    description="not a repo",
    language="",
    stars="1",
    forks="0",
    today="1",
)

EXTRA = {"period": "daily", "limit": 12}


def make_adapter(handler, **extra: object) -> GithubTrendingAdapter:
    source = SourceConfig(
        id="github_trending_page",
        name="GitHub 当日热榜",
        type="github_trending",
        url=URL,
        enabled=True,
        weight=0.9,
        extra={**EXTRA, **extra},
    )
    return GithubTrendingAdapter(
        source, HttpClient(transport=httpx.MockTransport(handler), backoff=0.0)
    )


def test_parses_rows_with_signals() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=page(ROW_REA, ROW_AIRLLM)))
    items = adapter.fetch(SINCE)

    assert [item.title for item in items] == ["morluto/rea", "lyogavin/airllm"]
    repo = items[0]
    assert repo.url == "https://github.com/morluto/rea"
    assert repo.extra["kind"] == "repo"
    assert repo.extra["stars"] == 124887
    assert repo.extra["stars_today"] == 25599
    assert repo.extra["forks"] == 28064
    assert repo.extra["language"] == "TypeScript"
    assert repo.extra["upvotes"] == 124887  # 社区热度信号与 github.py 一致
    assert repo.extra["trending_period"] == "daily"
    adapter.http.close()


def test_summary_contains_description_and_meta() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=page(ROW_REA)))
    item = adapter.fetch(SINCE)[0]
    assert "Reverse engineer anything with agents" in item.raw_summary
    assert "语言 TypeScript" in item.raw_summary
    assert "star 124,887" in item.raw_summary
    assert "今日 +25,599" in item.raw_summary
    assert "fork 28,064" in item.raw_summary
    adapter.http.close()


def test_published_is_discovery_time() -> None:
    """热榜条目没有发布时间，必须用"发现时间"进入当日窗口。"""
    adapter = make_adapter(lambda request: httpx.Response(200, text=page(ROW_REA)))
    item = adapter.fetch(SINCE)[0]
    now = datetime.now(UTC)
    assert abs((item.published_at - now).total_seconds()) < 60
    adapter.http.close()


def test_period_param_sent() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, text=page(ROW_REA))

    adapter = make_adapter(handler, period="weekly")
    adapter.fetch(SINCE)
    assert captured.get("since") == "weekly"
    adapter.http.close()


def test_invalid_period_falls_back_to_daily() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, text=page(ROW_REA))

    adapter = make_adapter(handler, period="hourly")
    adapter.fetch(SINCE)
    assert captured.get("since") == "daily"
    adapter.http.close()


def test_limit_applies() -> None:
    adapter = make_adapter(
        lambda request: httpx.Response(200, text=page(ROW_REA, ROW_AIRLLM)), limit=1
    )
    items = adapter.fetch(SINCE)
    assert [item.title for item in items] == ["morluto/rea"]
    adapter.http.close()


def test_limit_counts_parsed_items_not_rows() -> None:
    """P2-2：limit 是"解析成功的前 N 条"，坏行不能占用名额。"""
    adapter = make_adapter(
        lambda request: httpx.Response(200, text=page(ROW_BAD_HREF, ROW_REA, ROW_AIRLLM)),
        limit=2,
    )
    items = adapter.fetch(SINCE)
    assert [item.title for item in items] == ["morluto/rea", "lyogavin/airllm"]
    adapter.http.close()


def test_reserved_owner_skipped() -> None:
    """P2-1：/sponsors/xxx 这类两段路径不是仓库，必须排除。"""
    row_sponsors = ROW_TEMPLATE.format(
        href="/sponsors/acme",
        owner="acme",
        repo="",
        description="sponsor page",
        language="",
        stars="1",
        forks="0",
        today="1",
    )
    adapter = make_adapter(
        lambda request: httpx.Response(200, text=page(row_sponsors, ROW_REA))
    )
    items = adapter.fetch(SINCE)
    assert [item.title for item in items] == ["morluto/rea"]
    adapter.http.close()


def test_non_repo_href_skipped() -> None:
    """h2 链接不是 /owner/repo 形式的行必须丢弃（例如登录跳转）。"""
    adapter = make_adapter(lambda request: httpx.Response(200, text=page(ROW_BAD_HREF, ROW_REA)))
    items = adapter.fetch(SINCE)
    assert [item.title for item in items] == ["morluto/rea"]
    adapter.http.close()


def test_no_rows_raises() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=page()))
    with pytest.raises(SourceError, match="未匹配到趋势条目"):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_all_rows_invalid_raises() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=page(ROW_BAD_HREF)))
    with pytest.raises(SourceError, match="解析失败"):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_http_failure_raises() -> None:
    adapter = make_adapter(lambda request: httpx.Response(403, text="blocked"))
    with pytest.raises(SourceError):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_stars_today_missing_is_zero() -> None:
    row = ROW_REA.replace("25,599 stars today", "no signal")
    adapter = make_adapter(lambda request: httpx.Response(200, text=page(row)))
    item = adapter.fetch(SINCE)[0]
    assert item.extra["stars_today"] == 0
    assert "今日 +" not in item.raw_summary
    adapter.http.close()
