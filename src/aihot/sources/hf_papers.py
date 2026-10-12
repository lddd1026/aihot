"""Hugging Face Daily Papers：社区投票选出的当日热门论文。

注意：条目时间是"入选日"（列表本身按天更新），论文自身的投稿日期放在 extra 里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError
from aihot.utils import clean_html, now_utc, parse_datetime

DEFAULT_LIMIT = 30
ABSTRACT_LIMIT = 600
PAPERS_URL = "https://huggingface.co/papers/{paper_id}"


class HfPapersAdapter(SourceAdapter):
    """抓取 HF 每日论文榜（已按社区热度排序）。"""

    type = "hf_papers"

    def fetch(self, since: datetime) -> list[RawItem]:  # noqa: ARG002 - 榜单本身即当日
        limit = int(self.source.extra.get("limit") or DEFAULT_LIMIT)
        payload, result = self.http.get_json(
            self.source.url, params={"limit": min(max(limit, 1), 100)}
        )
        if payload is None:
            raise SourceError(f"请求失败: {result.error or result.status}")
        if not isinstance(payload, list):
            raise SourceError("响应结构异常（不是列表）")
        if not payload:
            return []

        items: list[RawItem] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            paper = entry.get("paper") if isinstance(entry.get("paper"), dict) else {}
            paper_id = str(paper.get("id") or "").strip()
            title = str(paper.get("title") or "").strip()
            if not paper_id or not title:
                continue
            featured = parse_datetime(entry.get("publishedAt")) or now_utc()
            authors = [
                str(author.get("name") or "")
                for author in (paper.get("authors") or [])
                if isinstance(author, dict)
            ]
            item = self.build_item(
                title=title,
                url=PAPERS_URL.format(paper_id=paper_id),
                published=featured,
                summary=clean_html(str(paper.get("summary") or ""))[:ABSTRACT_LIMIT],
                extra={
                    "kind": "paper",
                    "paper_id": paper_id,
                    "upvotes": _as_int(paper.get("upvotes")),
                    "authors": authors[:6],
                    "author_count": len(authors),
                    "paper_published_at": str(paper.get("publishedAt") or ""),
                },
            )
            if item is not None:
                items.append(item)
        return items


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
