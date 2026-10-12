"""M3 验收项 3 补充：长图渲染边界（极端超长时的降级与内容完整性）。

关注两类风险：
1. Chromium 全页截图高度上限（16384px）导致长图**静默截断**（末尾条目/页脚丢失）
2. 拆图后单张仍 >2MB 时的行为（是否降级为纯文字、是否有异常抛出）

方法：
- 用 DOM 量出真实页面高度与每个条目的纵向位置，与产出的 JPEG 高度对比
- 覆盖「真实上限」（--top 50 + 200 字摘要）与「病态超长」（40 条 × 780 字摘要）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    Suite,
    jpeg_info,
    make_digest,
    make_item,
    real_state_snapshot,
    workspace,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.presentation.render_image import (  # noqa: E402
    IMAGE_WIDTH,
    MAX_IMAGE_BYTES,
    RenderError,
    render_digest_html,
    render_images,
)
from aihot.presentation.render_text import build_views  # noqa: E402

CHROMIUM_CLAMP = 16384  # Chromium 全页截图的常见高度上限（纹理尺寸）


def make_extreme(count: int, summary_chars: int, *, briefs: int = 12):
    items = [
        make_item(
            900 + i,
            title=f"边界条目 {i}：某公司发布新一代模型与开源工具链",
            summary="边界测试摘要。" * (summary_chars // 7),
        )
        for i in range(1, count + 1)
    ]
    return make_digest(
        top_count=0,
        brief_count=0,
        top_items=items,
        briefs=[
            make_item(950 + i, title=f"边界简讯 {i}", category="行业动态")
            for i in range(1, briefs + 1)
        ],
        summary="边界测试综述。" * 30,
    )


def main() -> int:
    suite = Suite("m3_render_image_edges")
    before = real_state_snapshot()
    root = workspace("render-image-edges")

    from playwright.sync_api import sync_playwright

    # 真实上限：top_n 最大 50（config 校验 1~50），摘要上限 200 字
    real_max = make_extreme(50, 200)
    # 病态超长：40 条 × 780 字摘要（真实管线不可能产出，用于探测降级行为）
    pathological = make_extreme(40, 780)

    layouts: dict[str, dict] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(
            viewport={"width": IMAGE_WIDTH, "height": 1400}, device_scale_factor=1
        )
        page.set_default_timeout(60_000)
        for name, digest in (("real_max", real_max), ("pathological", pathological)):
            views_top, views_briefs = build_views(digest)
            html = render_digest_html(digest, top_items=views_top, briefs=views_briefs)
            page.set_content(html, wait_until="load")
            metrics = page.evaluate(
                """() => {
                    const items = Array.from(document.querySelectorAll('.item')).map(el => {
                        const rect = el.getBoundingClientRect();
                        return {
                            top: Math.round(rect.top + window.scrollY),
                            bottom: Math.round(rect.bottom + window.scrollY),
                        };
                    });
                    const footer = document.querySelector('.footer').getBoundingClientRect();
                    return {
                        scrollHeight: document.documentElement.scrollHeight,
                        items,
                        footerTop: Math.round(footer.top + window.scrollY),
                        footerBottom: Math.round(footer.bottom + window.scrollY),
                    };
                }"""
            )
            layouts[name] = metrics
        browser.close()

    suite.evidence_json("3e-layouts.json", layouts)

    # ---------- 真实上限（50 条 × 200 字） ----------
    real_paths = render_images(real_max, root / "real-max")
    real_infos = [jpeg_info(path) for path in real_paths]
    layout = layouts["real_max"]
    suite.check(
        "3e.1 真实上限（50 条）：产出 1~2 张且均 ≤2MB / 宽度 1080",
        1 <= len(real_paths) <= 2
        and all(i["bytes"] <= MAX_IMAGE_BYTES and i["width"] == IMAGE_WIDTH for i in real_infos),
        f"paths={[p.name for p in real_paths]} sizes={[i['bytes'] for i in real_infos]} "
        f"heights={[i['height'] for i in real_infos]}",
    )
    total_height = sum(i["height"] or 0 for i in real_infos)
    suite.check(
        "3e.2 真实上限：图片总高度覆盖整页（无静默截断）",
        total_height >= layout["scrollHeight"] - 60,
        f"image_total_height={total_height} scrollHeight={layout['scrollHeight']}"
        f"（分片自带页头，允许略高）",
    )
    suite.check(
        "3e.3 真实上限：页脚位于最后一张图内",
        total_height >= layout["footerBottom"] - 60,
        f"image_total_height={total_height} footerBottom={layout['footerBottom']}",
    )
    # 拆图方案条目完整性（DOM/HTML 层）
    from aihot.presentation.render_image import build_plans  # noqa: E402

    plan = build_plans(real_max)[1]
    real_views_top, real_views_briefs = build_views(real_max)
    plan_htmls = [render_digest_html(real_max, **part.context) for part in plan]
    all_titles = [item.title for item in real_views_top]
    covered = [t for t in all_titles if t in plan_htmls[0]] + [
        t for t in all_titles if t in plan_htmls[1]
    ]
    suite.check(
        "3e.3b 真实上限：拆图上下页并集 = 全部 50 条（不重不漏）",
        sorted(covered) == sorted(all_titles) and len(covered) == 50,
        f"covered={len(covered)}/50 上页含={sum(1 for t in all_titles if t in plan_htmls[0])}",
    )

    # ---------- 病态超长（40 条 × 780 字） ----------
    layout = layouts["pathological"]
    suite.check(
        "3e.5 病态超长：页面高度确实超过 Chromium 截图上限（测试前提成立）",
        layout["scrollHeight"] > CHROMIUM_CLAMP,
        f"scrollHeight={layout['scrollHeight']} clamp={CHROMIUM_CLAMP}",
    )
    outcome: dict = {"error": "", "paths": [], "infos": []}
    try:
        paths = render_images(pathological, root / "pathological")
        outcome["paths"] = [p.name for p in paths]
        outcome["infos"] = [jpeg_info(p) for p in paths]
        degraded = False
    except RenderError as exc:
        outcome["error"] = str(exc)
        degraded = True
    suite.check(
        "3e.6 病态超长：失败时抛 RenderError（可被编排层降级为纯文字，不崩溃）",
        degraded,
        outcome["error"] or f"未抛错 paths={outcome['paths']}",
    )
    suite.evidence_json("3e-pathological-outcome.json", outcome)

    after = real_state_snapshot()
    suite.check("3e.7 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
