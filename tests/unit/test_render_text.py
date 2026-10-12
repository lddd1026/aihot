"""文字渲染单元测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from aihot.models import Digest, NewsItem
from aihot.presentation.render_text import (
    MAX_WECOM_BYTES,
    render_archive_md,
    render_email_html,
    render_wecom_parts,
    save_digest_files,
)

DIGEST_DATE = date(2026, 9, 24)


def make_item(index: int, *, mention: int = 1) -> NewsItem:
    return NewsItem(
        source_id="src_a",
        title=f"English title {index}",
        url=f"https://example.com/{index}",
        published_at=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
        cluster_id=index,
        category="模型发布",
        importance=80,
        cn_title=f"中文标题 {index}",
        cn_summary=f"这是第 {index} 条的中文摘要，用于验证渲染。",
        extra={
            "mention_count": mention,
            "source_ids": ["src_a", "src_b"][:mention],
            "links": [
                {"source_id": "src_a", "title": f"t{index}", "url": f"https://example.com/{index}"}
            ],
        },
    )


def make_digest(*, top: int = 3, briefs: int = 2, summary: str = "今日综述内容。") -> Digest:
    return Digest(
        digest_date=DIGEST_DATE,
        summary=summary,
        top_items=[make_item(index, mention=2 if index == 1 else 1) for index in range(1, top + 1)],
        briefs=[make_item(100 + index) for index in range(1, briefs + 1)],
        stats={"scanned": 100, "clusters": 40, "selected": top, "briefs": briefs, "cost_cny": 0.05},
    )


class TestWeComRender:
    def test_single_part_contains_key_sections(self) -> None:
        parts = render_wecom_parts(make_digest())
        assert len(parts) == 1
        body = parts[0]
        assert "中文标题 1" in body
        assert "https://example.com/1" in body
        assert "2 篇报道 / 2 家来源" in body
        assert "其他值得一看" in body
        assert "共扫描 100 条" in body
        assert body.count("**") % 2 == 0  # 粗体标记成对

    def test_summary_included(self) -> None:
        parts = render_wecom_parts(make_digest(summary="这是综述。"))
        assert "这是综述。" in parts[0]

    def test_long_digest_is_split_within_limit(self) -> None:
        digest = make_digest(top=12, briefs=12)
        parts = render_wecom_parts(digest, max_bytes=800)
        assert len(parts) > 1
        for part in parts:
            assert len(part.encode("utf-8")) <= 800

    def test_default_limit_respected(self) -> None:
        parts = render_wecom_parts(make_digest(top=12, briefs=12))
        for part in parts:
            assert len(part.encode("utf-8")) <= MAX_WECOM_BYTES

    def test_no_briefs_section_when_empty(self) -> None:
        parts = render_wecom_parts(make_digest(briefs=0))
        assert "其他值得一看" not in parts[0]


class TestEmailRender:
    def test_contains_items_and_cid(self) -> None:
        html = render_email_html(make_digest(), image_cid="digest-image-1")
        assert "cid:digest-image-1" in html
        assert "中文标题 1" in html
        assert "今日综述内容。" in html
        assert "https://example.com/1" in html

    def test_without_image_cid(self) -> None:
        html = render_email_html(make_digest())
        assert "cid:" not in html

    def test_escapes_html_in_titles(self) -> None:
        digest = make_digest()
        digest.top_items[0].cn_title = "<script>alert(1)</script>"
        html = render_email_html(digest)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


class TestArchive:
    def test_markdown_structure(self) -> None:
        md = render_archive_md(make_digest())
        assert md.startswith("# AI 热点日报")
        assert "## 今日综述" in md
        assert "## 今日必读" in md
        assert "## 其他值得一看" in md
        assert "[中文标题 1](https://example.com/1)" in md

    def test_save_files(self, tmp_path) -> None:
        digest = make_digest()
        files = save_digest_files(digest, tmp_path)
        assert "markdown" in files
        assert digest.files["markdown"] == files["markdown"]
        content = (tmp_path / "2026-09-24.md").read_text(encoding="utf-8")
        assert "中文标题 1" in content
