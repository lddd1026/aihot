"""M3 验收项 6：降级与恢复。

覆盖：
- 图片渲染失败（真实失败路径：病态超长 → RenderError）→ 纯文字推送不中断、内容不丢
- 图片渲染失败（注入路径：render_images 抛 RenderError）→ run_all 状态/错误/推送行为一致
- 推送失败（企微 errcode）→ 日报已存档（三件套 + digests 行）→ resend 补发成功且不重跑 LLM
- 邮件失败不影响企微（渠道独立）
- LLM 全批次失败 → 仍能出日报并推送（原文降级，不崩溃）
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    CA_PEM,
    FakeLLM,
    FakeRSS,
    FakeSMTP,
    FakeWeCom,
    Suite,
    db_one,
    default_llm_generator,
    make_digest,
    make_item,
    parse_email,
    real_state_snapshot,
    rfc822,
    run_cli,
    workspace,
    write_config,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.config import EmailConfig, WeComConfig, load_config  # noqa: E402
from aihot.presentation.render_image import RenderError  # noqa: E402
from aihot.runner import deliver_digest, render_outputs, run_all  # noqa: E402


def local_push(cfg, *, wecom_url: str = "", smtp_port: int = 0, email_enabled: bool = False):
    """把配置里的推送渠道指向本地假服务（夹具构造）。"""
    push = dataclasses.replace(
        cfg.push,
        wecom=WeComConfig(enabled=bool(wecom_url), webhook=wecom_url),
        email=EmailConfig(
            enabled=email_enabled,
            smtp_host="127.0.0.1",
            smtp_port=smtp_port,
            use_ssl=False,
            username="sender@example.com",
            password="fake-auth-code",
            from_addr="sender@example.com",
            to_addrs=("receiver@example.com",),
            attach_image=False,
        ),
    )
    return dataclasses.replace(cfg, push=push)


def main() -> int:
    suite = Suite("m3_degrade")
    before = real_state_snapshot()
    root = workspace("degrade")

    # ---------- 1. 真实渲染失败 → 纯文字推送（病态超长日报） ----------
    cfg_path = write_config(
        root / "cfg-render.yaml", data_dir=root / "data-render", log_dir=root / "logs"
    )
    base_cfg = load_config(cfg_path)
    pathological_top = [
        make_item(
            600 + i,
            title=f"病态条目 {i}：超长内容",
            summary="病态摘要" * 110,
        )
        for i in range(1, 41)
    ]
    pathological = make_digest(
        top_count=0,
        brief_count=0,
        top_items=pathological_top,
        briefs=[
            make_item(700 + i, title=f"病态简讯 {i}", category="行业动态") for i in range(1, 13)
        ],
        summary="病态综述。" * 60,
    )
    with FakeWeCom("ok") as wecom, FakeSMTP() as smtp:
        cfg = local_push(
            base_cfg, wecom_url=wecom.local_url, smtp_port=smtp.port, email_enabled=True
        )
        rendered = render_outputs(cfg, pathological, with_image=True)
        suite.check(
            "6.1 渲染失败：image_paths 为空且记录 image_error（不抛异常）",
            rendered.image_paths == [] and bool(rendered.image_error),
            f"image_error={rendered.image_error[:80]}",
        )
        suite.check(
            "6.2 渲染失败：md/html 三件套仍落盘",
            Path(rendered.files["markdown"]).exists() and Path(rendered.files["html"]).exists(),
            f"files={sorted(rendered.files)}",
        )
        errors = deliver_digest(cfg, pathological, rendered)
        md_text = "\n\n".join(r["payload"]["markdown"]["content"] for r in wecom.markdown_requests)
        from aihot.presentation.render_text import MAX_WECOM_ITEMS  # noqa: E402

        pushed_items = pathological.top_items[:MAX_WECOM_ITEMS]
        suite.check(
            "6.3 渲染失败：纯文字推送继续（无图、有文）且内容完整",
            errors == []
            and len(wecom.image_requests) == 0
            and len(wecom.markdown_requests) >= 1
            and "今日综述" in md_text
            and all(item.url in md_text for item in pushed_items)
            and all(item.url in md_text for item in pathological.briefs),
            f"errors={errors} markdown={len(wecom.markdown_requests)} "
            f"Top 前 {len(pushed_items)} 条链接完整="
            f"{all(item.url in md_text for item in pushed_items)}",
        )
        suite.check(
            "6.4 渲染失败：邮件照常发送（渠道独立）",
            len(smtp.messages) == 1,
            f"emails={len(smtp.messages)}",
        )

    # ---------- 2. 注入渲染失败（render_images 抛 RenderError）→ run_all 一致降级 ----------
    digest = make_digest(top_count=8, brief_count=4)
    with FakeWeCom("ok") as wecom, FakeSMTP() as smtp:
        cfg = local_push(
            base_cfg, wecom_url=wecom.local_url, smtp_port=smtp.port, email_enabled=True
        )
        with mock.patch("aihot.runner.render_images", side_effect=RenderError("注入的渲染失败")):
            outcome = run_all(cfg, dry_run=True, with_image=True)
        suite.check(
            "6.5 注入渲染失败：run_all 不崩溃且错误可见",
            outcome.status == "partial"
            and any("长图渲染失败" in error for error in outcome.errors),
            f"status={outcome.status} errors={outcome.errors}",
        )
        suite.check(
            "6.6 注入渲染失败：文字渲染结果不受影响",
            len(outcome.rendered.wecom_parts) >= 1
            and "AI 热点日报" in outcome.rendered.wecom_parts[0]
            and bool(outcome.rendered.email_html),
            f"parts={len(outcome.rendered.wecom_parts)} "
            f"email_html={len(outcome.rendered.email_html)}",
        )

    # ---------- 3. 推送失败 → 存档 → resend 不重跑 LLM（CLI 级） ----------
    entries = [
        {
            "title": f"降级测试 AI 新闻 {index}：模型与工具更新",
            "link": f"https://example.com/degrade/{index}",
            "description": "AI model update",
            "pubDate": rfc822(40 + index),
        }
        for index in range(1, 13)
    ]
    with (
        FakeRSS(entries) as rss,
        FakeLLM() as llm,
        FakeWeCom("errcode", proxy=True) as wecom,
        FakeSMTP() as smtp,
    ):
        llm.generator = default_llm_generator
        data_dir = root / "data-resend"
        cfg_path = write_config(
            root / "cfg-resend.yaml",
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
            wecom_enabled=True,
            wecom_webhook=wecom.url,
            email_enabled=True,
            email_overrides={"smtp_host": "127.0.0.1", "smtp_port": smtp.port, "use_ssl": False},
        )
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}

        run_fail = run_cli(["run", "--config", str(cfg_path)], env_extra=env, timeout=600)
        suite.evidence("1-run-wecom-fail.txt", run_fail.text)
        llm_after_fail = llm.request_count
        suite.check(
            "6.7 推送失败：run 不崩溃（rc=0/partial）且错误可见",
            run_fail.rc == 0
            and "状态: partial" in run_fail.stdout
            and "企业微信" in run_fail.stdout
            and "errcode=93000" in run_fail.stdout,
            f"rc={run_fail.rc} ({run_fail.seconds:.1f}s)",
        )
        suite.check(
            "6.8 推送失败：邮件渠道仍送达（独立降级）",
            len(smtp.messages) == 1,
            f"emails={len(smtp.messages)}",
        )
        digest_files = sorted(p.name for p in (data_dir / "digests").iterdir())
        suite.check(
            "6.9 推送失败：日报已存档（三件套 + digests 行）",
            any(name.endswith(".jpg") for name in digest_files)
            and any(name.endswith(".md") for name in digest_files)
            and any(name.endswith(".html") for name in digest_files)
            and db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM digests") == 1,
            f"files={digest_files}",
        )

        wecom.mode = "ok"
        resend = run_cli(["resend", "--config", str(cfg_path)], env_extra=env, timeout=600)
        suite.evidence("2-resend-after-fail.txt", resend.text)
        suite.check(
            "6.10 resend 补发成功：图 + markdown 均送达",
            resend.rc == 0
            and "补发完成" in resend.stdout
            and len(wecom.image_requests) >= 1
            and len(wecom.markdown_requests) >= 1,
            f"rc={resend.rc} images={len(wecom.image_requests)} "
            f"markdown={len(wecom.markdown_requests)}",
        )
        suite.check(
            "6.11 resend 不重跑 LLM（计数不变）",
            llm.request_count == llm_after_fail,
            f"llm_before={llm_after_fail} llm_after={llm.request_count}",
        )
        message = parse_email(smtp.messages[0]["data"]) if smtp.messages else None
        resend_html = ""
        if message is not None:
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    resend_html = part.get_content()
        suite.check(
            "6.12 失败前邮件内容与日报一致（内容不丢）",
            message is not None
            and "今日综述" in resend_html
            and "https://example.com/degrade/" in resend_html,
            f"subject={message['Subject'] if message else None}",
        )

    # ---------- 4. 邮件失败不影响企微 ----------
    with FakeWeCom("ok") as wecom, FakeSMTP(reject_auth=True) as smtp:
        cfg = local_push(
            base_cfg, wecom_url=wecom.local_url, smtp_port=smtp.port, email_enabled=True
        )
        rendered = render_outputs(cfg, digest, with_image=False)
        errors = deliver_digest(cfg, digest, rendered)
        suite.check(
            "6.13 邮件失败：企微照常送达、错误被记录（不崩溃）",
            len(errors) == 1 and "邮件" in errors[0] and len(wecom.markdown_requests) >= 1,
            f"errors={errors} markdown={len(wecom.markdown_requests)}",
        )

    # ---------- 5. LLM 全批次失败 → 仍出日报并推送 ----------
    with FakeRSS(entries) as rss, FakeLLM() as llm_bad, FakeWeCom("ok", proxy=True) as wecom2:
        llm_bad.generator = lambda body: "这不是 JSON"
        data_dir = root / "data-llm-fail"
        cfg_path = write_config(
            root / "cfg-llm-fail.yaml",
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
            llm_base_url=llm_bad.base_url,
            wecom_enabled=True,
            wecom_webhook=wecom2.url,
            email_enabled=False,
        )
        env_llm = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom2.port}"}
        run_bad = run_cli(["run", "--config", str(cfg_path)], env_extra=env_llm, timeout=600)
        suite.evidence("3-run-llm-fail.txt", run_bad.text)
        digest_rows = db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM digests")
        suite.check(
            "6.14 LLM 全失败：仍生成日报并推送（原文降级，不崩溃）",
            run_bad.rc == 0
            and not __import__("m3_support").has_traceback(run_bad.combined)
            and digest_rows == 1
            and len(wecom2.markdown_requests) >= 1,
            f"rc={run_bad.rc} digests={digest_rows} markdown={len(wecom2.markdown_requests)}",
        )
        suite.check(
            "6.15 LLM 全失败：推送内容含条目链接（内容不丢）",
            all(
                f"https://example.com/degrade/{index}"
                in "\n\n".join(
                    r["payload"]["markdown"]["content"] for r in wecom2.markdown_requests
                )
                for index in range(1, 13)
            ),
            f"markdown={len(wecom2.markdown_requests)}",
        )

    after = real_state_snapshot()
    suite.check("6.16 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
