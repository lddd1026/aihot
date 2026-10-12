"""M4 新增适配器单元测试（arXiv / 通用 HTML / Hugging Face 论文）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from aihot.config import SourceConfig
from aihot.net import HttpClient
from aihot.sources.arxiv import ArxivAdapter
from aihot.sources.base import SourceError
from aihot.sources.hf_papers import HfPapersAdapter
from aihot.sources.html import HtmlAdapter

SINCE = datetime(2026, 9, 24, tzinfo=UTC)


def make_source(source_type: str, url: str, **extra: object) -> SourceConfig:
    return SourceConfig(
        id=f"test_{source_type}",
        name=f"test {source_type}",
        type=source_type,
        url=url,
        enabled=True,
        weight=1.0,
        extra=dict(extra),
    )


def make_http(handler) -> HttpClient:
    return HttpClient(transport=httpx.MockTransport(handler), backoff=0.0)


# ---------- arXiv ----------

ARXIV_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2609.12345v1</id>
    <title>Test Paper Title</title>
    <summary>Abstract text here with details.</summary>
    <published>2026-09-25T01:00:00Z</published>
    <link href="http://arxiv.org/abs/2609.12345v1" rel="alternate" type="text/html"/>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
    <category term="cs.AI"/>
    <category term="cs.CL"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2609.00001v1</id>
    <title>Old Paper</title>
    <summary>Old abstract.</summary>
    <published>2026-09-01T01:00:00Z</published>
    <link href="http://arxiv.org/abs/2609.00001v1" rel="alternate" type="text/html"/>
    <category term="cs.AI"/>
  </entry>
</feed>
"""


class TestArxivAdapter:
    def test_parses_and_filters_window(self) -> None:
        adapter = ArxivAdapter(
            make_source("arxiv", "http://export.arxiv.org/api/query"),
            make_http(lambda request: httpx.Response(200, text=ARXIV_SAMPLE)),
        )
        items = adapter.fetch(SINCE)
        assert len(items) == 1
        item = items[0]
        assert item.title == "Test Paper Title"
        assert item.url == "http://arxiv.org/abs/2609.12345v1"
        assert item.extra["kind"] == "paper"
        assert item.extra["authors"] == ["Alice", "Bob"]
        assert item.extra["categories"] == ["cs.AI", "cs.CL"]
        assert item.extra["arxiv_id"] == "2609.12345v1"
        assert "Abstract text" in item.raw_summary

    def test_query_uses_configured_categories(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, text=ARXIV_SAMPLE)

        adapter = ArxivAdapter(
            make_source("arxiv", "http://export.arxiv.org/api/query", categories=["cs.CV"]),
            make_http(handler),
        )
        adapter.fetch(SINCE)
        assert "cat%3Acs.CV" in captured["url"] or "cat:cs.CV" in captured["url"]

    def test_empty_feed_raises(self) -> None:
        adapter = ArxivAdapter(
            make_source("arxiv", "http://export.arxiv.org/api/query"),
            make_http(lambda request: httpx.Response(200, text="<feed xmlns='http://www.w3.org/2005/Atom'></feed>")),
        )
        with pytest.raises(SourceError):
            adapter.fetch(SINCE)

    def test_http_failure_raises(self) -> None:
        adapter = ArxivAdapter(
            make_source("arxiv", "http://export.arxiv.org/api/query"),
            make_http(lambda request: httpx.Response(500)),
        )
        with pytest.raises(SourceError):
            adapter.fetch(SINCE)


# ---------- 通用 HTML ----------

HTML_SAMPLE = """
<html><body><ul>
  <li><a href="/news/first">
      <div class="meta"><time datetime="2026-09-25T02:00:00Z">Sep 25, 2026</time>
      <span class="subj">Science</span></div>
      <span class="listItem__title"> First news title </span>
  </a></li>
  <li><a href="/other/skip"><span class="listItem__title">Should be skipped</span></a></li>
  <li><a href="/news/old">
      <div class="meta"><time datetime="2026-09-01T02:00:00Z">Sep 1, 2026</time></div>
      <span class="listItem__title">Old news title</span>
  </a></li>
</ul></body></html>
"""

