"""M3 验收项 3：长图渲染（JPEG / 1080px / ≤2MB / 中文可读 / 超长拆 2 张）。

验证手段：
- JPEG 头解析（宽度/高度/体积，纯标准库）
- Playwright + 系统 Edge：把 JPEG 画进 canvas 做像素分析（非空白、红色 Top3 徽标、蓝色普通徽标）
- DOM 级检查：渲染同一模板，读取 .rank 徽标的计算颜色与中文字体可用性
- 内容完整性：拆图计划的 HTML 覆盖全部条目且不重不漏
- 拆图触发：① 真实 2MB 阈值下的超长日报（50 条 × 200 字摘要）
  ② 实测整张/上下半张各质量档体积后取中间阈值，确定性复现同一拆图代码路径

注意：产品 render_images 内部使用 Playwright Sync API，不能与测试自身的
sync_playwright 上下文嵌套 —— 渲染与分析分阶段进行。
"""

from __future__ import annotations

import base64
import sys
import time
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
    QUALITY_LADDER,
    build_plans,
    render_digest_html,
    render_images,
)
from aihot.presentation.render_text import build_views  # noqa: E402


def analyze_jpeg(page, path: Path) -> dict:
    """把 JPEG 交给 Edge 解码并统计像素（data:URL 避免 canvas 污染）。"""
    data_uri = "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return page.evaluate(
        """async (src) => {
            const img = new Image();
            const loaded = await new Promise((resolve) => {
                img.onload = () => resolve(true);
                img.onerror = () => resolve(false);
                img.src = src;
            });
            if (!loaded) return { ok: false };
            const canvas = document.createElement('canvas');
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0);
            const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            let dark = 0, red = 0, blue = 0, colored = 0;
            const total = canvas.width * canvas.height;
            for (let i = 0; i < data.length; i += 4) {
                const r = data[i], g = data[i + 1], b = data[i + 2];
                const luma = 0.299 * r + 0.587 * g + 0.114 * b;
                if (luma < 128) dark++;
                if (luma < 245) colored++;
                if (Math.abs(r - 229) < 40 && Math.abs(g - 72) < 40 && Math.abs(b - 77) < 40) red++;
                if (Math.abs(r - 51) < 45 && Math.abs(g - 112) < 45 && Math.abs(b - 255) < 45)
                    blue++;
            }
            return {
                ok: true, width: canvas.width, height: canvas.height,
                dark_ratio: dark / total, ink_ratio: colored / total,
                red_px: red, blue_px: blue,
            };
        }""",
        data_uri,
    )


def jpeg_sizes(page, html: str) -> dict[int, int]:
    """在测试浏览器里量同一 HTML 在各质量档的 JPEG 体积（bytes）。"""
    page.set_content(html, wait_until="load")
    sizes: dict[int, int] = {}
    for quality in QUALITY_LADDER:
        data = page.screenshot(full_page=True, type="jpeg", quality=quality)
        sizes[quality] = len(data)
    return sizes


