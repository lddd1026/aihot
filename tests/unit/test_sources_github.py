"""GitHub 热门项目适配器单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from aihot.config import SourceConfig
from aihot.net import HttpClient
from aihot.sources.base import SourceError
from aihot.sources.github import GithubAdapter

SINCE = datetime(2026, 9, 24, tzinfo=UTC)

PAYLOAD = {
    "total_count": 160,
    "items": [
        {
            "full_name": "zai-org/ZCode",
            "html_url": "https://github.com/zai-org/ZCode",
            "description": "Z.ai's coding agent harness. Powerful and extensible.",
            "stargazers_count": 6725,
            "forks_count": 310,
            "language": "TypeScript",
            "topics": ["agent", "coding", "llm"],
            "created_at": "2026-09-20T04:00:00Z",
            "pushed_at": "2026-09-25T02:00:00Z",
        },
        {
            "full_name": "someone/noise",
            "html_url": "https://github.com/someone/noise",
            "description": "Android APK reverse engineering toolkit",
            "stargazers_count": 1200,
            "forks_count": 40,
            "language": "Python",
            "topics": [],
            "created_at": "2026-09-19T04:00:00Z",
        },
        {"full_name": "incomplete/entry"},  # 缺 html_url → 跳过
    ],
}

EXTRA = {"days": 7, "limit": 15, "min_stars": 100}


def make_adapter(handler, **extra: object) -> GithubAdapter:
    source = SourceConfig(
        id="github_trending",
        name="GitHub 热门项目",
        type="github",
        url="https://api.github.com/search/repositories",
        enabled=True,
        weight=0.9,
        extra={**EXTRA, **extra},
    )
    return GithubAdapter(source, HttpClient(transport=httpx.MockTransport(handler), backoff=0.0))


def test_parses_repos_with_signals() -> None:
    adapter = make_adapter(
        lambda request: httpx.Response(200, text=json.dumps(PAYLOAD))
    )
    items = adapter.fetch(SINCE)
    assert [item.title for item in items] == ["zai-org/ZCode", "someone/noise"]
    repo = items[0]
    assert repo.url == "https://github.com/zai-org/ZCode"
    assert repo.extra["kind"] == "repo"
    assert repo.extra["stars"] == 6725
    assert repo.extra["upvotes"] == 6725  # 参与社区热度
    assert repo.extra["forks"] == 310
    assert repo.extra["language"] == "TypeScript"
    assert repo.extra["topics"] == ["agent", "coding", "llm"]
    assert repo.extra["repo_created_at"].startswith("2026-09-20")
    adapter.http.close()


def test_published_is_discovery_time() -> None:
    """热门项目可能创建于几天前，但必须能进入当日窗口。"""
    adapter = make_adapter(lambda request: httpx.Response(200, text=json.dumps(PAYLOAD)))
    items = adapter.fetch(SINCE)
    now = datetime.now(UTC)
    for item in items:
        assert abs((item.published_at - now).total_seconds()) < 60
    adapter.http.close()


def test_summary_contains_description_and_meta() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=json.dumps(PAYLOAD)))
    item = adapter.fetch(SINCE)[0]
    assert "coding agent harness" in item.raw_summary
    assert "star 6725" in item.raw_summary
    assert "topic: agent" in item.raw_summary
    adapter.http.close()


def test_query_params() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, text=json.dumps(PAYLOAD))

    adapter = make_adapter(handler, language="Python", min_stars=500)
    adapter.fetch(SINCE)
    query = captured["q"]
    assert "created:>" in query
    assert "stars:>500" in query
    assert "language:Python" in query
    assert captured["sort"] == "stars"
    assert captured["per_page"] == "15"
    assert "authorization" not in captured["headers"]
    adapter.http.close()


def test_token_sets_authorization_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, text=json.dumps(PAYLOAD))

    adapter = make_adapter(handler, token="ghp_testtoken")
    adapter.fetch(SINCE)
    assert captured["headers"]["authorization"] == "Bearer ghp_testtoken"
    adapter.http.close()


def test_missing_items_key_raises() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=json.dumps({"message": "no"})))
    with pytest.raises(SourceError, match="items"):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_http_failure_raises() -> None:
    adapter = make_adapter(lambda request: httpx.Response(403, text="rate limited"))
    with pytest.raises(SourceError):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_empty_items_returns_empty() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=json.dumps({"items": []})))
    assert adapter.fetch(SINCE) == []
    adapter.http.close()


def test_created_after_uses_days_setting() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, text=json.dumps({"items": []}))

    adapter = make_adapter(handler, days=3)
    adapter.fetch(SINCE)
    expected = (datetime.now(UTC) - timedelta(days=3)).date().isoformat()
    assert f"created:>{expected}" in captured["q"]
    adapter.http.close()
