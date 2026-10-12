"""通用 RSS 适配器：一个模块覆盖大部分媒体源。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import feedparser

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError, entry_datetime
from aihot.utils import clean_html

logger = logging.getLogger("aihot.sources.rss")


class RssAdapter(SourceAdapter):
    """解析标准 RSS/Atom 源。新增同类源只需在配置里加一行。"""

    type = "rss"

    def fetch(self, since: datetime) -> list[RawItem]:
        result = self.http.get(self.source.url)
        if not result.ok:
            raise SourceError(f"请求失败: {result.error or result.status}")
        feed = feedparser.parse(result.text)
        entries = list(feed.entries or [])
        if not entries:
            if feed.get("bozo"):
                raise SourceError(f"RSS 解析失败: {feed.get('bozo_exception')}")
            if not feed.get("version"):
                raise SourceError("响应不是有效的 RSS/Atom 内容")
            return []  # 合法但当前为空的源
        if feed.get("bozo"):
            logger.warning(
                "源 %s 解析存在告警（仍使用 %d 条）: %s",
                self.id,
                len(entries),
                feed.get("bozo_exception"),
            )
        items: list[RawItem] = []
        for entry in entries:
            published = self._entry_datetime(entry)
            if published is not None and published < since:
                continue
            item = self.build_item(
                title=clean_html(str(entry.get("title") or "")),
                url=str(entry.get("link") or ""),
                published=published,
                summary=clean_html(str(entry.get("summary") or entry.get("description") or "")),
            )
            if item is not None:
                items.append(item)
        return items

    @staticmethod
    def _entry_datetime(entry: Any) -> datetime | None:
        """优先用 feedparser 解析好的结构，其次回退到字符串解析。"""
        return entry_datetime(entry)
