"""M4 验收 4 独立验证：告警通道（`alert` 子命令 + `runner._alert`）。

企微全部走本地假服务（HTTPS CONNECT 假代理 + 自签 CA），不发真实消息。
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_alert.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    CA_PEM,
    FakeRSS,
    FakeWeCom,
    default_llm_generator,
    make_item,
    real_state_snapshot,
    rfc822,
    run_cli,
    workspace,
    write_config,
)
from m4_support import FakeLLMProbe, Suite, make_m4_config  # noqa: E402

SENTINEL = "M4-独立告警哨兵-20260925"


def main() -> int:
    suite = Suite("m4_alert")
    before = real_state_snapshot()

    # ---------- 1. alert 子命令：payload 与退出码 ----------
    ws = workspace("m4-alert-cli")
    with FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url="http://127.0.0.1:1/v1",
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
        )
        result = run_cli(
            ["alert", "--message", SENTINEL, "--config", str(cfg)], env_extra=env, timeout=120
        )
        suite.check(
            "4.1 alert 发送成功 rc=0", result.rc == 0, f"rc={result.rc}", evidence=result.text
        )
        texts = wecom.text_requests
        suite.check(
            "4.2 收到 msgtype=text 告警消息", len(texts) == 1, f"text_requests={len(texts)}"
        )
        content = texts[0]["payload"]["text"]["content"] if texts else ""
        suite.check(
            "4.3 告警正文含哨兵文本与告警标识",
            SENTINEL in content and "告警" in content and "⚠️" in content,
            f"content={content[:80]!r}",
        )
        suite.check(
            "4.4 告警未走 markdown（msgtype 正确）", len(wecom.markdown_requests) == 0, "markdown=0"
        )

    # ---------- 2. 企微未启用 → rc=1 ----------
    ws = workspace("m4-alert-disabled")
    cfg = make_m4_config(
        ws / "config.yaml",
        llm_base_url="http://127.0.0.1:1/v1",
        wecom_enabled=False,
        data_dir=ws / "data",
        log_dir=ws / "logs",
    )
    result = run_cli(["alert", "--message", SENTINEL, "--config", str(cfg)], timeout=60)
    suite.check(
        "4.5 企微未启用时 alert rc=1 且提示明确",
        result.rc == 1 and "未启用" in result.combined,
        f"rc={result.rc}",
    )

    # ---------- 3. 企微 key 失效 → NotifyError → rc=1 ----------
    ws = workspace("m4-alert-errcode")
    with FakeWeCom("errcode", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url="http://127.0.0.1:1/v1",
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
        )
        result = run_cli(
            ["alert", "--message", SENTINEL, "--config", str(cfg)], env_extra=env, timeout=120
        )
        suite.check(
            "4.6 企微 errcode 非 0 时 alert rc=1",
            result.rc == 1 and "errcode=93000" in result.combined,
            f"rc={result.rc}",
            evidence=result.text,
        )

    # ---------- 4. 推送失败 → runner._alert 被触发（CLI 级 run） ----------
    ws = workspace("m4-alert-push-fail")
    entries = [
        {
            "title": f"告警演练 AI 新闻 {index}：模型与工具更新",
            "link": f"https://example.com/alert/{index}",
            "description": "AI model update",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 13)
    ]
    with (
        FakeRSS(entries) as rss,
        FakeLLMProbe() as llm,
        FakeWeCom("errcode", proxy=True) as wecom,
    ):
        llm.generator = default_llm_generator
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}
        cfg = make_m4_config(
            ws / "config.yaml",
            llm_base_url=llm.base_url,
            wecom_webhook=wecom.url,
            wecom_enabled=True,
            data_dir=ws / "data",
            log_dir=ws / "logs",
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
        )
        result = run_cli(["run", "--config", str(cfg)], env_extra=env, timeout=900)
        suite.evidence("4.7-run-push-fail.txt", result.text)
        suite.check(
            "4.7 推送失败 run 不崩溃（rc=0/partial）",
            result.rc == 0 and "状态: partial" in result.stdout,
            f"rc={result.rc}",
        )
        alert_texts = [
            r
            for r in wecom.text_requests
            if "告警" in (r["payload"] or {}).get("text", {}).get("content", "")
        ]
        suite.check(
            "4.8 推送失败触发了 runner._alert 文本告警",
            len(alert_texts) >= 1 and "推送失败" in alert_texts[0]["payload"]["text"]["content"],
            f"alert_texts={len(alert_texts)}",
        )
        suite.check(
            "4.9 告警正文含错误明细",
            bool(alert_texts) and "企业微信" in alert_texts[0]["payload"]["text"]["content"],
            (alert_texts[0]["payload"]["text"]["content"][:120] if alert_texts else "（无告警）"),
        )

    # ---------- 5. 渲染失败 → runner._alert 是否被触发（进程内） ----------
    ws = workspace("m4-alert-render-fail")
    with FakeWeCom("ok", proxy=True) as wecom:
        env = {"SSL_CERT_FILE": str(CA_PEM)}
        import os

        os.environ["SSL_CERT_FILE"] = str(CA_PEM)
        cfg_path = write_config(
            ws / "config.yaml",
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake",
                    "type": "rss",
                    "url": "http://127.0.0.1:1/feed",
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
            llm_base_url="http://127.0.0.1:1/v1",
            wecom_enabled=True,
            wecom_webhook=wecom.url,
        )
        import yaml

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        raw["proxy"] = {"enabled": True, "url": f"http://127.0.0.1:{wecom.port}"}
        cfg_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        import aihot.runner as runner
        from aihot.config import load_config
        from aihot.presentation import RenderError
        from aihot.runner import EnrichOutcome, FetchOutcome
        from aihot.sources import SourceStat

        cfg = load_config(cfg_path)
        original = {
            "fetch": runner.fetch_stage,
            "enrich": runner.enrich_stage,
            "render": runner.render_outputs,
        }
        runner.fetch_stage = lambda cfg, **kw: FetchOutcome(
            stats=[SourceStat(source_id="fake_feed", name="Fake", ok=True, count=1)],
            window_hours=24,
        )
        runner.enrich_stage = lambda cfg, **kw: EnrichOutcome(
            pending=1, kept=1, clusters=1, news_items=[make_item(1, source_id="fake_feed")]
        )

        def boom(cfg, digest, **kw):
            raise RenderError("独立测试：模拟渲染失败")

        runner.render_outputs = boom
        try:
            outcome = runner.run_all(cfg, dry_run=False, no_llm=True)
        finally:
            runner.render_outputs = original["render"]

        suite.check(
            "4.10 渲染失败 outcome.status=failed",
            outcome.status == "failed",
            f"status={outcome.status}",
        )
        alert_texts = [
            r
            for r in wecom.text_requests
            if "告警" in (r["payload"] or {}).get("text", {}).get("content", "")
        ]
        suite.check(
            "4.11 渲染失败触发 runner._alert（期望符合「失败必有告警」）",
            len(alert_texts) >= 1 and "渲染失败" in alert_texts[0]["payload"]["text"]["content"],
            f"alert_texts={len(alert_texts)}；details={outcome.errors}",
            evidence=(
                alert_texts[0]["payload"]["text"]["content"]
                if alert_texts
                else "（未收到任何告警）"
            ),
        )

        # run 记录核查
        from m4_support import db_one

        failed_runs = db_one(
            ws / "data" / "aihot.db",
            "SELECT COUNT(*) FROM runs WHERE status='failed' AND stage_stats LIKE '%渲染失败%'",
        )
        suite.check(
            "4.12 finally 仍写入 failed run 记录", failed_runs == 1, f"failed_runs={failed_runs}"
        )

        # 变体：长图渲染失败但降级为纯文字（status=partial）是否告警
        from aihot.runner import RenderOutcome

        before_alerts = len(wecom.text_requests)
        runner.render_outputs = lambda cfg, digest, **kw: RenderOutcome(
            wecom_parts=["**测试日报**（模拟长图降级）"],
            email_html="<html></html>",
            image_error="独立测试：模拟长图失败",
        )
        try:
            outcome2 = runner.run_all(cfg, dry_run=False, no_llm=True)
        finally:
            runner.render_outputs = original["render"]
            runner.fetch_stage = original["fetch"]
            runner.enrich_stage = original["enrich"]
        new_alerts = wecom.text_requests[before_alerts:]
        alert_2 = [r for r in new_alerts if (r["payload"] or {}).get("msgtype") == "text"]
        suite.check(
            "4.13 长图降级（partial）是否告警（期望「渲染失败需告警」）",
            len(alert_2) >= 1,
            f"status={outcome2.status} errors={outcome2.errors} alerts={len(alert_2)}",
        )

    after = real_state_snapshot()
    suite.check("4.14 本套件未改动真实库/存档", before == after, "真实库快照前后一致")
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
