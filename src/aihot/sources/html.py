"""通用 HTML 列表适配器：用配置里的 CSS 选择器解析网页列表。

新增同类站点只需在 config.yaml 里加一段配置，不用写代码：
    extra:
      item_selector: "li"                # 列表项
      link_selector: "a[href]"           # 条目链接
      link_pattern: "^/news/"            # 只接受匹配该正则的链接（可选）
      title_selector: "[class*='title']" # 标题（可选，缺省用链接文本）
      time_selector: "time"              # 时间（可选）
      time_attr: "datetime"              # 时间属性（可选，缺省取文本）
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError
from aihot.utils import clean_html, parse_datetime

DEFAULT_ITEM_SELECTOR = "li, article"
DEFAULT_LINK_SELECTOR = "a[href]"
DEFAULT_TIME_SELECTOR = "time"
MAX_SUMMARY_CHARS = 400


class HtmlAdapter(SourceAdapter):
    """按配置选择器抓取网页列表（如官方博客的 News 页）。"""

    type = "html"

    def fetch(self, since: datetime) -> list[RawItem]:
        result = self.http.get(self.source.url)
        if not result.ok:
            raise SourceError(f"请求失败: {result.error or result.status}")
        soup = BeautifulSoup(result.text, "html.parser")

        extra = self.source.extra
        item_selector = str(extra.get("item_selector") or DEFAULT_ITEM_SELECTOR)
        link_selector = str(extra.get("link_selector") or DEFAULT_LINK_SELECTOR)
        title_selector = str(extra.get("title_selector") or "")
        time_selector = str(extra.get("time_selector") or DEFAULT_TIME_SELECTOR)
        time_attr = str(extra.get("time_attr") or "")
        link_pattern = str(extra.get("link_pattern") or "")

        nodes = soup.select(item_selector)
        if not nodes:
            raise SourceError(f"选择器未匹配到任何条目：{item_selector}")

        items: list[RawItem] = []
        matched_anchors = 0
        for node in nodes:
            anchor = node.select_one(link_selector)
            if not isinstance(node, Tag) or not isinstance(anchor, Tag):
                continue
            matched_anchors += 1
            href = str(anchor.get("href") or "").strip()
            if not href or (link_pattern and not re.search(link_pattern, href)):
                continue
            title = self._text(node, title_selector) or anchor.get_text(" ", strip=True)
            published = self._published(node, time_selector, time_attr)
            summary = ""
            if self.source.extra.get("summary_selector"):
                summary = clean_html(self._text(node, str(self.source.extra["summary_selector"])))
            item = self.build_item(
                title=clean_html(title)[:200],
                url=href,
                published=published,
                summary=summary[:MAX_SUMMARY_CHARS],
            )
            if item is None:
                continue
            if published is not None and published < since:
                continue
            items.append(item)
        if matched_anchors == 0:
            raise SourceError(f"link_selector 未匹配到任何链接：{link_selector}")
        return items

    @staticmethod
    def _text(node: Tag, selector: str) -> str:
        if not selector:
            return ""
        found = node.select_one(selector)
        return found.get_text(" ", strip=True) if isinstance(found, Tag) else ""

    @staticmethod
    def _published(node: Tag, selector: str, attr: str) -> datetime | None:
        if not selector:
            return None
        found = node.select_one(selector)
        if not isinstance(found, Tag):
            return None
        if attr:
            value = found.get(attr)
            parsed = parse_datetime(value) if value else None
            if parsed is not None:
                return parsed
        return parse_datetime(found.get_text(" ", strip=True))