def main() -> int:
    suite = Suite("m3_render_image")
    before = real_state_snapshot()

    normal = make_digest(top_count=12, brief_count=12)
    # 超长日报：50 条 × 200 字摘要（≈ 生产上限；真实 2MB 阈值下应自动拆成 2 张）
    mega_top = [
        make_item(
            600 + i,
            title=f"超长日报条目 {i}：某公司发布新一代模型与开源工具链",
            summary="超长日报摘要内容，用于验证真实 2MB 上限下的自动拆图。" * 7,
        )
        for i in range(1, 51)
    ]
    mega_digest = make_digest(
        top_count=0,
        brief_count=0,
        top_items=mega_top,
        briefs=[
            make_item(700 + i, title=f"超长简讯 {i}", category="行业动态") for i in range(1, 13)
        ],
        summary="超长日报综述。" * 60,
    )

    # ---------- 阶段 A：产品渲染（无测试浏览器） ----------
    root = workspace("render-image")
    out_dir = root / "digests"

    start = time.perf_counter()
    paths = render_images(normal, out_dir / "normal")
    normal_seconds = time.perf_counter() - start
    suite.check(
        "3.1 常规日报产出 1 张图",
        len(paths) == 1,
        f"paths={[p.name for p in paths]} ({normal_seconds:.1f}s)",
    )
    info = jpeg_info(paths[0])
    suite.check(
        "3.2 JPEG 格式与宽度 1080px",
        info["soi"] and info["eoi"] and info["width"] == IMAGE_WIDTH and info["height"] > 1000,
        f"width={info['width']} height={info['height']} bytes={info['bytes']}",
    )
    suite.check(
        "3.3 体积 ≤ 2MB",
        info["bytes"] <= MAX_IMAGE_BYTES,
        f"bytes={info['bytes']} ({info['bytes'] / 1024:.0f} KB)",
    )

    start = time.perf_counter()
    mega_paths = render_images(mega_digest, out_dir / "mega")
    mega_seconds = time.perf_counter() - start
    mega_infos = [jpeg_info(path) for path in mega_paths]
    suite.check(
        "3.4 真实 2MB 阈值下：超长日报（50 条）自动拆成 2 张且各张合规",
        len(mega_paths) == 2
        and all(i["bytes"] <= MAX_IMAGE_BYTES and i["width"] == IMAGE_WIDTH for i in mega_infos),
        f"paths={[p.name for p in mega_paths]} "
        f"sizes={[i['bytes'] for i in mega_infos]} ({mega_seconds:.1f}s)",
    )

    # ---------- 内容完整性（模板 HTML 层） ----------
    split_plan = build_plans(mega_digest)[1]
    mega_views_top, mega_views_briefs = build_views(mega_digest)
    plan_htmls = [render_digest_html(mega_digest, **part.context) for part in split_plan]
    all_titles = [item.title for item in mega_views_top]
    middle = (len(mega_views_top) + 1) // 2
    covered = [t for t in all_titles if t in plan_htmls[0]] + [
        t for t in all_titles if t in plan_htmls[1]
    ]
    suite.check(
        "3.5 拆图内容完整（上/下页并集=全部 50 条，不重不漏）",
        sorted(covered) == sorted(all_titles)
        and all(t in plan_htmls[0] for t in all_titles[:middle])
        and all(t in plan_htmls[1] for t in all_titles[middle:])
        and "今日综述" in plan_htmls[0]
        and "今日综述" not in plan_htmls[1]
        and "其他值得一看" in plan_htmls[1]
        and all(item.title in plan_htmls[1] for item in mega_views_briefs),
        f"上页={middle}条 下页={len(all_titles) - middle}条 "
        f"简讯={len(mega_views_briefs)} 覆盖={len(covered)}/{len(all_titles)}",
    )
    normal_views_top, normal_views_briefs = build_views(normal)
    normal_html = render_digest_html(normal, top_items=normal_views_top, briefs=normal_views_briefs)
    suite.check(
        "3.6 渲染 HTML 中文无乱码（无 U+FFFD，标题完整）",
        all("\ufffd" not in html for html in [*plan_htmls, normal_html])
        and all((item.cn_title or item.title) in normal_html for item in normal.top_items),
        "无 U+FFFD 且标题完整",
    )

    # ---------- 阶段 B：测量整张 vs 上下半张体积，求可强制拆图的阈值 ----------
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = playwright.chromium.launch(channel=channel)
                suite.note(f"分析用浏览器内核: {channel}")
                break
            except Exception as exc:  # noqa: BLE001
                suite.note(f"{channel} 启动失败: {type(exc).__name__}: {exc}")
        if browser is None:
            suite.check("3.7 系统 Edge 可用（像素分析）", False, "msedge/chrome 均无法启动")
            return suite.finish()
        page = browser.new_page(
            viewport={"width": IMAGE_WIDTH, "height": 1400}, device_scale_factor=1
        )
        page.set_default_timeout(60_000)

        full_context = build_plans(mega_digest)[0][0].context
        full_sizes = jpeg_sizes(page, render_digest_html(mega_digest, **full_context))
        part_sizes = [jpeg_sizes(page, html) for html in plan_htmls]
        min_full = min(full_sizes.values())
        max_part = max(sizes[QUALITY_LADDER[0]] for sizes in part_sizes)
        threshold = (max_part + min_full) // 2 if max_part < min_full else 0
        suite.note(
            f"整张体积 {full_sizes}；上半张 {part_sizes[0]}；"
            f"下半张 {part_sizes[1]}；阈值={threshold}"
        )

        page.set_content(normal_html, wait_until="load")
        badges = page.evaluate(
            """() => Array.from(document.querySelectorAll('.rank')).map(el => ({
                text: el.textContent.trim(),
                bg: getComputedStyle(el).backgroundColor,
            }))"""
        )
        reds = [b["bg"] for b in badges if b["bg"] == "rgb(229, 72, 77)"]
        blues = [b["bg"] for b in badges if b["bg"] == "rgb(51, 112, 255)"]
        suite.check(
            "3.7 DOM 徽标配色：1~3 红、4+ 蓝",
            [b["text"] for b in badges[:3]] == ["1", "2", "3"]
            and len(reds) == 3
            and len(blues) == len(badges) - 3,
            f"reds={len(reds)} blues={len(blues)} 徽标数={len(badges)}",
        )
        font_info = page.evaluate(
            """() => {
                const style = getComputedStyle(document.querySelector('.title'));
                const ctx = document.createElement('canvas').getContext('2d');
                ctx.font = '33px "Microsoft YaHei"';
                return {
                    titleFont: style.fontFamily,
                    cjkWidth: ctx.measureText('中文字符宽度测试').width,
                };
            }"""
        )
        suite.check(
            "3.8 中文字体可用（测量宽度≈全角）",
            font_info["cjkWidth"] > 33 * 7 * 0.8,
            f"cjkWidth={font_info['cjkWidth']:.1f}px font={font_info['titleFont']}",
        )
        browser.close()

    # ---------- 阶段 C：确定性强制拆图（阈值介于半张 q88 与整张 q64 之间） ----------
    forced_paths: list[Path] = []
    forced_infos: list[dict] = []
    if threshold > 0:
        forced_paths = render_images(mega_digest, out_dir / "forced", max_bytes=threshold)
        forced_infos = [jpeg_info(path) for path in forced_paths]
    suite.check(
        "3.9 强制阈值下自动拆成 2 张且各张 ≤ 阈值（拆图逻辑独立复现）",
        threshold > 0
        and len(forced_paths) == 2
        and forced_paths[0].name.endswith("-1.jpg")
        and forced_paths[1].name.endswith("-2.jpg")
        and all(i["width"] == IMAGE_WIDTH and 0 < i["bytes"] <= threshold for i in forced_infos),
        f"threshold={threshold} sizes={[i['bytes'] for i in forced_infos]}",
    )

    # ---------- 阶段 D：像素分析 ----------
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(
            viewport={"width": IMAGE_WIDTH, "height": 1400}, device_scale_factor=1
        )
        pixels = analyze_jpeg(page, paths[0])
        suite.check(
            "3.10 图像非空白（Edge 可解码 + 有效墨迹）",
            bool(pixels.get("ok")) and pixels["ink_ratio"] > 0.01 and pixels["dark_ratio"] > 0.005,
            f"ink={pixels.get('ink_ratio', 0):.4f} dark={pixels.get('dark_ratio', 0):.4f}",
        )
        suite.check(
            "3.11 序号配色：Top3 红 + 其余蓝（像素级）",
            pixels.get("red_px", 0) > 500 and pixels.get("blue_px", 0) > 500,
            f"red_px={pixels.get('red_px')} blue_px={pixels.get('blue_px')}",
        )
        mega_pixels = [analyze_jpeg(page, path) for path in mega_paths]
        suite.check(
            "3.12 真实拆图两页均有内容（像素级）",
            len(mega_pixels) == 2
            and all(p.get("ok") and p["ink_ratio"] > 0.003 for p in mega_pixels),
            f"ink={[round(p.get('ink_ratio', 0), 4) for p in mega_pixels]}",
        )
        forced_pixels = [analyze_jpeg(page, path) for path in forced_paths]
        suite.check(
            "3.13 强制拆图两页均有内容（像素级）",
            len(forced_pixels) == 2
            and all(p.get("ok") and p["ink_ratio"] > 0.003 for p in forced_pixels),
            f"ink={[round(p.get('ink_ratio', 0), 4) for p in forced_pixels]}",
        )
        browser.close()

    suite.evidence_json(
        "3-image-metrics.json",
        {
            "normal": {"path": str(paths[0]), **info, "pixels": pixels},
            "mega_real_limit": {
                "paths": [str(p) for p in mega_paths],
                "infos": mega_infos,
                "pixels": mega_pixels,
            },
            "mega_forced_split": {
                "threshold": threshold,
                "full_sizes_by_quality": full_sizes,
                "part_sizes_by_quality": part_sizes,
                "parts": forced_infos,
                "pixels": forced_pixels,
            },
            "timings": {
                "normal_seconds": round(normal_seconds, 2),
                "mega_seconds": round(mega_seconds, 2),
            },
            "font": font_info,
        },
    )

    after = real_state_snapshot()
    suite.check("3.14 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
