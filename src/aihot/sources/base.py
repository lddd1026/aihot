"""采集层：适配器接口与公共逻辑。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urljoin, urlsplit

from aihot.config import SourceConfig
from aihot.models import RawItem
from aihot.net import HttpClient
from aihot.utils import detect_lang, normalize_url, now_utc, parse_datetime

logger = logging.getLogger("aihot.sources")


def is_http_url(url: str) -> bool:
    """仅接受 http(s)、带主机名且不含空白的绝对地址。"""
    if not url or any(char.isspace() for char in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def absolutize_url(base_url: str, url: str) -> str:
    """相对链接按源地址补全；无法解析时原样返回（交给白名单丢弃）。"""
    candidate = (url or "").strip()
    if not candidate:
        return ""
    try:
        has_scheme = bool(urlsplit(candidate).scheme)
    except ValueError:
        return candidate
    if has_scheme:
        return candidate
    return urljoin(base_url, candidate)


def entry_datetime(entry: Any) -> datetime | None:
    """从 feedparser 条目中取发布时间（优先结构化字段，其次字符串解析）。"""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is not None:
        return datetime(*parsed[:6], tzinfo=UTC)
    return parse_datetime(entry.get("published") or entry.get("updated"))


class SourceError(Exception):
    """源采集失败（由调度层隔离，不影响其他源）。"""


class SourceAdapter(ABC):
    """信息源适配器基类。

    约束：适配器只产出 RawItem，不写库、不推送、不调 LLM。
    """

    type: ClassVar[str] = ""

    def __init__(self, source: SourceConfig, http: HttpClient) -> None:
        self.source = source
        self.http = http

    @property
    def id(self) -> str:
        return self.source.id

    @property
    def name(self) -> str:
        return self.source.name

    @abstractmethod
    def fetch(self, since: datetime) -> list[RawItem]:
        """抓取 since 之后的条目，归一化为 RawItem 列表。"""

    def build_item(
        self,
        title: str,
        url: str,
        published: datetime | None,
        *,
        summary: str = "",
        extra: dict[str, Any] | None = None,
    ) -> RawItem | None:
        """归一化并组装 RawItem；标题无效或链接不是 http(s) 绝对地址时返回 None。"""
        clean_title = (title or "").strip()
        candidate = absolutize_url(self.source.url, url)
        normalized = normalize_url(candidate)
        if not clean_title or not is_http_url(normalized):
            if candidate:
                logger.debug("丢弃无效链接（源 %s）: %s", self.id, candidate[:120])
            return None
        return RawItem(
            source_id=self.id,
            title=clean_title,
            url=normalized,
            published_at=published or now_utc(),
            raw_summary=(summary or "").strip(),
            lang=detect_lang(clean_title),
            extra=dict(extra or {}),
        )
