"""M3 用户反馈修复点独立复验（补充套件）。

开发者声明已修复的三点：
① 综述曾被 JSON 包装污染 → 现走非 JSON 模式 + `clean_summary` 兜底
② 序号配色 Top3 红 / 其余蓝
③ 邮件改为纯文字（真实配置 `push.email.attach_image: false`）

本套件独立复验：
- `clean_summary` 对 6 类 JSON 包装/围栏/纯文本输入的清洗结果
- 端到端：假 LLM 故意返回 JSON 包装综述 → 日报综述字段无 JSON 残留
- 模板配色规则 + 真实配置的 attach_image=false 及其邮件效果
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    REAL_CONFIG,
    FakeLLM,
    FakeRSS,
    FakeSMTP,
    Suite,
    default_llm_generator,
    make_digest,
    parse_email,
    real_state_snapshot,
    rfc822,
    run_cli,
    workspace,
    write_config,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.config import EmailConfig, WeComConfig, load_config  # noqa: E402
from aihot.pipeline.enrich import clean_summary  # noqa: E402
from aihot.presentation.render_image import TEMPLATE_DIR  # noqa: E402
from aihot.presentation.render_text import render_email_html  # noqa: E402
from aihot.runner import deliver_digest, render_outputs  # noqa: E402

JSON_WRAPPED = "今日 AI 领域发生多起重要事件：模型发布、开源工具与算力进展齐头并进。"


def main() -> int:
    suite = Suite("m3_fixes")
    before = real_state_snapshot()
    root = workspace("fixes")

    # ---------- ① 综述 JSON 包装清洗 ----------
    cases = {
        "type+content 包装": f'{{"type": "json_object", "content": "{JSON_WRAPPED}"}}',
        "content 包装": f'{{"content": "{JSON_WRAPPED}"}}',
        "summary 包装": f'{{"summary": "{JSON_WRAPPED}"}}',
        "中文键包装": f'{{"综述": "{JSON_WRAPPED}"}}',
        "markdown 围栏 JSON": f'```json\n{{"content": "{JSON_WRAPPED}"}}\n```',
        "纯文本": JSON_WRAPPED,
    }
    for label, raw in cases.items():
        cleaned = clean_summary(raw)
        suite.check(
            f"10.1 综述清洗：{label}",
            cleaned == JSON_WRAPPED,
            f"结果前 30 字={cleaned[:30]!r} 残留花括号={'{' in cleaned}",
        )
    longest_case = '{"a": "短", "b": "' + JSON_WRAPPED + '", "c": "中"}'
    suite.check(
        "10.2 综述清洗：无已知键时取最长字符串值",
        clean_summary(longest_case) == JSON_WRAPPED,
        f"结果前 30 字={clean_summary(longest_case)[:30]!r}",
    )

    # ---------- ① 端到端：假 LLM 返回 JSON 包装综述 ----------
    def wrapped_summary_generator(body: dict | None) -> str:
        messages = (body or {}).get("messages") or []
        system = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "system"
        )
        if "主编" in system:
            return f'{{"type": "json_object", "content": "{JSON_WRAPPED}"}}'
        return default_llm_generator(body)

    entries = [
        {
            "title": f"综述清洗测试 AI 新闻 {index}：模型更新",
            "link": f"https://example.com/fix/{index}",
            "description": "AI model update",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 7)
    ]
    with FakeRSS(entries) as rss, FakeLLM() as llm:
        llm.generator = wrapped_summary_generator
        data_dir = root / "data"
        cfg_path = write_config(
            root / "config.yaml",
            data_dir=data_dir,
            log_dir=root / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake",
                    "type": "rss",
                    "url": rss.url,
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
            llm_base_url=llm.base_url,
        )
        result = run_cli(["run", "--config", str(cfg_path)], timeout=600)
        suite.evidence("10-run-json-summary.txt", result.text)
        archive_md = next((data_dir / "digests").glob("*.md")).read_text(encoding="utf-8")
        suite.check(
            "10.3 端到端：JSON 包装综述落盘后为纯正文",
            result.rc == 0
            and JSON_WRAPPED in archive_md
            and '"content"' not in archive_md
            and "json_object" not in archive_md,
            f"rc={result.rc} 含 json 残留={'"content"' in archive_md}",
        )
        preview = run_cli(["preview", "--config", str(cfg_path), "--no-image"], timeout=300)
        suite.evidence("10-preview-json-summary.txt", preview.text)
        suite.check(
            "10.4 端到端：企微 markdown 预览中综述同样干净",
            preview.rc == 0
            and JSON_WRAPPED in preview.stdout
            and '"content"' not in preview.stdout
            and "json_object" not in preview.stdout,
            f"rc={preview.rc} stdout 含残留={'"content"' in preview.stdout}",
        )

    # ---------- ② 序号配色 ----------
    template = (TEMPLATE_DIR / "digest.html.j2").read_text(encoding="utf-8")
    suite.check(
        "10.5 模板：Top3 红 / 其余蓝 配色规则存在",
        ".rank.top3 { background: #e5484d; }" in template
        and "border-radius: 12px; background: #3370ff" in template,
        "CSS 规则命中",
    )
    suite.check(
        "10.6 模板：仅前 3 条套用 top3（loop.index <= 3）",
        "{% if loop.index <= 3 %} top3{% endif %}" in template,
        "条件命中",
    )

    # ---------- ③ 邮件纯文字 ----------
    real_cfg = load_config(REAL_CONFIG)
    suite.check(
        "10.7 真实配置：push.email.attach_image = false（邮件纯文字）",
        real_cfg.push.email.attach_image is False,
        f"attach_image={real_cfg.push.email.attach_image}",
    )
    digest = make_digest(top_count=3, brief_count=2)
    suite.check(
        "10.8 邮件 HTML：attach_image=false 渲染结果无内嵌图",
        "cid:" not in render_email_html(digest, image_cid=None),
        "无 cid",
    )
    # 编排层：真实配置形态（wecom 关闭、email 假 SMTP、attach_image=false）→ 邮件不含图
    with FakeSMTP() as smtp:
        push = dataclasses.replace(
            real_cfg.push,
            wecom=WeComConfig(enabled=False, webhook=""),
            email=EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp.port,
                use_ssl=False,
                username="sender@example.com",
                password="fake-auth-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com",),
                attach_image=False,
            ),
        )
        app = dataclasses.replace(
            real_cfg.app,
            data_dir=root / "data-realcfg",
            log_dir=root / "logs-realcfg",
        )
        cfg = dataclasses.replace(real_cfg, app=app, push=push)
        rendered = render_outputs(cfg, digest, with_image=True)
        deliver_digest(cfg, digest, rendered)
        message = parse_email(smtp.messages[0]["data"]) if smtp.messages else None
        types = [part.get_content_type() for part in message.walk()] if message else []
        suite.check(
            "10.9 编排层：attach_image=false 邮件不含 image/* 部件",
            message is not None and not any(ctype.startswith("image/") for ctype in types),
            f"content_types={types}",
        )

    after = real_state_snapshot()
    suite.check("10.10 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
