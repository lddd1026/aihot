"""呈现层：日报组装、文字渲染、长图渲染。"""

from aihot.presentation.compose import (
    DigestStats,
    compose_digest,
    date_display,
    is_publishable,
    issue_number,
    item_links,
    mention_count,
    source_count,
)
from aihot.presentation.render_image import RenderError, render_digest_html, render_images
from aihot.presentation.render_text import (
    render_archive_md,
    render_email_html,
    render_wecom_parts,
    save_digest_files,
)

__all__ = [
    "DigestStats",
    "RenderError",
    "compose_digest",
    "date_display",
    "is_publishable",
    "issue_number",
    "item_links",
    "mention_count",
    "render_archive_md",
    "render_digest_html",
    "render_email_html",
    "render_images",
    "render_wecom_parts",
    "save_digest_files",
    "source_count",
]
