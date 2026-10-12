"""M5 声明 8 补充（第 2 轮复验版）：渲染失败告警在「企微直连」语义下仍会触发。

背景：既有 `m4_alert.py` 4.11/4.13 依赖「配置/环境代理把企微引到假服务」截获告警；
M5 P1 修复后企微强制直连且忽略环境代理，该截获方式彻底失效。
本脚本用**本地 webhook 的 Config 副本**（`m5.local_wecom_config`）重建本地验证：
- 渲染失败 → runner._alert 仍被调用、告警真的到达假企微；
- 记录 partial（仅长图降级且推送成功）是否告警（对照 M4 遗留 P2-6）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_render_alert.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402


def main() -> int:
    suite = m3.Suite("m5r_render_alert")
    m5.ensure_certs()
    before = m3.real_state_snapshot()

    import aihot.runner as runner  # noqa: E402
    from aihot.config import load_config  # noqa: E402
    from aihot.presentation import RenderError  # noqa: E402
    from aihot.runner import EnrichOutcome, FetchOutcome, RenderOutcome  # noqa: E402
    from aihot.sources import SourceStat  # noqa: E402

    ws = m3.workspace("m5r-render-alert")
    with m3.FakeWeCom("ok") as wecom:
        cfg_path = m3.write_config(
            ws / "config.yaml",
            data_dir=ws / "data",
            log_dir=ws / "logs",
            sources=[
                {
                    "id": "fake_feed",
                    "name": "Fake",
                    "type": "rss",
                    "url": "http://127.0.0.1:1/feed.xml",
                    "enabled": True,
                    "weight": 1.0,
                }
            ],
            llm_base_url="http://127.0.0.1:1/v1",
            wecom_enabled=False,
        )
        # 已通过校验的 Config 副本：企微启用并指向本地假服务（localhost-only 夹具）
        cfg = m5.local_wecom_config(load_config(cfg_path), wecom.local_url)
        m5.clean_proxy_env()  # 清掉可能存在的环境代理（新语义下本就不该影响直连）

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
            pending=1, kept=1, clusters=1, news_items=[m3.make_item(1, source_id="fake_feed")]
        )

        def boom(cfg, digest, **kw):
            raise RenderError("M5R 独立测试：模拟渲染失败")

        runner.render_outputs = boom
        try:
            outcome = runner.run_all(cfg, dry_run=False, no_llm=True)
        finally:
            runner.render_outputs = original["render"]

        suite.evidence(
            "1-render-fail-alert.txt",
            f"status={outcome.status}\nerrors={outcome.errors}\n"
            f"text_requests={[r['payload']['text']['content'] for r in wecom.text_requests]}",
        )
        suite.check(
            "8b.1 渲染失败 outcome.status=failed",
            outcome.status == "failed",
            f"status={outcome.status}",
        )
        alert_texts = [
            r
            for r in wecom.text_requests
            if "告警" in (r["payload"] or {}).get("text", {}).get("content", "")
        ]
        suite.check(
            "8b.2 渲染失败告警在直连语义下真的到达企微（本地假服务收到）",
            len(alert_texts) >= 1
            and "渲染失败" in alert_texts[0]["payload"]["text"]["content"],
            f"alert_texts={len(alert_texts)}",
            evidence=(
                alert_texts[0]["payload"]["text"]["content"] if alert_texts else "（无告警）"
            ),
        )

        # 变体：仅长图降级（推送成功）→ 是否告警（对照 M4 遗留 P2-6）
        before_alerts = len(wecom.text_requests)
        runner.render_outputs = lambda cfg, digest, **kw: RenderOutcome(
            wecom_parts=["**M5R 测试日报**（模拟长图降级，文字照常）"],
            email_html="<html></html>",
            image_error="M5R 独立测试：模拟长图失败",
        )
        try:
            outcome2 = runner.run_all(cfg, dry_run=False, no_llm=True)
        finally:
            runner.render_outputs = original["render"]
            runner.fetch_stage = original["fetch"]
            runner.enrich_stage = original["enrich"]

        new_alerts = len(wecom.text_requests) - before_alerts
        suite.evidence(
            "2-partial-image-degrade.txt",
            f"status={outcome2.status}\nerrors={outcome2.errors}\nnew_alerts={new_alerts}",
        )
        suite.check(
            "8b.3 仅长图降级（status=partial，文字已送达）时无告警（记录 M4 遗留 P2-6 现状）",
            outcome2.status == "partial" and new_alerts == 0,
            f"status={outcome2.status}；新增告警={new_alerts}（M4 报告 P2-6 仍未解决）",
        )

    suite.check(
        "8b.4 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
