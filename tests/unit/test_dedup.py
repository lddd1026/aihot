"""事件聚类单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from aihot.models import Cluster, RawItem
from aihot.pipeline.dedup import (
    cluster_items,
    merge_clusters_by_groups,
    normalize_title,
    pick_representative,
    title_similarity,
    validate_merged_groups,
)

BASE_TIME = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


def make_item(
    title: str,
    *,
    source_id: str = "src_a",
    url: str = "",
    hours_offset: float = 0.0,
    extra: dict | None = None,
    summary: str = "",
) -> RawItem:
    return RawItem(
        source_id=source_id,
        title=title,
        url=url or f"https://example.com/{abs(hash(title)) % 1000000}",
        published_at=BASE_TIME + timedelta(hours=hours_offset),
        raw_summary=summary,
        extra=extra or {},
    )


class TestNormalizeTitle:
    def test_strips_punctuation_and_case(self) -> None:
        # 注意：GPT-6 的连字符属于产品名，必须保留
        assert normalize_title("OpenAI 发布：GPT-6！") == "openai发布gpt6"

    def test_strips_source_suffix(self) -> None:
        assert normalize_title("某模型发布 - 量子位") == "某模型发布"
        assert normalize_title("Some model released | TechCrunch") == "somemodelreleased"

    def test_keeps_hyphenated_product_names(self) -> None:
        assert normalize_title("GPT-6 Sol") == "gpt6sol"
        assert normalize_title("Llama-3 发布") == "llama3发布"

    def test_empty(self) -> None:
        assert normalize_title("") == ""


class TestTitleSimilarity:
    def test_identical_is_100(self) -> None:
        assert title_similarity("OpenAI 发布 GPT-6", "OpenAI 发布 GPT-6") == 100.0

    def test_similar_titles_high(self) -> None:
        score = title_similarity(
            "OpenAI 发布 GPT-6 Sol 和 Luna 两个新模型",
            "OpenAI 发布 GPT-6 Sol 与 Luna 新模型",
        )
        assert score >= 88

    def test_different_models_not_merged(self) -> None:
        # 关键回归：不同模型名（Sol / Luna）不得因归一化而误判为同一事件
        score = title_similarity("OpenAI 发布 GPT-6 Sol", "OpenAI 发布 GPT-6 Luna")
        assert score < 88

    def test_english_reorder_matches(self) -> None:
        score = title_similarity(
            "OpenAI launches GPT-6 Sol and Luna",
            "GPT-6 Sol and Luna launched by OpenAI",
        )
        assert score >= 88

    def test_containment_matches(self) -> None:
        score = title_similarity("云栖大会发布新模型", "云栖大会发布新模型（附直播回放）")
        assert score >= 88

    def test_short_titles_differing_by_char_not_merged(self) -> None:
        assert title_similarity("独立新闻 0", "独立新闻 1") < 88

    def test_different_titles_low(self) -> None:
        score = title_similarity("OpenAI 发布 GPT-6", "DeepMind 开源 AlphaFold 新版本")
        assert score < 60

    def test_empty_returns_zero(self) -> None:
        assert title_similarity("", "abc") == 0.0


class TestClusterItems:
    def test_same_url_merged(self) -> None:
        items = [
            make_item("AI 新闻 A", url="https://example.com/a"),
            make_item("完全不同的标题", url="https://example.com/a", source_id="src_b"),
        ]
        result = cluster_items(items)
        assert result.stats.clusters == 1
        assert result.clusters[0].mention_count == 2
        assert result.clusters[0].source_ids == ["src_a", "src_b"]

    def test_similar_titles_merged(self) -> None:
        items = [
            make_item("OpenAI 发布 GPT-6 Sol 和 Luna 两个新模型"),
            make_item("OpenAI 发布 GPT-6 Sol 与 Luna 新模型", source_id="src_b", hours_offset=1),
        ]
        result = cluster_items(items)
        assert result.stats.clusters == 1
        assert result.stats.multi_source_clusters == 1

    def test_different_events_not_merged(self) -> None:
        items = [
            make_item("OpenAI 发布 GPT-6"),
            make_item("某公司完成 10 亿元融资", source_id="src_b"),
        ]
        assert cluster_items(items).stats.clusters == 2

    def test_time_window_prevents_merge(self) -> None:
        items = [
            make_item("OpenAI 发布 GPT-6", url="https://example.com/a"),
            make_item(
                "OpenAI 发布 GPT-6",
                source_id="src_b",
                url="https://example.com/b",
                hours_offset=100,
            ),
        ]
        assert cluster_items(items, max_hours_apart=72).stats.clusters == 2

    def test_same_url_merges_regardless_of_time(self) -> None:
        items = [
            make_item("OpenAI 发布 GPT-6", url="https://example.com/same"),
            make_item(
                "OpenAI 发布 GPT-6",
                source_id="src_b",
                url="https://example.com/same",
                hours_offset=100,
            ),
        ]
        assert cluster_items(items, max_hours_apart=72).stats.clusters == 1

    def test_threshold_respected(self) -> None:
        items = [
            make_item("OpenAI 发布 GPT-6 Sol"),
            make_item("OpenAI 发布 GPT-6 Luna", source_id="src_b"),
        ]
        assert cluster_items(items, threshold=99).stats.clusters == 2

    def test_empty_input(self) -> None:
        result = cluster_items([])
        assert result.clusters == []
        assert result.stats.clusters == 0

    def test_cluster_ids_are_sequential(self) -> None:
        items = [make_item(f"独立新闻 {index}") for index in range(5)]
        result = cluster_items(items)
        assert [cluster.cluster_id for cluster in result.clusters] == [1, 2, 3, 4, 5]


class TestRepresentative:
    def test_prefers_higher_source_weight(self) -> None:
        items = [
            make_item("普通源", source_id="low"),
            make_item("官方源", source_id="official"),
        ]
        weights = {"low": 0.8, "official": 1.2}
        assert pick_representative(items, weights).source_id == "official"

    def test_prefers_summary_then_heat(self) -> None:
        items = [
            make_item("无摘要", source_id="a"),
            make_item("有摘要", source_id="a", summary="一段摘要"),
        ]
        assert pick_representative(items, {}).title == "有摘要"

        hot = [
            make_item("冷", source_id="a", extra={"points": 10}),
            make_item("热", source_id="a", extra={"points": 900}),
        ]
        assert pick_representative(hot, {}).title == "热"

    def test_timezone_aware_input(self) -> None:
        item = make_item("时区测试")
        item.published_at = datetime(2026, 9, 24, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        assert pick_representative([item], {}).title == "时区测试"


class TestMergeClustersByGroups:
    def test_merges_groups(self) -> None:
        items = [
            make_item("OpenAI GPT-6", source_id="a"),
            make_item("GPT-6 发布", source_id="b"),
            make_item("无关新闻", source_id="c"),
        ]
        result = cluster_items(items, threshold=99)  # 规则层不合并
        assert result.stats.clusters == 3
        merged = merge_clusters_by_groups(result.clusters, result.members, [[1, 2], [3]])
        assert len(merged.clusters) == 2
        big = max(merged.clusters, key=lambda cluster: cluster.mention_count)
        assert big.mention_count == 2
        assert sorted(big.source_ids) == ["a", "b"]
        assert len(merged.members[big.cluster_id]) == 2

    def test_empty_groups(self) -> None:
        merged = merge_clusters_by_groups([], {}, [])
        assert merged.clusters == []


class TestValidateMergedGroups:
    def _clusters(self, titles: list[str]) -> list[Cluster]:
        return [
            Cluster(
                cluster_id=index,
                canonical_title=title,
                item_hashes=[],
                source_ids=[f"src{index}"],
                mention_count=1,
                first_seen=BASE_TIME,
            )
            for index, title in enumerate(titles, start=1)
        ]

    def test_connected_group_accepted(self) -> None:
        clusters = self._clusters(["OpenAI 发布 GPT-6", "GPT-6 released by OpenAI"])
        accepted, rejections = validate_merged_groups([[1, 2]], clusters)
        assert accepted == [[1, 2]]
        assert rejections == []

    def test_disconnected_group_split(self) -> None:
        clusters = self._clusters(
            ["OpenAI 发布 GPT-6", "OpenAI 优化 prompt caching", "五角大楼称过度依赖 AI"]
        )
        accepted, rejections = validate_merged_groups([[1, 2, 3]], clusters)
        assert accepted == [[1], [2], [3]]
        assert len(rejections) == 1
        assert "相似链" in rejections[0]

    def test_oversized_group_split(self) -> None:
        titles = [f"同一事件报道 {index}" for index in range(6)]
        clusters = self._clusters(titles)
        accepted, rejections = validate_merged_groups([[1, 2, 3, 4, 5, 6]], clusters)
        assert len(accepted) == 6
        assert any("分组过大" in reason for reason in rejections)

    def test_singletons_pass_through(self) -> None:
        clusters = self._clusters(["甲", "乙"])
        accepted, rejections = validate_merged_groups([[1], [2]], clusters)
        assert accepted == [[1], [2]]
        assert rejections == []

    def test_partial_group_split_keeps_valid_part(self) -> None:
        clusters = self._clusters(
            ["OpenAI 发布 GPT-6 Sol 和 Luna", "OpenAI 发布 GPT-6 Sol 与 Luna", "完全无关的新闻"]
        )
        accepted, rejections = validate_merged_groups([[1, 2, 3]], clusters)
        assert [1, 2] in accepted
        assert [3] in accepted
        assert len(rejections) == 1
