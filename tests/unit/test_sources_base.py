"""适配器公共逻辑单元测试（链接归一化与白名单）。"""

from __future__ import annotations

import httpx

from aihot.config import SourceConfig
from aihot.net import HttpClient
from aihot.sources.base import is_http_url
from aihot.sources.rss import RssAdapter


def make_adapter(source_url: str = "https://example.com/feed") -> RssAdapter:
    source = SourceConfig(
        id="t1",
        name="测试源",
        type="rss",
        url=source_url,
        enabled=True,
        weight=1.0,
    )
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    return RssAdapter(source, http)


class TestIsHttpUrl:
    def test_valid(self) -> None:
        assert is_http_url("https://example.com/a")
        assert is_http_url("http://example.com")

    def test_rejects_hostless(self) -> None:
        assert not is_http_url("https:///path")

    def test_rejects_non_http_scheme(self) -> None:
        assert not is_http_url("javascript:alert(1)")
        assert not is_http_url("mailto:a@b.com")
        assert not is_http_url("ftp://example.com/x")

    def test_rejects_whitespace(self) -> None:
        assert not is_http_url("https://example.com/not a url")

    def test_rejects_invalid_ipv6(self) -> None:
        assert not is_http_url("http://[::1")


class TestBuildItemLinks:
    def test_relative_link_resolved_against_source(self) -> None:
        item = make_adapter().build_item("标题", "/post/1", None)
        assert item is not None
        assert item.url == "https://example.com/post/1"

    def test_protocol_relative_link_resolved(self) -> None:
        item = make_adapter().build_item("标题", "//cdn.example.com/x", None)
        assert item is not None
        assert item.url == "https://cdn.example.com/x"

    def test_javascript_scheme_dropped(self) -> None:
        assert make_adapter().build_item("标题", "javascript:alert(1)", None) is None

    def test_mailto_dropped(self) -> None:
        assert make_adapter().build_item("标题", "mailto:a@b.com", None) is None

    def test_hostless_dropped(self) -> None:
        assert make_adapter().build_item("标题", "https:///path", None) is None

    def test_garbage_dropped(self) -> None:
        assert make_adapter().build_item("标题", "not a url", None) is None

    def test_empty_title_dropped(self) -> None:
        assert make_adapter().build_item("", "https://example.com/a", None) is None
