"""预筛单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aihot.config import Config, load_config
from aihot.models import RawItem
from aihot.pipeline.prefilter import prefilter_items

BASE_CONFIG = """
app:
  timezone: Asia/Shanghai
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
push:
  wecom:
    enabled: false
pipeline:
  prefilter:
    gate_sources: [hackernews]
  dedup:
    llm_refine: false
sources:
  - id: qbitai
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
  - id: hackernews
    type: hackernews
    url: https://hn.algolia.com/api/v1/search_by_date
    enabled: true
    weight: 0.8
"""


def load_cfg(tmp_path: Path, text: str = BASE_CONFIG) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def make_item(
    title: str,
    *,
    source_id: str = "qbitai",
    summary: str = "",
    url: str | None = None,
) -> RawItem:
    return RawItem(
        source_id=source_id,
        title=title,
        url=url or f"https://example.com/{abs(hash(title)) % 100000}",
        published_at=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
        raw_summary=summary,
    )


class TestKeywordGate:
    def test_gated_source_non_ai_dropped(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        result = prefilter_items([make_item("Z80 REPL", source_id="hackernews")], cfg)
        assert result.items == []
        assert result.stats.dropped_offtopic == 1

    def test_gated_source_ai_kept(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        items = [
            make_item("Show HN: Local LLM runner", source_id="hackernews"),
            make_item("Chinese company 智谱 open-sources a model", source_id="hackernews"),
            make_item("New GPU benchmark results", source_id="hackernews"),
        ]
        result = prefilter_items(items, cfg)
        assert len(result.items) == 3

    def test_ungated_source_bypasses_gate(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        result = prefilter_items([make_item("某公司发布新框架")], cfg)
        assert len(result.items) == 1

    def test_gate_can_be_disabled(self, tmp_path: Path) -> None:
        text = BASE_CONFIG.replace("gate_sources: [hackernews]", "gate_sources: []")
        cfg = load_cfg(tmp_path, text)
        result = prefilter_items([make_item("Z80 REPL", source_id="hackernews")], cfg)
        assert len(result.items) == 1

    def test_extra_ai_keywords(self, tmp_path: Path) -> None:
        text = BASE_CONFIG.replace(
            "  prefilter:\n    gate_sources: [hackernews]",
            "  prefilter:\n    gate_sources: [hackernews]\n"
            '    extra_ai_keywords: ["frobnicator"]',
        )
        cfg = load_cfg(tmp_path, text)
        result = prefilter_items(
            [make_item("Quantum Frobnicator released", source_id="hackernews")], cfg
        )
        assert len(result.items) == 1


class TestBlacklist:
    def test_hiring_dropped(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        result = prefilter_items([make_item("我们正在招聘大模型算法工程师")], cfg)
        assert result.items == []
        assert result.stats.dropped_blacklist == 1

    def test_promo_dropped(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        result = prefilter_items(
            [make_item("3 days left to save up to $200 on AI Summit tickets")], cfg
        )
        assert result.items == []

    def test_webinar_dropped(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        result = prefilter_items([make_item("Register now for our LLM webinar")], cfg)
        assert result.items == []

    def test_normal_news_kept(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        result = prefilter_items([make_item("OpenAI 发布新一代模型")], cfg)
        assert len(result.items) == 1

    def test_custom_blacklist_adds_to_builtin(self, tmp_path: Path) -> None:
        text = BASE_CONFIG.replace(
            "  prefilter:\n    gate_sources: [hackernews]",
            "  prefilter:\n    gate_sources: [hackernews]\n"
            '    blacklist_keywords: ["foo"]',
        )
        cfg = load_cfg(tmp_path, text)
        # 内置黑名单仍然生效
        assert prefilter_items([make_item("我们正在招聘算法工程师")], cfg).stats.kept == 0
        # 自定义词也生效
        assert prefilter_items([make_item("foo bar AI")], cfg).stats.kept == 0
        # 正常新闻不受影响
        assert prefilter_items([make_item("OpenAI 发布新模型")], cfg).stats.kept == 1

    def test_builtin_blacklist_can_be_disabled(self, tmp_path: Path) -> None:
        text = BASE_CONFIG.replace(
            "  prefilter:\n    gate_sources: [hackernews]",
            "  prefilter:\n    gate_sources: [hackernews]\n"
            "    use_builtin_blacklist: false\n"
            '    blacklist_keywords: ["foo"]',
        )
        cfg = load_cfg(tmp_path, text)
        assert prefilter_items([make_item("我们正在招聘算法工程师")], cfg).stats.kept == 1
        assert prefilter_items([make_item("foo bar AI")], cfg).stats.kept == 0


class TestBatchDedup:
    def test_same_url_same_source_dropped_once(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        items = [
            make_item("AI 新闻 A", url="https://example.com/same"),
            make_item("AI 新闻 A（重复）", url="https://example.com/same"),
        ]
        result = prefilter_items(items, cfg)
        assert len(result.items) == 1
        assert result.stats.dropped_duplicate == 1

    def test_stats_total_counts_input(self, tmp_path: Path) -> None:
        cfg = load_cfg(tmp_path)
        items = [make_item("AI 新闻"), make_item("我们正在招聘", url="https://example.com/2")]
        result = prefilter_items(items, cfg)
        assert result.stats.total == 2
        assert result.stats.kept == 1
