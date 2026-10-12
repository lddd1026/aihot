"""RSS 适配器单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from aihot.config import SourceConfig
from aihot.net import HttpClient
from aihot.sources.base import SourceError
from aihot.sources.rss import RssAdapter

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>测试源</title>
    <item>
      <title>新条目</title>
      <link>https://example.com/new?utm_source=rss</link>
      <pubDate>Wed, 23 Sep 2026 08:00:00 +0000</pubDate>
      <description><![CDATA[<p>摘要 <b>内容</b></p>]]></description>
    </item>
    <item>
      <title>旧条目</title>
      <link>https://example.com/old</link>
      <pubDate>Tue, 01 Sep 2026 08:00:00 +0000</pubDate>
    </item>
    <item>
      <title>无日期条目</title>
      <link>https://example.com/no-date</link>
    </item>
    <item>
      <title></title>
      <link>https://example.com/no-title</link>
    </item>
  </channel>
</rss>
"""

SINCE = datetime(2026, 9, 22, tzinfo=UTC)


def make_adapter(handler) -> RssAdapter:
    source = SourceConfig(
        id="t1",
        name="测试源",
        type="rss",
        url="https://example.com/feed",
        enabled=True,
        weight=1.0,
    )
    return RssAdapter(source, HttpClient(transport=httpx.MockTransport(handler), backoff=0.0))


def test_fetch_filters_window_and_normalizes() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text=RSS_SAMPLE))
    items = adapter.fetch(SINCE)
    urls = [item.url for item in items]
    assert "https://example.com/new" in urls  # 跟踪参数已去除
    assert "https://example.com/old" not in urls  # 窗口外被过滤
    assert "https://example.com/no-date" in urls  # 无日期条目保留
    assert "https://example.com/no-title" not in urls  # 无标题被丢弃
    assert len(items) == 2
    new_item = next(item for item in items if item.url.endswith("/new"))
    assert new_item.title == "新条目"
    assert new_item.raw_summary == "摘要 内容"
    assert new_item.lang == "zh"
    assert new_item.source_id == "t1"
    adapter.http.close()


def test_http_failure_raises_source_error() -> None:
    adapter = make_adapter(lambda request: httpx.Response(500))
    with pytest.raises(SourceError):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_broken_feed_raises_source_error() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text="<html>not a feed</html>"))
    with pytest.raises(SourceError):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_garbage_feed_raises_source_error() -> None:
    adapter = make_adapter(lambda request: httpx.Response(200, text="garbage text"))
    with pytest.raises(SourceError):
        adapter.fetch(SINCE)
    adapter.http.close()


def test_valid_but_empty_feed_returns_empty_list() -> None:
    empty_feed = (
        '<?xml version="1.0"?>'
        '<rss version="2.0"><channel><title>空源</title></channel></rss>'
    )
    adapter = make_adapter(lambda request: httpx.Response(200, text=empty_feed))
    assert adapter.fetch(SINCE) == []
    adapter.http.close()
