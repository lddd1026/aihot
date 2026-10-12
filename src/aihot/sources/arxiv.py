"""arXiv 论文源：官方 Atom API，按分类取最新投稿。

约定：条目在 extra 中标记 `kind="paper"`，日报会把它归入「论文精选」小节。
"""

from __future__ import annotations

from datetime import datetime

import feedparser

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError, entry_datetime
from aihot.utils import clean_html

DEFAULT_CATEGORIES = ("cs.AI", "cs.CL", "cs.LG")
DEFAULT_MAX_RESULTS = 50
ABSTRACT_LIMIT = 600


class ArxivAdapter(SourceAdapter):
    """按分类抓取最新论文（默认 cs.AI / cs.CL / cs.LG）。"""

    type = "arxiv"

    def fetch(self, since: datetime) -> list[RawItem]:
        categories = tuple(self.source.extra.get("categories") or DEFAULT_CATEGORIES)
        max_results = int(self.source.extra.get("max_results") or DEFAULT_MAX_RESULTS)
        params = {
            "search_query": " OR ".join(f"cat:{name}" for name in categories),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(max(1, min(max_results, 200))),
        }
        result = self.http.get(self.source.url, params=params)
        if not result.ok:
            raise SourceError(f"请求失败: {result.error or result.status}")
        feed = feedparser.parse(result.text)
        entries = list(feed.entries or [])
        if not entries:
            raise SourceError(f"arXiv 返回为空（{feed.get('bozo_exception') or '无条目'}）")

        items: list[RawItem] = []
        for entry in entries:
            published = entry_datetime(entry)
            if published is not None and published < since:
                continue
            abstract = clean_html(str(entry.get("summary") or ""), limit=ABSTRACT_LIMIT)
            authors = [str(author.get("name") or "") for author in (entry.get("authors") or [])]
            categories_in_entry = [
                str(tag.get("term") or "") for tag in (entry.get("tags") or [])
            ]
            item = self.build_item(
                title=clean_html(str(entry.get("title") or "")),
                url=str(entry.get("link") or entry.get("id") or ""),
                published=published,
                summary=abstract,
                extra={
                    "kind": "paper",
                    "arxiv_id": str(entry.get("id") or "").rsplit("/", 1)[-1],
                    "authors": authors[:6],
                    "author_count": len(authors),
                    "categories": categories_in_entry[:5],
                },
            )
            if item is not None:
                items.append(item)
        return items
