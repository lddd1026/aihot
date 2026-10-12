"""数据契约：各层之间流动的结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from aihot.utils import parse_datetime, url_hash


def _utcnow() -> datetime:
    return datetime.now(UTC)

@dataclass(slots=True)
class RawItem:
    """采集层输出的原始条目。"""

    source_id: str
    title: str
    url: str
    published_at: datetime
    raw_summary: str = ""
    lang: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=_utcnow)

    @property
    def url_hash(self) -> str:
        return url_hash(self.url)


@dataclass(slots=True)
class NewsItem(RawItem):
    """AI 加工后的条目。"""

    cluster_id: int | None = None
    category: str = "其他"
    importance: int = 0
    cn_title: str = ""
    cn_summary: str = ""


@dataclass(slots=True)
class Cluster:
    """事件簇：同一事件的多源报道合并结果。"""

    cluster_id: int
    canonical_title: str
    item_hashes: list[str]
    source_ids: list[str]
    mention_count: int
    heat_score: float = 0.0
    first_seen: datetime = field(default_factory=_utcnow)


@dataclass(slots=True)
class Digest:
    """一期日报。"""

    digest_date: date
    summary: str
    top_items: list[NewsItem] = field(default_factory=list)
    papers: list[NewsItem] = field(default_factory=list)
    briefs: list[NewsItem] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RunRecord:
    """一次运行的记录。"""

    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"  # running / ok / failed / partial
    stage_stats: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def news_item_to_dict(item: NewsItem) -> dict[str, Any]:
    """NewsItem → 可 JSON 序列化的字典（日报存档用）。"""
    return {
        "source_id": item.source_id,
        "title": item.title,
        "url": item.url,
        "published_at": item.published_at.astimezone(UTC).isoformat(),
        "raw_summary": item.raw_summary,
        "lang": item.lang,
        "extra": item.extra,
        "cluster_id": item.cluster_id,
        "category": item.category,
        "importance": item.importance,
        "cn_title": item.cn_title,
        "cn_summary": item.cn_summary,
    }


def news_item_from_dict(data: dict[str, Any]) -> NewsItem:
    """字典 → NewsItem（存档恢复用，字段缺失时给出安全默认值）。"""
    published = parse_datetime(data.get("published_at")) or datetime.now(UTC)
    return NewsItem(
        source_id=str(data.get("source_id") or ""),
        title=str(data.get("title") or ""),
        url=str(data.get("url") or ""),
        published_at=published,
        raw_summary=str(data.get("raw_summary") or ""),
        lang=str(data.get("lang") or ""),
        extra=dict(data.get("extra") or {}),
        cluster_id=data.get("cluster_id"),
        category=str(data.get("category") or "其他"),
        importance=int(data.get("importance") or 0),
        cn_title=str(data.get("cn_title") or ""),
        cn_summary=str(data.get("cn_summary") or ""),
    )
