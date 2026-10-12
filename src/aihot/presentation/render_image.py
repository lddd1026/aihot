"""长图渲染：Jinja2 → HTML → 系统 Edge 截图 → JPEG（含体积控制与自动拆图）。

设计要点：
- 使用系统自带 Edge（`channel="msedge"`），不额外下载浏览器内核
- 先整张渲染；超过体积上限则降质（88 → 76 → 64）；仍超限则拆成上下两张
- 任何渲染失败都抛 RenderError，由编排层降级为纯文字推送（内容不丢）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aihot.models import Digest
from aihot.presentation.render_text import TEMPLATE_DIR, build_views

logger = logging.getLogger("aihot.render")

IMAGE_WIDTH = 1080
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 企微图片消息上限 2MB
QUALITY_LADDER = (88, 76, 64)
VIEWPORT_HEIGHT = 1400
RENDER_TIMEOUT_MS = 30_000
SPLIT_THRESHOLD = 8  # 超过该条数才考虑拆图（先降质）


class RenderError(Exception):
    """图片渲染失败（调用方应降级为纯文字）。"""


class _TooLargeError(Exception):
    """降质后仍超过体积上限（内部信号，触发拆图）。"""


@dataclass(slots=True)
class _Part:
    filename: str
    context: dict[str, Any]


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_plans(digest: Digest) -> list[list[_Part]]:
    """渲染计划：先整张，超限再拆上下两张。"""
    top, briefs, papers = build_views(digest)
    stamp = digest.digest_date.isoformat()
    full = [
        _Part(
            f"{stamp}.jpg",
            {
                "top_items": top,
                "briefs": briefs,
                "papers": papers,
                "show_summary": True,
                "part_suffix": "",
            },
        )
    ]
    if len(top) <= 1:
        return [full]
    middle = max(1, (len(top) + 1) // 2)
    split = [
        _Part(
            f"{stamp}-1.jpg",
            {
                "top_items": top[:middle],
                "briefs": [],
                "papers": [],
                "show_summary": True,
                "part_suffix": "（上）",
            },
        ),
        _Part(
            f"{stamp}-2.jpg",
            {
                "top_items": top[middle:],
                "briefs": briefs,
                "papers": papers,
                "show_summary": False,
                "part_suffix": "（下）",
            },
        ),
    ]
    return [full, split]


def render_digest_html(
    digest: Digest,
    *,
    top_items: list[Any],
    briefs: list[Any],
    papers: list[Any] | None = None,
    show_summary: bool = True,
    part_suffix: str = "",
) -> str:
    """渲染长图 HTML（不截图）。"""
    from aihot.presentation.compose import date_display, issue_number

    template = _environment().get_template("digest.html.j2")
    return template.render(
        date_text=date_display(digest.digest_date),
        issue_no=issue_number(digest.digest_date),
        summary=digest.summary,
        top_items=top_items,
        briefs=briefs,
        papers=papers or [],
        stats=digest.stats,
        show_summary=show_summary,
        part_suffix=part_suffix,
    )


def render_images(
    digest: Digest,
    out_dir: Path,
    *,
    width: int = IMAGE_WIDTH,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> list[Path]:
    """渲染日报长图，返回图片路径（可能拆成 2 张）。失败抛 RenderError。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    last_error = ""
    for plan in build_plans(digest):
        try:
            return _render_plan(digest, plan, out_dir, width=width, max_bytes=max_bytes)
        except _TooLargeError as exc:
            last_error = str(exc)
            logger.warning("图片体积超限，尝试下一套方案: %s", exc)
        except RenderError:
            raise
        except Exception as exc:  # 浏览器/系统异常统一转成 RenderError
            raise RenderError(f"图片渲染失败: {type(exc).__name__}: {exc}") from exc
    raise RenderError(f"图片体积超限（降质与拆图后仍失败）: {last_error}")


def _render_plan(
    digest: Digest,
    plan: list[_Part],
    out_dir: Path,
    *,
    width: int,
    max_bytes: int,
) -> list[Path]:
    from playwright.sync_api import sync_playwright

    paths: list[Path] = []
    try:
        playwright_ctx = sync_playwright().start()
    except Exception as exc:  # playwright 未安装/驱动异常
        raise RenderError(f"Playwright 启动失败: {type(exc).__name__}: {exc}") from exc
    try:
        browser = _launch_browser(playwright_ctx)
        try:
            page = browser.new_page(
                viewport={"width": width, "height": VIEWPORT_HEIGHT},
                device_scale_factor=1,
            )
            page.set_default_timeout(RENDER_TIMEOUT_MS)
            for part in plan:
                html = render_digest_html(digest, **part.context)
                page.set_content(html, wait_until="load")
                path = out_dir / part.filename
                if not _capture(page, path, max_bytes):
                    raise _TooLargeError(f"{part.filename} 超过 {max_bytes // 1024} KB")
                paths.append(path)
            return paths
        finally:
            browser.close()
    finally:
        playwright_ctx.stop()


def _launch_browser(playwright_ctx: Any) -> Any:
    """优先用系统 Edge，其次用系统 Chrome，最后回退到 Playwright 自带内核。"""
    for channel in ("msedge", "chrome"):
        try:
            return playwright_ctx.chromium.launch(channel=channel)
        except Exception as exc:  # noqa: BLE001 - 逐个候选尝试
            logger.debug("启动 %s 失败: %s", channel, exc)
    try:
        return playwright_ctx.chromium.launch()
    except Exception as exc:
        raise RenderError(
            "无法启动浏览器：请确认已安装 Microsoft Edge，或执行 playwright install chromium"
        ) from exc


def _capture(page: Any, path: Path, max_bytes: int) -> bool:
    """按质量阶梯截图，返回是否满足体积限制。"""
    for quality in QUALITY_LADDER:
        page.screenshot(path=str(path), full_page=True, type="jpeg", quality=quality)
        size = path.stat().st_size if path.exists() else 0
        logger.debug("截图 %s quality=%d size=%d", path.name, quality, size)
        if 0 < size <= max_bytes:
            return True
    return False