HTML_EXTRA = {
    "item_selector": "li:has(a[href^='/news/'])",
    "link_selector": "a[href]",
    "link_pattern": "^/news/",
    "title_selector": "[class*='title']",
    "time_selector": "time",
    "time_attr": "datetime",
}


class TestHtmlAdapter:
    def _adapter(self, html: str = HTML_SAMPLE) -> HtmlAdapter:
        return HtmlAdapter(
            make_source("html", "https://example.com/news", **HTML_EXTRA),
            make_http(lambda request: httpx.Response(200, text=html)),
        )

    def test_parses_list_with_selectors(self) -> None:
        items = self._adapter().fetch(SINCE)
        assert len(items) == 1
        item = items[0]
        assert item.title == "First news title"
        assert item.url == "https://example.com/news/first"  # 相对链接已补全
        assert item.published_at == datetime(2026, 9, 25, 2, 0, tzinfo=UTC)

    def test_link_pattern_filters(self) -> None:
        items = self._adapter().fetch(datetime(2026, 1, 1, tzinfo=UTC))
        urls = [item.url for item in items]
        assert all("/news/" in url for url in urls)
        assert len(items) == 2  # first + old（窗口放宽后都保留）

    def test_selector_matching_nothing_raises(self) -> None:
        adapter = HtmlAdapter(
            make_source("html", "https://example.com/news", item_selector="article.nope"),
            make_http(lambda request: httpx.Response(200, text=HTML_SAMPLE)),
        )
        with pytest.raises(SourceError, match="选择器未匹配"):
            adapter.fetch(SINCE)

    def test_title_falls_back_to_link_text(self) -> None:
        extra = dict(HTML_EXTRA)
        extra.pop("title_selector")
        adapter = HtmlAdapter(
            make_source("html", "https://example.com/news", **extra),
            make_http(lambda request: httpx.Response(200, text=HTML_SAMPLE)),
        )
        items = adapter.fetch(SINCE)
        assert "First news title" in items[0].title


# ---------- Hugging Face 论文 ----------

HF_SAMPLE = [
    {
        "paper": {
            "id": "2609.11111",
            "title": "Featured Paper",
            "summary": "Paper abstract.",
            "upvotes": 42,
            "authors": [{"name": "Carol"}, {"name": "Dave"}],
            "publishedAt": "2026-09-01T00:00:00.000Z",
        },
        "publishedAt": "2026-09-25T01:00:00.000Z",
    },
    {"paper": {"id": "", "title": "Missing id"}},
    {"not_a_paper": True},
]


class TestHfPapersAdapter:
    def _adapter(self, payload: object) -> HfPapersAdapter:
        return HfPapersAdapter(
            make_source("hf_papers", "https://huggingface.co/api/daily_papers"),
            make_http(lambda request: httpx.Response(200, text=json.dumps(payload))),
        )

    def test_parses_featured_papers(self) -> None:
        items = self._adapter(HF_SAMPLE).fetch(SINCE)
        assert len(items) == 1
        item = items[0]
        assert item.title == "Featured Paper"
        assert item.url == "https://huggingface.co/papers/2609.11111"
        assert item.extra["kind"] == "paper"
        assert item.extra["upvotes"] == 42
        assert item.extra["author_count"] == 2
        # 时间用"入选日"而非论文投稿日，保证能进入当日窗口
        assert item.published_at == datetime(2026, 9, 25, 1, 0, tzinfo=UTC)

    def test_non_list_payload_raises(self) -> None:
        with pytest.raises(SourceError, match="不是列表"):
            self._adapter({"unexpected": True}).fetch(SINCE)

    def test_empty_list_returns_empty(self) -> None:
        assert self._adapter([]).fetch(SINCE) == []

    def test_http_failure_raises(self) -> None:
        adapter = HfPapersAdapter(
            make_source("hf_papers", "https://huggingface.co/api/daily_papers"),
            make_http(lambda request: httpx.Response(429)),
        )
        with pytest.raises(SourceError):
            adapter.fetch(SINCE)
