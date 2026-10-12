"""日报组装：从排序结果中选出 Top N、简讯与统计。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from aihot.models import Digest, NewsItem
from aihot.pipeline.rank import RankedItem

BRIEF_COUNT = 12
NOISE_CATEGORY = "其他"
NOISE_MAX_IMPORTANCE = 15
# 开发者向日报不收录的分类（硬件/商业/政策类会被 LLM 归入这些分类）
EXCLUDED_CATEGORIES = frozenset({"融资商业", "政策监管", "行业动态"})
PAPER_KIND = "paper"
PAPER_LIMIT = 2
REPO_KIND = "repo"
MAX_REPO_ITEMS = 4  # GitHub 项目在「今日必读」中的上限（其余进简讯，避免挤掉新闻）


@dataclass(slots=True)
class DigestStats:
    """日报页脚统计。"""

    scanned: int = 0
    kept: int = 0
    clusters: int = 0
    selected: int = 0
    brief_count: int = 0
    sources: int = 0
    cost_cny: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "scanned": self.scanned,
            "kept": self.kept,
            "clusters": self.clusters,
            "selected": self.selected,
            "briefs": self.brief_count,
            "sources": self.sources,
            "cost_cny": round(self.cost_cny, 4),
        }
        payload.update(self.extra)
        return payload


def is_publishable(item: NewsItem) -> bool:
    """是否适合进入日报：排除被过滤项、主题不符分类与明显噪音（非 AI/纯营销）。"""
    if item.category == "过滤" or item.category in EXCLUDED_CATEGORIES:
        return False
    if item.category == NOISE_CATEGORY and item.importance <= NOISE_MAX_IMPORTANCE:
        return False
    return bool((item.cn_title or item.title).strip())


def is_paper(item: NewsItem) -> bool:
    """论文类条目（arXiv / Hugging Face）单独成节，不占用 Top 名额。"""
    return str(item.extra.get("kind") or "") == PAPER_KIND


def paper_note(item: NewsItem) -> str:
    """论文来源标注（如 "arXiv · cs.AI"）。"""
    if not is_paper(item):
        return ""
    categories = item.extra.get("categories") or []
    primary = str(categories[0]) if categories else ""
    source = "arXiv" if "arxiv" in item.source_id.lower() else "Hugging Face"
    return f"{source} · {primary}" if primary else source


def is_repo(item: NewsItem) -> bool:
    """GitHub 热门项目（用于控制精选区占比）。"""
    return str(item.extra.get("kind") or "") == REPO_KIND


def compose_digest(
    ranked: list[RankedItem],
    *,
    digest_date: date,
    top_n: int,
    summary: str = "",
    stats: DigestStats | None = None,
    brief_count: int = BRIEF_COUNT,
    paper_limit: int = PAPER_LIMIT,
    max_repo_items: int = MAX_REPO_ITEMS,
) -> Digest:
    """组装一期日报：论文精选 + Top N 精选 + 其他值得一看 + 统计。

    精选区按排名挑选，但对 GitHub 项目数量限额（超出的进简讯），保证新闻与项目平衡。
    """
    publishable = [entry for entry in ranked if is_publishable(entry.item)]
    paper_entries = [entry for entry in publishable if is_paper(entry.item)][: max(0, paper_limit)]
    rest = [entry for entry in publishable if not is_paper(entry.item)]

    top_entries: list[RankedItem] = []
    brief_pool: list[RankedItem] = []
    repo_used = 0
    for entry in rest:
        is_repo_entry = is_repo(entry.item)
        if len(top_entries) < max(0, top_n) and (not is_repo_entry or repo_used < max_repo_items):
            top_entries.append(entry)
            if is_repo_entry:
                repo_used += 1
        else:
            brief_pool.append(entry)
    brief_entries = brief_pool[: max(0, brief_count)]

    resolved = stats or DigestStats()
    resolved.selected = len(top_entries)
    resolved.brief_count = len(brief_entries)

    payload = resolved.as_dict()
    payload["papers"] = len(paper_entries)

    return Digest(
        digest_date=digest_date,
        summary=summary.strip(),
        top_items=[entry.item for entry in top_entries],
        papers=[entry.item for entry in paper_entries],
        briefs=[entry.item for entry in brief_entries],
        stats=payload,
        files={},
    )


def item_links(item: NewsItem) -> list[dict[str, str]]:
    """条目关联的原始报道链接（多源报道时不止一条）。"""
    links = item.extra.get("links")
    if isinstance(links, list) and links:
        cleaned = [
            {
                "source_id": str(link.get("source_id") or ""),
                "title": str(link.get("title") or ""),
                "url": str(link.get("url") or ""),
            }
            for link in links
            if isinstance(link, dict) and link.get("url")
        ]
        if cleaned:
            return cleaned
    return [{"source_id": item.source_id, "title": item.title, "url": item.url}]


def source_count(item: NewsItem) -> int:
    sources = item.extra.get("source_ids")
    if isinstance(sources, list) and sources:
        return len(sources)
    return len({link["source_id"] for link in item_links(item)})


def mention_count(item: NewsItem) -> int:
    try:
        return max(1, int(item.extra.get("mention_count") or 1))
    except (TypeError, ValueError):
        return 1


def date_display(digest_date: date) -> str:
    """中文日期展示（含星期）。"""
    weekdays = "一二三四五六日"
    return (
        f"{digest_date.year}年{digest_date.month}月"
        f"{digest_date.day}日 星期{weekdays[digest_date.weekday()]}"
    )


def issue_number(digest_date: date) -> int:
    """第 N 期（以项目上线日 2026-09-23 为第 1 期）。"""
    anchor = date(2026, 9, 23)
    return max(1, (digest_date - anchor).days + 1)


def local_date(now: datetime, timezone_name: str) -> date:
    from zoneinfo import ZoneInfo

    return now.astimezone(ZoneInfo(timezone_name)).date()
