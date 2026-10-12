"""日报组装单元测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from aihot.models import NewsItem
from aihot.pipeline.rank import rank_items
from aihot.presentation.compose import (
    DigestStats,
    compose_digest,
    date_display,
    is_publishable,
    issue_number,
    item_links,
    mention_count,
    source_count,
)

BASE_TIME = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


def make_news(
    title: str,
    *,
    category: str = "模型发布",
    importance: int = 60,
    mention: int = 1,
    source_ids: list[str] | None = None,
    links: list[dict] | None = None,
    hours_offset: float = 0.0,
) -> NewsItem:
    sources = source_ids or ["src_a"]
    extra = {"mention_count": mention, "source_ids": sources}
    if links is not None:
        extra["links"] = links
    return NewsItem(
        source_id=sources[0],
        title=title,
        url=f"https://example.com/{abs(hash(title)) % 100000}",
        published_at=BASE_TIME + timedelta(hours=hours_offset),
        cluster_id=1,
        category=category,
        importance=importance,
        cn_title=title,
        cn_summary="摘要",
        extra=extra,
    )


def make_repo(title: str, *, importance: int = 80) -> NewsItem:
    item = make_news(title, importance=importance, category="开源工具")
    item.extra["kind"] = "repo"
    item.extra["stars"] = 5000
    item.extra["upvotes"] = 5000
    return item


class TestPublishable:
    def test_filtered_category_excluded(self) -> None:
        assert is_publishable(make_news("x", category="过滤", importance=0)) is False

    def test_noise_excluded(self) -> None:
        assert is_publishable(make_news("x", category="其他", importance=15)) is False
        assert is_publishable(make_news("x", category="其他", importance=10)) is False

    def test_low_importance_other_excluded_but_high_kept(self) -> None:
        assert is_publishable(make_news("x", category="其他", importance=16)) is True

    def test_normal_kept(self) -> None:
        assert is_publishable(make_news("x")) is True

    def test_excluded_categories_not_publishable(self) -> None:
        """开发者向：硬件/商业/政策类（LLM 会归入这些分类）不进日报。"""
        for category in ("行业动态", "融资商业", "政策监管"):
            assert is_publishable(make_news("x", category=category, importance=95)) is False

    def test_dev_categories_publishable(self) -> None:
        for category in ("模型发布", "开源工具", "研究突破", "产品更新"):
            assert is_publishable(make_news("x", category=category, importance=60)) is True

    def test_excluded_categories_kept_out_of_digest(self) -> None:
        items = [
            make_news("技术新闻", category="模型发布", importance=90),
            make_news("收购消息", category="行业动态", importance=99),
            make_news("融资消息", category="融资商业", importance=99),
            make_news("监管消息", category="政策监管", importance=99),
        ]
        digest = compose_digest(rank_items(items), digest_date=date(2026, 10, 12), top_n=12)
        assert [item.cn_title for item in digest.top_items] == ["技术新闻"]
        assert digest.briefs == []


class TestComposeDigest:
    def _ranked(self, count: int = 20):
        items = [make_news(f"新闻 {index}", importance=90 - index) for index in range(count)]
        return rank_items(items)

    def test_repo_items_capped_in_top(self) -> None:
        """GitHub 项目在精选中限流，其余进简讯（避免挤掉新闻）。"""
        items = [make_repo(f"repo-{index}", importance=95 - index) for index in range(8)]
        items += [make_news(f"新闻 {index}", importance=60 - index) for index in range(10)]
        digest = compose_digest(rank_items(items), digest_date=date(2026, 9, 25), top_n=12)
        repo_titles = [
            item.cn_title for item in digest.top_items if item.extra.get("kind") == "repo"
        ]
        assert len(repo_titles) == 4  # MAX_REPO_ITEMS
        assert len(digest.top_items) == 12
        assert any(item.extra.get("kind") == "repo" for item in digest.briefs)

    def test_top_and_brief_split(self) -> None:
        digest = compose_digest(
            self._ranked(20),
            digest_date=date(2026, 9, 24),
            top_n=12,
            summary="综述内容",
            stats=DigestStats(scanned=100, kept=50, clusters=30),
        )
        assert len(digest.top_items) == 12
        assert len(digest.briefs) == 8
        assert digest.summary == "综述内容"
        assert digest.stats["selected"] == 12
        assert digest.stats["briefs"] == 8

    def test_noise_items_excluded_from_digest(self) -> None:
        items = [
            make_news("正常新闻", importance=80),
            make_news("噪音", category="其他", importance=10),
            make_news("被过滤", category="过滤", importance=0),
        ]
        digest = compose_digest(rank_items(items), digest_date=date(2026, 9, 24), top_n=12)
        titles = [item.cn_title for item in digest.top_items]
        assert titles == ["正常新闻"]

    def test_top_n_zero_gives_empty(self) -> None:
        digest = compose_digest(self._ranked(5), digest_date=date(2026, 9, 24), top_n=0)
        assert digest.top_items == []
        assert len(digest.briefs) == 5

    def test_summary_stripped(self) -> None:
        digest = compose_digest(
            self._ranked(1), digest_date=date(2026, 9, 24), top_n=1, summary="  综述  \n"
        )
        assert digest.summary == "综述"


class TestHelpers:
    def test_mention_and_source_count(self) -> None:
        item = make_news("x", mention=3, source_ids=["a", "b"])
        assert mention_count(item) == 3
        assert source_count(item) == 2

    def test_mention_count_fallback(self) -> None:
        item = make_news("x")
        item.extra.pop("mention_count")
        assert mention_count(item) == 1

    def test_item_links_prefers_extra(self) -> None:
        item = make_news("x", links=[{"source_id": "hn", "title": "t", "url": "https://hn/1"}])
        links = item_links(item)
        assert links[0]["source_id"] == "hn"
        assert links[0]["url"] == "https://hn/1"

    def test_item_links_fallback_to_self(self) -> None:
        item = make_news("x")
        links = item_links(item)
        assert len(links) == 1
        assert links[0]["url"] == item.url

    def test_date_display(self) -> None:
        assert date_display(date(2026, 9, 24)) == "2026年9月24日 星期四"
        assert date_display(date(2026, 9, 27)) == "2026年9月27日 星期日"

    def test_issue_number_starts_at_launch(self) -> None:
        assert issue_number(date(2026, 9, 23)) == 1
        assert issue_number(date(2026, 9, 24)) == 2
        assert issue_number(date(2026, 1, 1)) == 1  # 早于上线日也不小于 1
