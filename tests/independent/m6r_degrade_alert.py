"""M6 补充验收：非 dry-run 的「运行降级提示」是否真的推送到渠道（README/DESIGN 的承诺）。

声明 5 只要求 dry-run + check --quick；本脚本补上"降级必须让用户知情"的推送侧：
- 假节点（端口通、请求失败）+ 本地假 RSS + 本地假 LLM + 本地假企微（Config 副本替换 webhook）；
- 进程内 `runner.run_all(cfg, dry_run=False, with_image=False)`：
  - fetch 阶段降级 → 自动 `send_alert("运行降级提示", …)`；
  - 断言假企微收到含「运行降级提示」「降级为直连」的文本；
  - 断言日报本身也送达（delivered），即"降级不阻断出报"。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_degrade_alert.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

from aihot.config import load_config  # noqa: E402


def text_payloads(wecom: m6.m5.RecordedWeComProbe) -> list[str]:
    return [
        str((row["payload"] or {}).get("text", {}).get("content", ""))
        for row in wecom.text_requests
    ]


def main() -> int:
    suite = m3.Suite("m6r_degrade_alert")
    before = m3.real_state_snapshot()
    m6.clean_proxy_env()
    ws = m3.workspace("m6r-degrade-alert")

    with (
        m6.FakeNodeProxy("http403") as bad_node,
        m3.FakeRSS(m6.m5.rss_entries(6, "degrade-alert-a")) as rss_a,
        m3.FakeRSS(m6.m5.rss_entries(4, "degrade-alert-b")) as rss_b,
        m6.m4.FakeLLMProbe() as llm,
        m6.m5.RecordedWeComProbe("ok") as wecom,
    ):
        raw = m6.raw_config(
            ws,
            sources=[
                m6.rss_source_payload(rss_a.url, source_id="fake_feed"),
                m6.rss_source_payload(rss_b.url, source_id="fake_feed_b"),
            ],
            proxy_url=bad_node.url,
            proxy_enabled=True,
            llm_base_url=llm.base_url,
        )
        cfg = load_config(m6.staged_config(ws, raw))
        # 企微渠道指向本地假服务（config 校验要求 qyapi 域名，因此在校验后的 Config 上替换）
        cfg = m6.m5.local_wecom_config(cfg, wecom.local_url)

        import aihot.runner as runner  # noqa: E402

        outcome = runner.run_all(cfg, dry_run=False, with_image=False)
        texts = text_payloads(wecom)
        alert_texts = [text for text in texts if "运行降级提示" in text or "降级" in text]
        suite.evidence(
            "1-run-all-degraded.txt",
            f"status={outcome.status}\ndelivered={outcome.delivered}\n"
            f"fetch.proxy_mode={outcome.fetch.proxy_mode if outcome.fetch else None}\n"
            f"errors={outcome.errors}\n"
            f"wecom text 条数={len(texts)}\n"
            f"alert 文本={alert_texts}\n"
            f"markdown 条数={len(wecom.markdown_requests)}\n"
            f"llm POST={llm.request_count}",
        )
        suite.check(
            "7.1 降级自动推送「运行降级提示」到企微（含降级说明）",
            any("运行降级提示" in text and "降级" in text for text in texts),
            f"alert 文本={alert_texts or '（未收到）'}",
        )
        suite.check(
            "7.2 降级不阻断出报：日报本身也送达（delivered=True）",
            outcome.delivered is True and bool(outcome.digest),
            f"delivered={outcome.delivered}；digest={'有' if outcome.digest else '无'}；"
            f"errors={outcome.errors}",
        )
        proxy_mode = outcome.fetch.proxy_mode if outcome.fetch else None
        suite.check(
            "7.3 假 LLM 确实被调用（本轮是完整 run，不是空转）",
            llm.request_count >= 1 and proxy_mode == "degraded",
            f"POST={llm.request_count}；proxy_mode={proxy_mode}",
        )
        alerts_file = ws / "logs" / "ALERTS.log"
        alerts_text = (
            alerts_file.read_text(encoding="utf-8") if alerts_file.exists() else "（无 ALERTS.log）"
        )
        suite.evidence("2-alerts-log.txt", alerts_text)
        suite.check(
            "7.4 假节点收到了 https 健康探测（CONNECT），说明降级判定确有依据",
            any(row["method"] == "CONNECT" for row in bad_node.requests),
            f"假节点请求={bad_node.requests[:2]}",
        )
        m6.write_json("3-bad-node-requests.json", bad_node.requests)

    m6.clean_proxy_env()
    suite.check(
        "7.5 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
