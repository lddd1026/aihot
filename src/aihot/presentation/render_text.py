"""文字渲染：企微 markdown / 邮件 HTML / 本地存档 md。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aihot.models import Digest, NewsItem
from aihot.presentation.compose import (
    date_display,
    issue_number,
    item_links,
    mention_count,
    paper_note,
    source_count,
)

TEMPLATE_DIR = Path(__file__).parent / "templates"
MAX_WECOM_BYTES = 3800  # 企微 markdown 上限 4096 字节，留出余量
MAX_WECOM_ITEMS = 12
MAX_WECOM_PARTS = 10  # 安全上限：正常日报 1~3 片，异常内容不至于刷屏

logger = logging.getLogger("aihot.render.text")


@dataclass(slots=True)
class ItemView:
    """模板友好的条目视图（模板里不写业务逻辑）。"""

    title: str
    summary: str
    category: str
    url: str
    mention_text: str
    source_name: str
    links: list[dict[str, str]] = field(default_factory=list)
    note: str = ""


def _to_view(item: NewsItem) -> ItemView:
    links = item_links(item)
    mentions = mention_count(item)
    sources = source_count(item)
    mention_text = f"{mentions} 篇报道 / {sources} 家来源" if mentions > 1 else "单篇报道"
    return ItemView(
        title=(item.cn_title or item.title).strip(),
        summary=(item.cn_summary or "").strip(),
        category=item.category or "其他",
        url=item.url,
        mention_text=mention_text,
        source_name=links[0]["source_id"] if links else item.source_id,
        links=links,
        note=paper_note(item),
    )


def build_views(digest: Digest) -> tuple[list[ItemView], list[ItemView], list[ItemView]]:
    """返回 (精选, 简讯, 论文) 三组视图。"""
    return (
        [_to_view(item) for item in digest.top_items],
        [_to_view(item) for item in digest.briefs],
        [_to_view(item) for item in digest.papers],
    )


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _template_context(digest: Digest) -> dict[str, object]:
    top, briefs, papers = build_views(digest)
    return {
        "date_text": date_display(digest.digest_date),
        "issue_no": issue_number(digest.digest_date),
        "summary": digest.summary,
        "top_items": top,
        "briefs": briefs,
        "papers": papers,
        "stats": digest.stats,
    }


# ---------- 企微 markdown ----------


def _format_wecom_item(index: int, view: ItemView) -> str:
    lines = [f"**{index}. [{view.title}]({view.url})**"]
    if view.summary:
        lines.append(view.summary)
    lines.append(f"🏷 {view.category} · {view.mention_text}")
    return "\n".join(lines)


def _fit(block: str, max_bytes: int) -> str:
    """单块超过上限时按字节截断（保证不产生超长消息）。"""
    encoded = block.encode("utf-8")
    if len(encoded) <= max_bytes:
        return block
    return encoded[: max_bytes - 3].decode("utf-8", errors="ignore") + "…"


def _pack(blocks: list[str], max_bytes: int, *, continuation: str = "") -> list[str]:
    """按字节上限把内容块打包成多条消息。"""
    parts: list[str] = []
    current: list[str] = []
    for block in blocks:
        fitted = _fit(block, max_bytes)
        candidate = "\n\n".join([*current, fitted])
        if current and len(candidate.encode("utf-8")) > max_bytes:
            parts.append("\n\n".join(current))
            current = [continuation, fitted] if continuation else [fitted]
        else:
            current.append(fitted)
    if current:
        parts.append("\n\n".join(current))
    return [part for part in parts if part.strip()]


def render_wecom_parts(digest: Digest, *, max_bytes: int = MAX_WECOM_BYTES) -> list[str]:
    """渲染企微 markdown（含综述与论文精选），超长自动分片且每条都在字节上限内。"""
    context = _template_context(digest)
    top: list[ItemView] = context["top_items"]  # type: ignore[assignment]
    briefs: list[ItemView] = context["briefs"]  # type: ignore[assignment]
    papers: list[ItemView] = context["papers"]  # type: ignore[assignment]
    date_text = context["date_text"]

    blocks: list[str] = [f"📌 **{date_text} AI 热点日报**"]
    if digest.summary:
        blocks.append(f"**今日综述**\n{digest.summary}")
    if top:
        blocks.append("**🔥 今日必读**")
        blocks.extend(
            _format_wecom_item(index, view)
            for index, view in enumerate(top[:MAX_WECOM_ITEMS], start=1)
        )
    if papers:
        blocks.append("**🔬 论文精选**")
        for view in papers:
            lines = [f"📄 [{view.title}]({view.url})"]
            if view.summary:
                lines.append(view.summary)
            lines.append(f"🏷 {view.note or '论文'}")
            blocks.append("\n".join(lines))
    if briefs:
        blocks.append("**📎 其他值得一看**")
        blocks.extend(f"• [{view.title}]({view.url})（{view.category}）" for view in briefs)
    blocks.append(
        f'<font color="comment">共扫描 {digest.stats.get("scanned", 0)} 条 · '
        f'精选 {digest.stats.get("selected", 0)} 条 · 由 DeepSeek 生成</font>'
    )
    return _cap_parts(_pack(blocks, max_bytes, continuation=f"**（续）{date_text} AI 热点日报**"))


def _cap_parts(parts: list[str]) -> list[str]:
    """限制分片数量；被截断时在末片明确提示（不静默丢内容）。"""
    if len(parts) <= MAX_WECOM_PARTS:
        return parts
    logger.warning("企微分片过多（%d 片），仅发送前 %d 片", len(parts), MAX_WECOM_PARTS)
    kept = parts[:MAX_WECOM_PARTS]
    kept[-1] = kept[-1] + '\n\n<font color="warning">（内容过长，后续条目已省略）</font>'
    return kept


# ---------- 邮件 HTML ----------


def render_email_html(digest: Digest, *, image_cid: str | None = None) -> str:
    template = _environment().get_template("email.html.j2")
    return template.render(**_template_context(digest), image_cid=image_cid)


# ---------- 本地存档 markdown ----------


def render_archive_md(digest: Digest) -> str:
    top, briefs, papers = build_views(digest)
    lines = [
        f"# AI 热点日报 · {date_display(digest.digest_date)}",
        "",
    ]
    if digest.summary:
        lines += ["## 今日综述", "", digest.summary, ""]
    if top:
        lines += ["## 今日必读", ""]
        for index, view in enumerate(top, start=1):
            lines.append(
                f"{index}. **[{view.title}]({view.url})** — {view.category} · {view.mention_text}"
            )
            if view.summary:
                lines.append(f"   - {view.summary}")
        lines.append("")
    if papers:
        lines += ["## 论文精选", ""]
        for view in papers:
            lines.append(f"- **[{view.title}]({view.url})** — {view.note or '论文'}")
            if view.summary:
                lines.append(f"   - {view.summary}")
        lines += [""]
    if briefs:
        lines += ["## 其他值得一看", ""]
        for view in briefs:
            lines.append(f"- [{view.title}]({view.url})（{view.category}）")
        lines += [""]
    lines += [
        "---",
        f"共扫描 {digest.stats.get('scanned', 0)} 条 · "
        f"筛出 {digest.stats.get('clusters', 0)} 个事件 · "
        f"精选 {digest.stats.get('selected', 0)} 条 · 成本 ¥{digest.stats.get('cost_cny', 0)}",
        "",
    ]
    return "\n".join(lines)


def save_digest_files(
    digest: Digest, out_dir: Path, *, image_paths: list[Path] | None = None
) -> dict[str, str]:
    """把日报文字与图片路径写盘，返回文件清单。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = digest.digest_date.isoformat()
    md_path = out_dir / f"{stamp}.md"
    md_path.write_text(render_archive_md(digest), encoding="utf-8")
    files = {"markdown": str(md_path)}
    for index, path in enumerate(image_paths or [], start=1):
        key = "image" if index == 1 else f"image{index}"
        files[key] = str(path)
    digest.files = files
    return files


def digest_date_from(value: str) -> date:
    """解析 YYYY-MM-DD（preview/resend 用）。"""
    return date.fromisoformat(value)
