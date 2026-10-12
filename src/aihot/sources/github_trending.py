"""GitHub 当日热榜（官方 Trending 页，HTML 解析）。

与 `github.py`（官方 Search API：近 N 天创建 + star 最多）互补：
- 本适配器抓「当日全站热榜」，反映"今天正在快速增长"的项目
- 官方页面没有结构化接口，需要解析 HTML；页面改版的风险由「单源失败隔离」兜底
- 页面偶发 429/403/超时属预期（有重试 + 隔离，不影响整体日报）

页面结构（2026-10 实测）：
    article.Box-row
      ├─ h2 a[href]                        → /owner/repo
      ├─ p                                 → 描述
      ├─ span[itemprop=programmingLanguage] → 语言
      ├─ a[href$="/stargazers"]            → 总 star
      ├─ a[href$="/forks"]                 → fork
      └─ span.float-sm-right               → "25,599 stars today"
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from aihot.models import RawItem
from aihot.sources.base import SourceAdapter, SourceError
from aihot.utils import clean_html, now_utc

ROW_SELECTOR = "article.Box-row"
TITLE_SELECTOR = "h2 a[href]"
DESC_SELECTOR = "p"
LANG_SELECTOR = '[itemprop="programmingLanguage"]'
STARS_SELECTOR = 'a[href$="/stargazers"]'
FORKS_SELECTOR = 'a[href$="/forks"]'
TODAY_SELECTOR = "span.float-sm-right"
REPO_HREF = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
TODAY_RE = re.compile(r"([\d,]+)\s*stars?\s*today", re.IGNORECASE)
# 这些"两段路径"不是仓库（改版/异常时可能出现，防御性排除）
RESERVED_OWNERS = frozenset(
    {
        "sponsors", "orgs", "settings", "login", "signup", "features", "topics",
        "collections", "marketplace", "apps", "about", "pricing", "security",
        "enterprise", "contact", "site", "trending", "explore", "notifications",
        "new", "search",
    }
)

DEFAULT_PERIOD = "daily"
VALID_PERIODS = frozenset({"daily", "weekly", "monthly"})
DEFAULT_LIMIT = 12
MAX_LIMIT = 25
SUMMARY_LIMIT = 400


class GithubTrendingAdapter(SourceAdapter):
    """抓取 GitHub Trending 页「当日热榜」。"""

    type = "github_trending"

    def fetch(self, since: datetime) -> list[RawItem]:  # noqa: ARG002 - 热榜无发布时间，统一用"发现时间"
        period = str(self.source.extra.get("period") or DEFAULT_PERIOD).strip().lower()
        if period not in VALID_PERIODS:
            period = DEFAULT_PERIOD
        limit = max(1, min(MAX_LIMIT, int(self.source.extra.get("limit") or DEFAULT_LIMIT)))

        result = self.http.get(self.source.url, params={"since": period})
        if not result.ok:
            raise SourceError(f"请求失败: {result.error or result.status}")

        soup = BeautifulSoup(result.text, "html.parser")
        rows = soup.select(ROW_SELECTOR)
        if not rows:
            raise SourceError(f"页面未匹配到趋势条目（{ROW_SELECTOR}），可能被拦截或已改版")

        items: list[RawItem] = []
        for row in rows:
            if not isinstance(row, Tag):
                continue
            item = self._build_row(row, period)
            if item is not None:
                items.append(item)
        if not items:
            raise SourceError("趋势条目全部解析失败（页面结构可能已变化）")
        # limit 语义 = 解析成功的前 N 条（而不是"页面前 N 行"，避免坏行占用名额）
        return items[:limit]

    # ---------- 内部 ----------

    def _build_row(self, row: Tag, period: str) -> RawItem | None:
        link = row.select_one(TITLE_SELECTOR)
        href = str(link.get("href") or "").strip() if isinstance(link, Tag) else ""
        match = REPO_HREF.match(href)
        if not match or match.group(1).lower() in RESERVED_OWNERS:
            return None
        full_name = f"{match.group(1)}/{match.group(2)}"
        url = f"https://github.com/{full_name}"

        description = clean_html(_text(row, DESC_SELECTOR))
        language = _text(row, LANG_SELECTOR)
        stars = _parse_count(_text(row, STARS_SELECTOR))
        forks = _parse_count(_text(row, FORKS_SELECTOR))
        stars_today = _parse_stars_today(_text(row, TODAY_SELECTOR))

        summary_parts = [description] if description else []
        meta_bits = []
        if language:
            meta_bits.append(f"语言 {language}")
        if stars:
            meta_bits.append(f"star {stars:,}")
        if stars_today:
            meta_bits.append(f"今日 +{stars_today:,}")
        if forks:
            meta_bits.append(f"fork {forks:,}")
        if meta_bits:
            summary_parts.append("｜".join(meta_bits))

        return self.build_item(
            title=full_name,
            url=url,
            # 热榜的新闻价值是"今天正在快速增长"，用发现时间进入当日窗口
            published=now_utc(),
            summary=" ｜ ".join(summary_parts)[:SUMMARY_LIMIT],
            extra={
                "kind": "repo",
                "stars": stars,
                "stars_today": stars_today,
                "upvotes": stars,  # 社区热度信号，与 github.py 保持一致
                "forks": forks,
                "language": language,
                "trending_period": period,
                "source_page": "trending",
            },
        )


def _text(node: Tag, selector: str) -> str:
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if isinstance(found, Tag) else ""


def _parse_count(text: str) -> int:
    """'124,887' → 124887；解析失败返回 0。"""
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else 0


def _parse_stars_today(text: str) -> int:
    """'25,599 stars today' → 25599。"""
    match = TODAY_RE.search(text or "")
    return _parse_count(match.group(1)) if match else 0
