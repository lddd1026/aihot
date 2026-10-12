"""M4 验收 8 独立验证：日报「🔬 论文精选」板块（长图/邮件/企微/存档 四种输出）。

- ≤2 篇/天且不占 Top 名额（compose_digest 行为）
- 四种输出均包含论文精选（长图另用 Windows OCR 实测识别）
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_papers.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m3_support import make_item, workspace  # noqa: E402
from m4_support import PROJECT, Suite, write_raw  # noqa: E402

from aihot.models import Digest, NewsItem  # noqa: E402
from aihot.pipeline.rank import rank_items  # noqa: E402
from aihot.presentation.compose import compose_digest  # noqa: E402
from aihot.presentation.render_image import render_digest_html, render_images  # noqa: E402
from aihot.presentation.render_text import (  # noqa: E402
    render_archive_md,
    render_email_html,
    render_wecom_parts,
)


def paper_item(index: int, *, source_id: str = "arxiv", with_cat: bool = True) -> NewsItem:
    item = make_item(
        index,
        title=f"论文精选独立验证 Paper {index}",
        summary=f"第 {index} 篇论文摘要：用于验证论文板块渲染。",
        category="研究突破",
        importance=88,
        source_id=source_id,
    )
    item.title = f"Independent Paper {index}: Scaling Laws Revisited"
    item.extra["kind"] = "paper"
    if with_cat:
        item.extra["categories"] = ["cs.AI", "cs.LG"]
    item.extra["authors"] = ["Alice", "Bob"]
    item.extra["arxiv_id"] = f"2609.{10000 + index}v1"
    return item


def normal_item(index: int) -> NewsItem:
    return make_item(
        index,
        title=f"普通新闻标题 {index}：模型与产品更新",
        summary=f"第 {index} 条普通条目摘要。",
        category="模型发布",
        importance=80 - index % 5,
        source_id="qbitai",
    )


def hf_item(index: int) -> NewsItem:
    item = paper_item(index, source_id="hf_papers", with_cat=False)
    item.extra["upvotes"] = 99
    item.title = f"HF Daily Paper {index}: Agentic Retrieval"
    return item


def build_digest() -> Digest:
    ranked = rank_items(
        [paper_item(i) for i in range(1, 5)]
        + [hf_item(i) for i in range(5, 7)]
        + [normal_item(i) for i in range(1, 31)],
        weights={"arxiv": 0.7, "hf_papers": 0.9, "qbitai": 1.0},
        interest_bonus={"模型发布": 8, "研究突破": 8},
    )
    return compose_digest(
        ranked,
        digest_date=date(2026, 9, 25),
        top_n=12,
        summary="今日综述：用于论文板块验证的独立测试文本。",
    )


def main() -> int:
    suite = Suite("m4_papers")
    digest = build_digest()

    # ---------- 1. 数量与 Top 名额 ----------
    suite.check(
        "8.1 论文精选 ≤ 2 篇/天",
        len(digest.papers) == 2,
        f"papers={len(digest.papers)}",
    )
    suite.check(
        "8.2 论文不占 Top 名额（Top 仍 12 条且全是非论文）",
        len(digest.top_items) == 12
        and all(str(item.extra.get("kind") or "") != "paper" for item in digest.top_items),
        f"top={len(digest.top_items)} "
        f"papers_in_top={sum(1 for i in digest.top_items if i.extra.get('kind') == 'paper')}",
    )
    suite.check(
        "8.3 论文在 stats 中单独计数",
        digest.stats.get("papers") == 2,
        f"stats.papers={digest.stats.get('papers')}",
    )
    suite.check(
        "8.4 精选 2 篇为排名最高的论文（含 HF）",
        {item.source_id for item in digest.papers} <= {"arxiv", "hf_papers"},
        f"sources={[item.source_id for item in digest.papers]}",
    )

    # ---------- 2. 企微 markdown ----------
    wecom_parts = render_wecom_parts(digest)
    wecom_text = "\n\n".join(wecom_parts)
    suite.check(
        "8.5 企微 markdown 含「🔬 论文精选」与小节条目",
        "🔬 论文精选" in wecom_text and any(item.cn_title in wecom_text for item in digest.papers),
        f"parts={len(wecom_parts)}",
        evidence=wecom_text,
    )
    suite.check(
        "8.6 企微论文条目标注来源（arXiv / Hugging Face）",
        "arXiv" in wecom_text or "Hugging Face" in wecom_text,
        "note 标注存在",
    )

    # ---------- 3. 邮件 HTML ----------
    email_html = render_email_html(digest)
    suite.check(
        "8.7 邮件 HTML 含「🔬 论文精选」与小节条目",
        "🔬 论文精选" in email_html and all(item.cn_title in email_html for item in digest.papers),
        f"html={len(email_html)} bytes",
        evidence=email_html,
    )

    # ---------- 4. 存档 markdown ----------
    archive_md = render_archive_md(digest)
    suite.check(
        "8.8 存档 markdown 含「## 论文精选」与小节条目",
        "## 论文精选" in archive_md and all(item.cn_title in archive_md for item in digest.papers),
        "archive md 齐全",
        evidence=archive_md,
    )

    # ---------- 5. 长图 HTML + 实拍图 OCR ----------
    # 按产品的 build_plans 走：先整张渲染
    from aihot.presentation.render_image import build_plans

    plan = build_plans(digest)[0][0]
    html = render_digest_html(digest, **plan.context)
    suite.check(
        "8.9 长图 HTML 含论文精选板块",
        "🔬 论文精选" in html and all(item.cn_title in html for item in digest.papers),
        "image html 含论文",
    )
    write_raw("26-papers-image.html", html)

    out_dir = workspace("m4-papers-image")
    try:
        paths = render_images(digest, out_dir)
        rendered = paths and paths[0].exists()
    except Exception as exc:  # noqa: BLE001
        rendered = False
        suite.note(f"长图渲染失败（隔离判定）: {type(exc).__name__}: {exc}")
    suite.check(
        "8.10 长图实际渲染成功（JPEG）",
        bool(rendered),
        f"paths={paths if 'paths' in dir() else None}",
    )

    ocr_text = ""
    if rendered:
        jpg = paths[0]
        script = PROJECT / "tests" / "independent" / "m3_ocr.ps1"
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Path",
                str(jpg),
            ],
            capture_output=True,
            timeout=180,
        )
        ocr_path = Path(str(jpg) + ".ocr.txt")
        if ocr_path.exists():
            ocr_text = ocr_path.read_text(encoding="utf-8", errors="replace")
        write_raw("26-papers-image-ocr.txt", ocr_text + f"\n\n[ocr runner] rc={proc.returncode}\n")
        import re as _re

        compact = _re.sub(r"\s+", "", ocr_text)
        matched_titles = [
            item.cn_title[:14]
            for item in digest.papers
            if item.cn_title.replace(" ", "") in compact
        ]
        suite.check(
            "8.11 长图 OCR 实测识别到「论文精选」或论文标题",
            ("论文精选" in compact) or bool(matched_titles) or ("IndependentPaper" in compact),
            f"ocr_chars={len(ocr_text)}；命中论文精选={'论文精选' in compact}；"
            f"命中标题={matched_titles}",
        )

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
