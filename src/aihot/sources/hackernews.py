"""Hacker News 适配器：使用 Algolia API，保留 points/评论数作为热度信号。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError
from aihot.utils import clean_html, parse_datetime

HN_ITEM_URL = "https://news.ycombinator.com/item?id={object_id}"


class HackerNewsAdapter(SourceAdapter):
    """按“窗口内 + 点数达标”抓取 HN 故事，热度信号进 extra。"""

    type = "hackernews"

    def fetch(self, since: datetime) -> list[RawItem]:
        min_points = _as_int(self.source.extra.get("min_points"), default=30)
        limit = _as_int(self.source.extra.get("limit"), default=100)
        params: dict[str, Any] = {
            "tags": "story",
            "numericFilters": f"created_at_i>{int(since.timestamp())},points>{min_points}",
            "hitsPerPage": min(limit, 1000),
        }
        payload, result = self.http.get_json(self.source.url, params=params)
        if payload is None:
            raise SourceError(f"请求失败: {result.error or result.status}")
        if not isinstance(payload, dict) or "hits" not in payload:
            raise SourceError(f"响应缺少 hits 字段: {str(payload)[:120]}")
        hits = payload.get("hits") or []
        items: list[RawItem] = []
        for hit in hits:
            object_id = str(hit.get("objectID") or "")
            article_url = hit.get("url") or HN_ITEM_URL.format(object_id=object_id)
            published = self._hit_datetime(hit)
            if published is not None and published < since:
                continue
            item = self.build_item(
                title=str(hit.get("title") or ""),
                url=str(article_url),
                published=published,
                summary=clean_html(str(hit.get("story_text") or "")),
                extra={
                    "points": _as_int(hit.get("points")),
                    "num_comments": _as_int(hit.get("num_comments")),
                    "hn_id": object_id,
                    "hn_url": HN_ITEM_URL.format(object_id=object_id),
                    "author": str(hit.get("author") or ""),
                },
            )
            if item is not None:
                items.append(item)
        return items

    @staticmethod
    def _hit_datetime(hit: dict[str, Any]) -> datetime | None:
        created = parse_datetime(hit.get("created_at"))
        if created is not None:
            return created
        created_i = hit.get("created_at_i")
        if isinstance(created_i, int | float):
            return datetime.fromtimestamp(float(created_i), tz=UTC)
        return None


def _as_int(value: object, *, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
