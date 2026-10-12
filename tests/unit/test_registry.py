"""信息源注册表与并发调度单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx

from aihot.config import SourceConfig, load_config
from aihot.models import RawItem
from aihot.net import HttpClient
from aihot.sources.base import SourceAdapter
from aihot.sources.registry import build_adapters, collect_all

CONFIG_TEXT = """
app:
  timezone: Asia/Shanghai
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
push:
  wecom:
    enabled: false
sources:
  - id: rss1
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
  - id: html1
    type: html
    url: https://example.com/
    enabled: true
    weight: 1.0
  - id: off1
    type: rss
    url: https://example.com/off
    enabled: false
    weight: 1.0
"""


class FakeAdapter(SourceAdapter):
    """测试用适配器：可控返回或抛错。"""

    type = "fake"

    def __init__(
        self,
        source: SourceConfig,
        http: HttpClient,
        *,
        items: list[RawItem] | None = None,
        error: str = "",
    ) -> None:
        super().__init__(source, http)
        self._items = items or []
        self._error = error

    def fetch(self, since: datetime) -> list[RawItem]:
        if self._error:
            raise RuntimeError(self._error)
        return self._items


def make_source(source_id: str, *, source_type: str = "rss", enabled: bool = True) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        name=source_id,
        type=source_type,
        url="https://example.com/feed",
        enabled=enabled,
        weight=1.0,
    )


def make_item(source_id: str, url: str) -> RawItem:
    return RawItem(
        source_id=source_id,
        title="标题",
        url=url,
        published_at=datetime.now(UTC),
    )


def test_collect_all_isolates_failures() -> None:
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    good = FakeAdapter(make_source("good"), http, items=[make_item("good", "https://a.com/1")])
    bad = FakeAdapter(make_source("bad"), http, error="boom")
    result = collect_all([good, bad], datetime.now(UTC))
    assert len(result.items) == 1
    assert result.ok_count == 1
    assert len(result.failed) == 1
    assert "boom" in result.failed[0].error
    assert result.failed[0].source_id == "bad"
    http.close()


def test_collect_all_with_no_adapters() -> None:
    result = collect_all([], datetime.now(UTC))
    assert result.items == []
    assert result.stats == []


def test_build_adapters_includes_implemented_types(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    cfg = load_config(path)
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    adapters, skipped = build_adapters(cfg, http)
    # html 类型已实现（通用选择器适配器），因此不再被跳过
    assert [adapter.id for adapter in adapters] == ["rss1", "html1"]
    assert skipped == []
    http.close()


def test_build_adapters_skips_unimplemented_type(tmp_path: Path, monkeypatch) -> None:
    """保留"未实现类型"的降级路径：把某类型从注册表里摘掉时应被跳过而不是崩溃。"""
    from aihot.sources import registry

    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    cfg = load_config(path)
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    monkeypatch.setitem(registry.ADAPTER_TYPES, "html", None)
    adapters, skipped = registry.build_adapters(cfg, http)
    assert [adapter.id for adapter in adapters] == ["rss1"]
    assert len(skipped) == 1 and "html1" in skipped[0]
    http.close()


def test_build_adapters_only_filter(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    cfg = load_config(path)
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    adapters, _ = build_adapters(cfg, http, only={"html1"})
    assert [adapter.id for adapter in adapters] == ["html1"]
    http.close()

    adapters, skipped = build_adapters(cfg, http, only={"no_such_source"})
    assert adapters == []
    assert skipped == []
    http.close()
