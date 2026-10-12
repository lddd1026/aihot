"""长图渲染单元测试（含真实浏览器渲染冒烟）。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from aihot.models import Digest, NewsItem
from aihot.presentation import render_image
from aihot.presentation.render_image import (
    RenderError,
    build_plans,
    render_digest_html,
    render_images,
)

DIGEST_DATE = date(2026, 9, 24)


def make_item(index: int) -> NewsItem:
    return NewsItem(
        source_id="src_a",
        title=f"Title {index}",
        url=f"https://example.com/{index}",
        published_at=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
        cluster_id=index,
        category="模型发布",
        importance=80,
        cn_title=f"中文标题 {index}",
        cn_summary=f"第 {index} 条摘要",
        extra={"mention_count": 1, "source_ids": ["src_a"]},
    )


def make_paper(index: int = 1) -> NewsItem:
    return NewsItem(
        source_id="arxiv",
        title=f"Paper {index}",
        url=f"https://arxiv.org/abs/2609.{index}",
        published_at=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
        cluster_id=1000 + index,
        category="研究突破",
        importance=70,
        cn_title=f"论文标题 {index}",
        cn_summary=f"论文摘要 {index}",
        extra={
            "kind": "paper",
            "categories": ["cs.AI"],
            "mention_count": 1,
            "source_ids": ["arxiv"],
        },
    )


def make_digest(top: int = 4, briefs: int = 2) -> Digest:
    return Digest(
        digest_date=DIGEST_DATE,
        summary="今日综述：用于渲染测试。",
        top_items=[make_item(index) for index in range(1, top + 1)],
        briefs=[make_item(100 + index) for index in range(1, briefs + 1)],
        stats={"scanned": 100, "clusters": 40, "selected": top},
    )


class TestPlans:
    def test_full_plan_first(self) -> None:
        plans = build_plans(make_digest())
        assert len(plans) == 2
        assert len(plans[0]) == 1
        assert plans[0][0].filename == "2026-09-24.jpg"

    def test_split_plan_covers_all_items(self) -> None:
        digest = make_digest(top=6, briefs=2)
        split = build_plans(digest)[1]
        assert [part.filename for part in split] == ["2026-09-24-1.jpg", "2026-09-24-2.jpg"]
        first = split[0].context
        second = split[1].context
        assert len(first["top_items"]) + len(second["top_items"]) == 6
        assert first["show_summary"] is True
        assert second["show_summary"] is False
        assert second["briefs"]  # 简讯跟在最后一张
        assert first["briefs"] == []
    def test_single_item_no_split(self) -> None:
        plans = build_plans(make_digest(top=1, briefs=0))
        assert len(plans) == 1


class TestHtml:
    def test_html_contains_items_and_summary(self) -> None:
        from aihot.presentation.render_text import build_views

        digest = make_digest()
        top, briefs, papers = build_views(digest)
        html = render_digest_html(
            digest, top_items=top, briefs=briefs, papers=papers, show_summary=True
        )
        assert "中文标题 1" in html
        assert "第 1 条摘要" in html
        assert "今日综述：用于渲染测试。" in html
        assert "第 2 期" in html

    def test_show_summary_false_hides_summary(self) -> None:
        from aihot.presentation.render_text import build_views

        digest = make_digest()
        top, briefs, papers = build_views(digest)
        html = render_digest_html(
            digest, top_items=top, briefs=briefs, papers=papers, show_summary=False
        )
        assert "今日综述" not in html

    def test_part_suffix_rendered(self) -> None:
        from aihot.presentation.render_text import build_views

        digest = make_digest()
        top, briefs, papers = build_views(digest)
        html = render_digest_html(
            digest, top_items=top, briefs=briefs, papers=papers, part_suffix="（上）"
        )
        assert "（上）" in html

    def test_papers_section_rendered(self) -> None:
        from aihot.presentation.render_text import build_views

        digest = make_digest()
        digest.papers = [make_paper()]
        top, briefs, papers = build_views(digest)
        html = render_digest_html(
            digest, top_items=top, briefs=briefs, papers=papers, show_summary=True
        )
        assert "论文精选" in html
        assert "论文标题" in html
        assert "arXiv · cs.AI" in html


class TestRealRender:
    def test_render_produces_jpeg(self, tmp_path) -> None:
        try:
            paths = render_images(make_digest(), tmp_path, width=800)
        except RenderError as exc:  # 无浏览器环境时跳过（独立门禁会覆盖）
            pytest.skip(f"浏览器不可用: {exc}")
        assert len(paths) == 1
        assert paths[0].suffix == ".jpg"
        assert paths[0].stat().st_size > 1000

    def test_falls_back_to_split_when_full_page_too_large(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_capture = render_image._capture  # noqa: SLF001

        def fake_capture(page, path, max_bytes) -> bool:  # noqa: ANN001 - 测试替身
            if path.name == "2026-09-24.jpg":  # 强制整张图失败 → 走拆图分支
                return False
            return original_capture(page, path, max_bytes)

        monkeypatch.setattr(render_image, "_capture", fake_capture)
        try:
            paths = render_images(make_digest(top=6), tmp_path, width=800)
        except RenderError as exc:
            pytest.skip(f"浏览器不可用: {exc}")
        assert [path.name for path in paths] == ["2026-09-24-1.jpg", "2026-09-24-2.jpg"]
        for path in paths:
            assert path.stat().st_size > 1000

    def test_render_error_when_all_plans_fail(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(render_image, "_capture", lambda page, path, max_bytes: False)
        try:
            with pytest.raises(RenderError):
                render_images(make_digest(), tmp_path, width=600)
        except RenderError as exc:
            if "浏览器" in str(exc):  # 无浏览器时跳过
                pytest.skip(str(exc))
            raise
