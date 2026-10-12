"""Hacker News 适配器单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from aihot.config import SourceConfig
from aihot.net import HttpClient
from aihot.sources.base import SourceError
from aihot.sources.hackernews import HackerNewsAdapter

SINCE = datetime(2026, 9, 22, tzinfo=UTC)

PAYLOAD = {
    "hits": [
        {
            "objectID": "1",
            "title": "GPT-6 released",
            "url": "https://example.com/gpt6?utm_medium=hn",
            "points": 1500,
            "num_comments": 700,
            "created_at": "2026-09-23T08:00:00Z",
            "author": "someone",
        },
        {
            "objectID": "2",
            "title": "Ask HN: text post",
            "url": None,
            "points": 120,
            "num_comments": 30,
            "created_at_i": 1790121600,  # 2026-09-23T00:00:00Z
            "story_text": "hello <b>world</b>",
        },
        {
            "objectID": "3",
            "title": "old story",
            "url": "https://example.com/old",
            "points": 99,
            "created_at": "2026-09-01T00:00:00Z",
        },
    ]
}


def make_adapter(payload: dict) -> HackerNewsAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "created_at_i" in str(request.url)
        assert "points" in str(request.url)
        return httpx.Response(200, text=json.dumps(payload))

    source = SourceConfig(
        id="hackernews",
        name="Hacker News",
        type="hackernews",
        url="https://hn.algolia.com/api/v1/search_by_date",
        enabled=True,
        weight=0.8,
        extra={"min_points": 30},
    )
    return HackerNewsAdapter(
        source, HttpClient(transport=httpx.MockTransport(handler), backoff=0.0)
    )


def test_fetch_parses_points_and_text_posts() -> None:
    adapter = make_adapter(PAYLOAD)
    items = adapter.fetch(SINCE)
    by_title = {item.title: item for item in items}
    assert "old story" not in by_title  # 窗口外过滤
    gpt = by_title["GPT-6 released"]
    assert gpt.url == "https://example.com/gpt6"  # 跟踪参数去除
    assert gpt.extra["points"] == 1500
    assert gpt.extra["num_comments"] == 700
    assert gpt.extra["hn_url"].endswith("id=1")
    assert gpt.lang == "en"
    text_post = by_title["Ask HN: text post"]
    assert text_post.url == "https://news.ycombinator.com/item?id=2"
    assert text_post.raw_summary == "hello world"
    assert text_post.extra["points"] == 120
    adapter.http.close()


def test_empty_hits() -> None:
    adapter = make_adapter({"hits": []})
    assert adapter.fetch(SINCE) == []
    adapter.http.close()


def test_malformed_payload_raises() -> None:
    adapter = make_adapter({"unexpected": True})
    with pytest.raises(SourceError):
        adapter.fetch(SINCE)
    adapter.http.close()
