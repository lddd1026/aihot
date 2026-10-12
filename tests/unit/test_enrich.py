"""LLM 加工单元测试（全部使用注入的假 chat，不产生真实调用）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aihot.config import Config, load_config
from aihot.models import NewsItem, RawItem
from aihot.pipeline.dedup import cluster_items
from aihot.pipeline.enrich import (
    Enricher,
    _parse_refine_groups,
    _valid_items,
    clean_summary,
    parse_json_object,
)

BASE_TIME = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)

CONFIG_TEXT = """
app:
  timezone: Asia/Shanghai
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
  batch_size: 2
  daily_cost_limit_cny: 1.0
push:
  wecom:
    enabled: false
pipeline:
  dedup:
    llm_refine: true
  enrich:
    price_input_per_million: 2.0
    price_output_per_million: 8.0
sources:
  - id: src_a
    type: rss
    url: https://example.com/a
    enabled: true
    weight: 1.0
"""


def load_cfg(tmp_path: Path, text: str = CONFIG_TEXT) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def make_item(title: str, *, source_id: str = "src_a", url: str = "", summary: str = "") -> RawItem:
    return RawItem(
        source_id=source_id,
        title=title,
        url=url or f"https://example.com/{abs(hash(title)) % 1000000}",
        published_at=BASE_TIME,
        raw_summary=summary,
    )


def make_chat(replies: list[str | None]):
    """按顺序返回预设回复；用完后返回错误。"""
    queue = list(replies)

    def chat(system: str, user: str) -> tuple[str | None, str]:
        if not queue:
            return None, "没有更多预设回复"
        return queue.pop(0), ""

    return chat


def enrich_reply(mapping: dict[int, dict]) -> str:
    items = [{"index": index, **payload} for index, payload in mapping.items()]
    return json.dumps({"items": items})


class TestParseJsonObject:
    def test_plain(self) -> None:
        assert parse_json_object('{"a": 1}') == {"a": 1}

    def test_with_markdown_fence(self) -> None:
        assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_with_surrounding_text(self) -> None:
        assert parse_json_object('好的，结果如下：{"a": 1} 完毕') == {"a": 1}

    def test_broken_json(self) -> None:
        assert parse_json_object('{"a": ') is None

    def test_not_object(self) -> None:
        assert parse_json_object("[1, 2]") is None

    def test_empty(self) -> None:
        assert parse_json_object("") is None


class TestParseRefineGroups:
    def test_high_confidence_merged(self) -> None:
        payload = {"groups": [{"indexes": [1, 2], "confidence": "high", "event": "e"}]}
        assert _parse_refine_groups(payload, {1, 2, 3}) == [[1, 2], [3]]

    def test_medium_confidence_not_merged(self) -> None:
        payload = {"groups": [{"indexes": [1, 2], "confidence": "medium", "event": "e"}]}
        assert _parse_refine_groups(payload, {1, 2}) == [[1], [2]]

    def test_missing_confidence_not_merged(self) -> None:
        payload = {"groups": [{"indexes": [1, 2], "event": "e"}]}
        assert _parse_refine_groups(payload, {1, 2}) == [[1], [2]]

    def test_unmentioned_ids_become_singletons(self) -> None:
        payload = {"groups": [{"indexes": [2, 3], "confidence": "high", "event": "e"}]}
        assert _parse_refine_groups(payload, {1, 2, 3, 4}) == [[2, 3], [1], [4]]

    def test_unknown_index_rejected(self) -> None:
        payload = {"groups": [{"indexes": [1, 9], "confidence": "high"}]}
        assert _parse_refine_groups(payload, {1}) is None

    def test_duplicate_index_rejected(self) -> None:
        payload = {
            "groups": [
                {"indexes": [1], "confidence": "high"},
                {"indexes": [1], "confidence": "high"},
            ]
        }
        assert _parse_refine_groups(payload, {1}) is None

    def test_wrong_shape_rejected(self) -> None:
        assert _parse_refine_groups({"groups": "x"}, {1}) is None
        assert _parse_refine_groups(None, {1}) is None
        assert _parse_refine_groups({"groups": [[1, 2]]}, {1, 2}) is None
        assert _parse_refine_groups({"groups": [{"indexes": []}]}, {1}) is None

    def test_valid_items_ok(self) -> None:
        payload = {"items": [{"index": 1}, {"index": 2}]}
        assert _valid_items(payload, 2) == [{"index": 1}, {"index": 2}]

    def test_valid_items_out_of_order(self) -> None:
        payload = {"items": [{"index": 2}, {"index": 1}]}
        assert _valid_items(payload, 2) == [{"index": 1}, {"index": 2}]

    def test_valid_items_count_mismatch(self) -> None:
        assert _valid_items({"items": [{"index": 1}]}, 2) is None

    def test_valid_items_duplicate_index(self) -> None:
        assert _valid_items({"items": [{"index": 1}, {"index": 1}]}, 2) is None


class TestRefineClusters:
    def _result(self):
        items = [
            make_item("OpenAI 发布 GPT-6", source_id="src_a"),
            make_item("GPT-6 released by OpenAI", source_id="src_b"),
        ]
        return cluster_items(items, threshold=99)  # 规则层不合并

    @staticmethod
    def _reply(*groups: list[int], confidence: str = "high") -> str:
        payload = [
            {"indexes": list(group), "confidence": confidence, "event": "某事件"}
            for group in groups
        ]
        return json.dumps({"groups": payload})

    def test_merges_via_llm(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([self._reply([1, 2])]))
        merged = enricher.refine_clusters(self._result())
        assert len(merged.clusters) == 1
        assert merged.clusters[0].mention_count == 2
        assert enricher.stats.refine_calls == 1
        assert enricher.stats.refine_rejected == 0

    def test_medium_confidence_does_not_merge(self, tmp_path: Path) -> None:
        enricher = Enricher(
            load_cfg(tmp_path), chat=make_chat([self._reply([1, 2], confidence="medium")])
        )
        result = self._result()
        merged = enricher.refine_clusters(result)
        assert len(merged.clusters) == len(result.clusters)

    def test_keeps_rule_clusters_on_bad_json(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat(["这不是 JSON"]))
        result = self._result()
        merged = enricher.refine_clusters(result)
        assert len(merged.clusters) == len(result.clusters)
        assert enricher.stats.refine_failed == 1

    def test_keeps_rule_clusters_on_exception(self, tmp_path: Path) -> None:
        def boom(system: str, user: str) -> tuple[str | None, str]:
            return None, "ConnectError: timeout"

        enricher = Enricher(load_cfg(tmp_path), chat=boom)
        result = self._result()
        merged = enricher.refine_clusters(result)
        assert len(merged.clusters) == len(result.clusters)
        assert enricher.stats.refine_failed == 1

    def test_unknown_index_rejected(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([self._reply([1, 9])]))
        result = self._result()
        merged = enricher.refine_clusters(result)
        assert len(merged.clusters) == len(result.clusters)
        assert enricher.stats.refine_failed == 1

    def test_disconnected_group_is_split(self, tmp_path: Path) -> None:
        """LLM 把不相关的事件并成一组时，规则校验必须拆散它（P1 回归）。"""
        items = [
            make_item("OpenAI 发布 GPT-6 Sol", url="https://example.com/1"),
            make_item("OpenAI 优化 prompt caching", url="https://example.com/2"),
            make_item("五角大楼称过度依赖 AI", url="https://example.com/3"),
        ]
        result = cluster_items(items, threshold=99)
        assert len(result.clusters) == 3
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([self._reply([1, 2, 3])]))
        merged = enricher.refine_clusters(result)
        assert len(merged.clusters) == 3  # 全部被拆回单元素
        assert enricher.stats.refine_rejected == 1
        assert enricher.stats.refine_failed == 0

    def test_single_cluster_skips_llm(self, tmp_path: Path) -> None:
        items = [make_item("唯一新闻")]
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([]))
        result = enricher.refine_clusters(cluster_items(items))
        assert enricher.stats.refine_calls == 0
        assert len(result.clusters) == 1


class TestEnrichClusters:
    def _result(self, count: int = 2):
        items = [
            make_item(f"新闻 {index}", url=f"https://example.com/{index}")
            for index in range(count)
        ]
        return cluster_items(items)

    def test_basic_enrichment(self, tmp_path: Path) -> None:
        reply = enrich_reply(
            {
                1: {
                    "cn_title": "中文标题一",
                    "cn_summary": "中文摘要一",
                    "category": "模型发布",
                    "importance": 88,
                    "is_ai": True,
                },
                2: {
                    "cn_title": "中文标题二",
                    "cn_summary": "中文摘要二",
                    "category": "融资商业",
                    "importance": 40,
                    "is_ai": True,
                },
            }
        )
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([reply]))
        items = enricher.enrich_clusters(self._result(), {"src_a": 1.0})
        assert [item.cn_title for item in items] == ["中文标题一", "中文标题二"]
        assert items[0].category == "模型发布"
        assert items[0].importance == 88
        assert items[0].extra["mention_count"] == 1
        assert enricher.stats.succeeded == 2
        assert enricher.stats.failed_batches == 0

    def test_bad_json_falls_back(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat(["坏数据", "还是坏的"]))
        items = enricher.enrich_clusters(self._result(3), {"src_a": 1.0})
        assert len(items) == 3
        assert all(item.importance == 30 for item in items)  # 降级为中性重要度
        assert all(item.cn_title == item.title for item in items)
        assert enricher.stats.failed_batches == 2
        assert enricher.stats.fallback_items == 3

    def test_exception_falls_back(self, tmp_path: Path) -> None:
        def boom(system: str, user: str) -> tuple[str | None, str]:
            return None, "Timeout"

        enricher = Enricher(load_cfg(tmp_path), chat=boom)
        items = enricher.enrich_clusters(self._result(2), {"src_a": 1.0})
        assert len(items) == 2
        assert enricher.stats.failed_batches == 1

    def test_invalid_category_becomes_other(self, tmp_path: Path) -> None:
        reply = enrich_reply(
            {
                1: {"cn_title": "T", "cn_summary": "S", "category": "瞎写的分类", "importance": 50},
                2: {"cn_title": "T2", "cn_summary": "S2", "category": "模型发布", "importance": 50},
            }
        )
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([reply]))
        items = enricher.enrich_clusters(self._result(), {"src_a": 1.0})
        assert items[0].category == "其他"

    def test_is_ai_false_downgraded(self, tmp_path: Path) -> None:
        reply = enrich_reply(
            {
                1: {
                    "cn_title": "T",
                    "cn_summary": "S",
                    "category": "模型发布",
                    "importance": 95,
                    "is_ai": False,
                },
                2: {"cn_title": "T2", "cn_summary": "S2", "category": "模型发布", "importance": 50},
            }
        )
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([reply]))
        items = enricher.enrich_clusters(self._result(), {"src_a": 1.0})
        assert items[0].category == "其他"
        assert items[0].importance <= 15

    def test_cost_limit_stops_processing(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([]))
        enricher.stats.cost_cny = 99.0  # 直接模拟已超预算
        items = enricher.enrich_clusters(self._result(4), {"src_a": 1.0})
        assert enricher.stats.batches == 0
        assert enricher.stats.cost_limit_hit is True
        assert len(items) == 4  # 仍然返回降级条目，不丢数据

    def test_empty_input(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([]))
        assert enricher.enrich_clusters(cluster_items([]), {}) == []


class TestCleanSummary:
    def test_plain_text_passthrough(self) -> None:
        assert clean_summary("今天的关键进展是……") == "今天的关键进展是……"

    def test_json_wrapper_extracted(self) -> None:
        raw = json.dumps({"type": "json_object", "content": "真正的综述正文。"}, ensure_ascii=False)
        assert clean_summary(raw) == "真正的综述正文。"

    def test_fenced_json_extracted(self) -> None:
        raw = '```json\n{"综述": "带围栏的正文"}\n```'
        assert clean_summary(raw) == "带围栏的正文"

    def test_long_text_truncated(self) -> None:
        assert len(clean_summary("字" * 1000)) == 600

    def test_empty(self) -> None:
        assert clean_summary("") == ""


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs: object):  # noqa: ANN201 - 测试替身
        self.calls.append(dict(kwargs))
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class _FakeClient:
    """模拟 openai SDK 客户端，用于检查请求参数。"""

    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


class TestRequestModes:
    def _news(self, title: str = "标题") -> NewsItem:
        return NewsItem(
            source_id="src_a",
            title=title,
            url="https://example.com/1",
            published_at=BASE_TIME,
            cn_title=title,
            category="模型发布",
            importance=80,
        )

    def test_enrich_uses_json_mode(self, tmp_path: Path) -> None:
        reply = enrich_reply(
            {1: {"cn_title": "T", "cn_summary": "S", "category": "模型发布", "importance": 80}}
        )
        client = _FakeClient(reply)
        enricher = Enricher(load_cfg(tmp_path), client=client)
        enricher.enrich_clusters(cluster_items([make_item("新闻 1")]), {"src_a": 1.0})
        assert client.completions.calls[0]["response_format"] == {"type": "json_object"}

    def test_summarize_does_not_use_json_mode(self, tmp_path: Path) -> None:
        client = _FakeClient("今天的关键进展是……")
        enricher = Enricher(load_cfg(tmp_path), client=client)
        assert enricher.summarize([self._news()]) == "今天的关键进展是……"
        assert "response_format" not in client.completions.calls[0]

    def test_summarize_cleans_json_wrapper(self, tmp_path: Path) -> None:
        client = _FakeClient('{"content": "被 JSON 包裹的综述"}')
        enricher = Enricher(load_cfg(tmp_path), client=client)
        assert enricher.summarize([self._news()]) == "被 JSON 包裹的综述"


class TestSummarize:
    def test_returns_text(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat(["今天的关键进展是……"]))
        items = Enricher(load_cfg(tmp_path), chat=make_chat([])).enrich_clusters(
            cluster_items([make_item("新闻")]), {"src_a": 1.0}
        )
        assert enricher.summarize(items) == "今天的关键进展是……"

    def test_failure_returns_empty(self, tmp_path: Path) -> None:
        enricher = Enricher(load_cfg(tmp_path), chat=make_chat([]))
        assert enricher.summarize([]) == ""
        items = Enricher(load_cfg(tmp_path), chat=make_chat([])).enrich_clusters(
            cluster_items([make_item("新闻")]), {"src_a": 1.0}
        )
        assert enricher.summarize(items) == ""
