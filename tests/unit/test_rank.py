"""热度排序单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aihot.models import NewsItem
from aihot.pipeline.rank import community_signal, rank_items, score_item, select_top

BASE_TIME = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


def make_news(
    *,
    title: str = "标题",
    source_id: str = "src",
    importance: int = 50,
    category: str = "行业动态",
    mention: int = 1,
    points: int = 0,
    comments: int = 0,
    hours_offset: float = 0.0,
) -> NewsItem:
    return NewsItem(
        source_id=source_id,
        title=title,
        url=f"https://example.com/{title}",
        published_at=BASE_TIME + timedelta(hours=hours_offset),
        cluster_id=1,
        category=category,
        importance=importance,
        cn_title=title,
        cn_summary="摘要",
        extra={
            "mention_count": mention,
            "points": points,
            "num_comments": comments,
            "source_ids": [source_id],
        },
    )


class TestCommunitySignal:
    def test_zero_without_signals(self) -> None:
        assert community_signal({}) == 0.0

    def test_monotonic_and_capped(self) -> None:
        low = community_signal({"points": 50})
        mid = community_signal({"points": 300})
        high = community_signal({"points": 5000})
        assert 0 < low < mid <= 1.0
        assert high == 1.0

    def test_comments_count_double(self) -> None:
        assert community_signal({"num_comments": 100}) > community_signal({"points": 100})


class TestScoreItem:
    def test_importance_dominates(self) -> None:
        high, _ = score_item(make_news(importance=95))
        low, _ = score_item(make_news(importance=20))
        assert high - low > 35  # 0.55 * 75 = 41.25 分差

    def test_cross_source_bonus(self) -> None:
        single, _ = score_item(make_news(mention=1))
        multi, _ = score_item(make_news(mention=4))
        assert multi - single > 15

    def test_community_bonus(self) -> None:
        quiet, _ = score_item(make_news())
        popular, _ = score_item(make_news(points=1200, comments=400))
        assert popular > quiet

    def test_source_weight_bonus(self) -> None:
        normal, _ = score_item(make_news(), source_weight=0.8)
        official, _ = score_item(make_news(), source_weight=1.2)
        assert official > normal

    def test_interest_bonus_applied(self) -> None:
        base, breakdown = score_item(make_news(category="行业动态"))
        boosted, boosted_breakdown = score_item(
            make_news(category="模型发布"), interest_bonus={"模型发布": 8}
        )
        assert boosted - base == 8
        assert boosted_breakdown["interest_bonus"] == 8.0
        assert breakdown["interest_bonus"] == 0.0

    def test_importance_clamped(self) -> None:
        over, _ = score_item(make_news(importance=500))
        normal, _ = score_item(make_news(importance=100))
        assert over == normal

    def test_breakdown_keys(self) -> None:
        _, breakdown = score_item(make_news())
        assert set(breakdown) == {
            "importance",
            "cross_source",
            "community",
            "source_weight",
            "interest_bonus",
        }


class TestRankItems:
    def test_sorted_by_score_desc(self) -> None:
        items = [
            make_news(title="低", importance=10),
            make_news(title="高", importance=95),
            make_news(title="中", importance=50),
        ]
        ranked = rank_items(items)
        assert [entry.item.title for entry in ranked] == ["高", "中", "低"]

    def test_tie_break_prefers_newer(self) -> None:
        items = [
            make_news(title="旧", importance=50, hours_offset=-5),
            make_news(title="新", importance=50, hours_offset=0),
        ]
        ranked = rank_items(items)
        assert ranked[0].item.title == "新"

    def test_weights_applied(self) -> None:
        items = [make_news(source_id="low"), make_news(source_id="high")]
        ranked = rank_items(items, weights={"low": 0.5, "high": 1.2})
        assert ranked[0].item.source_id == "high"

    def test_select_top(self) -> None:
        ranked = rank_items([make_news(title=str(index)) for index in range(5)])
        assert len(select_top(ranked, 3)) == 3
        assert select_top(ranked, 0) == []
        assert len(select_top(ranked, 99)) == 5
