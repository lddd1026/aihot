"""M3 验收项 2：文字渲染（企微 markdown / 邮件 HTML / 本地存档 md）。

覆盖：
- 企微 markdown 含综述与全部条目链接；每条消息 ≤ 4096 字节（含极端长文）
- 邮件 HTML 结构正确；attach_image=false 时不出现内嵌图 cid；image_cid 传入时匹配
- 本地存档 md 含全部链接与统计
- HTML 转义（标题含标签/引号不注入）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import Suite, make_digest, make_item, real_state_snapshot, workspace  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.presentation.render_text import (  # noqa: E402
    MAX_WECOM_BYTES,
    render_archive_md,
    render_email_html,
    render_wecom_parts,
    save_digest_files,
)

WECOM_LIMIT = 4096  # 企业微信 markdown 硬上限


def main() -> int:
    suite = Suite("m3_render_text")
    before = real_state_snapshot()

    digest = make_digest(top_count=12, brief_count=12)

    # ---------- 1. 企微 markdown ----------
    parts = render_wecom_parts(digest)
    joined = "\n\n".join(parts)
    sizes = [len(part.encode("utf-8")) for part in parts]
    suite.check(
        "2.1 分片数量与字节上限",
        all(size <= WECOM_LIMIT for size in sizes),
        f"parts={len(parts)} sizes={sizes} (内部上限 {MAX_WECOM_BYTES})",
    )
    suite.check(
        "2.2 首片含日期标题",
        "AI 热点日报" in parts[0] and "2026年9月24日" in parts[0],
        parts[0].splitlines()[0] if parts else "",
    )
    suite.check(
        "2.3 含今日综述",
        "今日综述" in joined and digest.summary in joined,
        f"summary_in={digest.summary in joined}",
    )
    suite.check(
        "2.4 综述为完整原文（未被截断/JSON 包装）",
        digest.summary in joined and "{" not in digest.summary,
        f"len={len(digest.summary)}",
    )
    top_urls = [item.url for item in digest.top_items]
    brief_urls = [item.url for item in digest.briefs]
    missing_top = [url for url in top_urls if url not in joined]
    missing_brief = [url for url in brief_urls if url not in joined]
    suite.check(
        "2.5 Top 条目链接全部存在",
        not missing_top,
        f"missing={missing_top}",
    )
    suite.check(
        "2.6 简讯链接全部存在",
        not missing_brief,
        f"missing={missing_brief}",
    )
    link_count = len(re.findall(r"\[[^\]]+\]\(https?://[^)]+\)", joined))
    suite.check(
        "2.7 markdown 可点击语法 [标题](链接)",
        link_count >= len(top_urls) + len(brief_urls),
        f"links={link_count} 期望≥{len(top_urls) + len(brief_urls)}",
    )
    suite.check(
        "2.8 序号与分类标签",
        "**1. [" in joined
        and "🏷" in joined
        and "🔥 今日必读" in joined
        and "📎 其他值得一看" in joined,
        "包含编号/标签/分节",
    )
    suite.check(
        "2.9 续片带续接标题（多片时）",
        len(parts) == 1 or all("（续）" in part for part in parts[1:]),
        f"parts={len(parts)}",
    )
    suite.check(
        "2.10 统计页脚与 stats 一致",
        f"共扫描 {digest.stats['scanned']} 条" in joined
        and f"精选 {digest.stats['selected']} 条" in joined,
        "页脚统计命中",
    )

    # ---------- 2. 极端长文（每块超上限） ----------
    huge_top = [
        make_item(
            200 + i,
            title=f"超长条目 {i}：" + "很长的标题" * 40,
            summary="长摘要" * 1200,  # 约 3600 汉字 ≈ 10KB
        )
        for i in range(1, 13)
    ]
    huge = make_digest(
        top_count=0, brief_count=0, top_items=huge_top, briefs=[], summary="超长综述" * 800
    )
    huge_parts = render_wecom_parts(huge)
    huge_sizes = [len(part.encode("utf-8")) for part in huge_parts]
    huge_joined = "\n\n".join(huge_parts)
    suite.check(
        "2.11 极端长文：每条消息 ≤ 4096 字节",
        all(size <= WECOM_LIMIT for size in huge_sizes),
        f"parts={len(huge_parts)} max={max(huge_sizes)} sizes={huge_sizes}",
    )
    suite.check(
        "2.12 极端长文：全部链接仍在（截断不丢 URL）",
        all(item.url in huge_joined for item in huge_top),
        f"missing={[i.url for i in huge_top if i.url not in huge_joined]}",
    )
    suite.check(
        "2.13 极端长文：发生截断时保留省略号标记",
        "…" in huge_joined,
        f"ellipsis={'…' in huge_joined}",
    )
    suite.evidence(
        "2-wecom-extreme-parts.txt",
        "\n\n".join(
            f"=== PART {index} ({len(part.encode('utf-8'))} bytes) ===\n{part[:400]}…"
            for index, part in enumerate(huge_parts, 1)
        ),
    )
    suite.evidence(
        "2-wecom-normal-parts.txt",
        "\n\n".join(
            f"=== PART {index} ({len(part.encode('utf-8'))} bytes) ===\n{part}"
            for index, part in enumerate(parts, 1)
        ),
    )

    # ---------- 3. 邮件 HTML ----------
    html_no_image = render_email_html(digest, image_cid=None)
    suite.check(
        "2.14 邮件 HTML 结构（doctype/html/head/body）",
        html_no_image.lstrip().startswith("<!DOCTYPE html>")
        and '<html lang="zh-CN">' in html_no_image
        and "<head>" in html_no_image
        and "<body" in html_no_image
        and html_no_image.rstrip().endswith("</html>"),
        f"bytes={len(html_no_image.encode('utf-8'))}",
    )
    suite.check(
        "2.15 attach_image=false → 不含内嵌图 cid",
        "cid:" not in html_no_image and "<img" not in html_no_image,
        f"cid={'cid:' in html_no_image} img={'<img' in html_no_image}",
    )
    suite.check(
        "2.16 邮件含综述与全部 Top 标题",
        digest.summary in html_no_image
        and all((item.cn_title or item.title) in html_no_image for item in digest.top_items),
        "综述+Top 标题命中",
    )
    suite.check(
        "2.17 邮件含全部简讯标题与链接",
        all(item.url in html_no_image for item in digest.briefs)
        and all((item.cn_title or item.title) in html_no_image for item in digest.briefs),
        f"briefs={len(digest.briefs)}",
    )
    suite.check(
        "2.18 邮件页脚统计与 stats 一致",
        f"共扫描 {digest.stats['scanned']} 条" in html_no_image
        and f"精选 {digest.stats['selected']} 条" in html_no_image,
        "统计命中",
    )
    html_with_image = render_email_html(digest, image_cid="digest-image-1")
    suite.check(
        "2.19 image_cid 传入时内嵌图占位正确",
        '<img src="cid:digest-image-1"' in html_with_image,
        "cid 命中",
    )

    # ---------- 4. HTML 转义（对抗：标题含标签/引号） ----------
    evil = make_item(300, title='<script>alert("x")</script> & "quoted"')
    evil_digest = make_digest(
        top_count=0,
        brief_count=0,
        top_items=[evil],
        briefs=[],
        summary="</div><script>bad()</script>",
    )
    evil_html = render_email_html(evil_digest)
    suite.check(
        "2.20 邮件 HTML 转义（无脚本注入）",
        "<script>" not in evil_html and "&lt;script&gt;" in evil_html,
        f"script_raw={'<script>' in evil_html} escaped={'&lt;script&gt;' in evil_html}",
    )

    # ---------- 5. 本地存档 md ----------
    md = render_archive_md(digest)
    suite.check(
        "2.21 存档 md 含标题/综述/全部链接/统计",
        md.startswith("# AI 热点日报")
        and digest.summary in md
        and all(item.url in md for item in digest.top_items + digest.briefs)
        and "共扫描 300 条" in md,
        f"bytes={len(md.encode('utf-8'))}",
    )

    # ---------- 6. 落盘（save_digest_files） ----------
    root = workspace("render-text-files")
    out_dir = root / "digests"
    files = save_digest_files(digest, out_dir)
    md_path = Path(files["markdown"])
    suite.check(
        "2.22 save_digest_files 写出 md 且 digest.files 更新",
        md_path.exists()
        and md_path.name == "2026-09-24.md"
        and digest.files.get("markdown") == str(md_path),
        f"files={files}",
    )
    suite.evidence(
        "2-email-no-image.html",
        html_no_image,
    )
    suite.evidence(
        "2-email-with-image.html",
        html_with_image,
    )

    after = real_state_snapshot()
    suite.check("2.23 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
