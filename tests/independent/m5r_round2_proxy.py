"""M5 修复轮第 2 轮复验：P1（显式直连、忽略环境代理）与 P2-1（alert 不再静默）。

P1 覆盖（进程环境同时设置 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY = 本机坏端口）：
- R2.1 CLI：`check --quick` 的 LLM 探测仍到达本地假 LLM（若代理生效必然失败）；
- R2.2 进程内 `checks.probe_wecom`（Config 副本的 webhook 指向本地假企微）仍成功；
- R2.3 进程内 `checks.probe_llm` 仍成功；
- R2.4 进程内 `net.HttpClient(proxy=None)` 仍能直连本地假 RSS；
- R2.5 进程内 `WeComNotifier` 推送仍到达本地假企微；
- R2.6 反证：不设环境代理且目标端口关闭时，probe_wecom 失败、WeComNotifier 抛 NotifyError
  （不是"永远成功"）；
- R2.7 函数级 `runner.send_alert`：企微不可用（本地关闭端口）→ 自动改用邮件（假 SMTP）。

P2-1 覆盖：
- R2.8 `aihot alert` 渠道全灭 → rc=1 且输出含「未启用」「本地兜底」；
       `aihot skip` 路径不受影响 → rc=0 且打印通道为本地文件。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_round2_proxy.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace as dataclasses_replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402

BAD = f"http://127.0.0.1:{m5.BAD_PORT}"
SENTINEL = "M5R-第2轮-直连复验-20261012"


def arm_bad_env_proxies() -> None:
    """在进程环境中设置坏端口代理（Windows 的 os.environ 大小写不敏感，只设大写）。"""
    os.environ["HTTPS_PROXY"] = BAD
    os.environ["HTTP_PROXY"] = BAD
    os.environ["ALL_PROXY"] = BAD
    os.environ.pop("NO_PROXY", None)
    os.environ.pop("no_proxy", None)


def main() -> int:
    suite = m3.Suite("m5r_round2_proxy")
    m5.ensure_certs()
    before = m3.real_state_snapshot()
    ws = m3.workspace("m5r-round2")

    import aihot.checks as checks  # noqa: E402
    from aihot.config import load_config  # noqa: E402
    from aihot.delivery.base import NotifyError  # noqa: E402
    from aihot.delivery.wecom import WeComNotifier  # noqa: E402
    from aihot.net import HttpClient  # noqa: E402
    from aihot.runner import send_alert  # noqa: E402

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
        email_enabled=False,
    )

    # ---------- R2.1 CLI：环境代理为坏端口时 LLM 探测仍成功 ----------
    with m5.m4.FakeLLMProbe() as llm:
        m3.write_config(  # llm.base_url 指向本地假服务
            ws / "config-llm.yaml",
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
            llm_base_url=llm.base_url,
            wecom_enabled=False,
        )
        env = m5.clean_proxy_env(
            {"HTTPS_PROXY": BAD, "HTTP_PROXY": BAD, "ALL_PROXY": BAD}
        )
        result = m3.run_cli(
            ["check", "--quick", "--config", str(ws / "config-llm.yaml")],
            env_extra=env,
            timeout=120,
        )
        suite.evidence("1-check-quick-env-proxy-ignored.txt", result.text)
        suite.check(
            "R2.1 环境 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY 为坏端口时 "
            "check --quick 仍 rc=0 且 LLM [OK ]",
            result.rc == 0 and "[OK ] LLM API" in result.stdout and "[ERR]" not in result.stdout,
            f"rc={result.rc}；"
            + next(
                (ln.strip() for ln in result.stdout.splitlines() if "LLM API" in ln),
                "（无 LLM 行）",
            ),
            evidence=result.text,
        )
        suite.check(
            "R2.1b 假 LLM 确实收到探测（/models 命中 ≥1）",
            llm.models_probes >= 1,
            f"models_probes={llm.models_probes}",
        )

    # ---------- R2.2/R2.4：进程内直连（probe_wecom / WeComNotifier） ----------
    with m5.RecordedWeComProbe("ok") as wecom, m5.m4.FakeLLMProbe() as llm2:
        cfg = m5.local_wecom_config(load_config(cfg_path), wecom.local_url)
        cfg = dataclasses_replace(cfg, llm=dataclasses_replace(cfg.llm, base_url=llm2.base_url))

        arm_bad_env_proxies()
        try:
            probe = checks.probe_wecom(cfg)
            suite.check(
                "R2.2 环境代理坏端口下 probe_wecom 仍到达本地假企微（忽略环境代理）",
                probe.ok and "errcode=0" in probe.detail,
                f"ok={probe.ok}；detail={probe.detail[:80]}",
                evidence=str(wecom.do_GET_calls),
            )
            probe_llm = checks.probe_llm(cfg)
            suite.check(
                "R2.3 环境代理坏端口下 probe_llm 仍到达本地假 LLM",
                probe_llm.ok,
                f"ok={probe_llm.ok}；detail={probe_llm.detail[:80]}",
            )
            with m3.FakeRSS(m5.rss_entries(3, "round2")) as rss:
                client = HttpClient(proxy=None, retries=0)
                try:
                    fetch = client.get(rss.url)
                finally:
                    client.close()
                suite.check(
                    "R2.4 环境代理坏端口下 net.HttpClient(proxy=None) 仍直连本地假 RSS",
                    fetch.ok and rss.hits >= 1,
                    f"ok={fetch.ok}；status={fetch.status}；hits={rss.hits}",
                )

            notifier = WeComNotifier(wecom.local_url)
            try:
                notifier.send_text(SENTINEL)
            finally:
                notifier.close()
            suite.check(
                "R2.5 环境代理坏端口下 WeComNotifier 推送仍到达本地假企微",
                any(
                    SENTINEL in str((row["payload"] or {}).get("text", {}).get("content", ""))
                    for row in wecom.text_requests
                ),
                f"text_requests={len(wecom.text_requests)}",
            )
        finally:
            m5.clean_proxy_env()

        # ---------- 反证：无环境代理 + 目标端口关闭 → 必须失败 ----------
        dead_cfg = m5.local_wecom_config(
            cfg, f"http://127.0.0.1:{m5.BAD_PORT}/cgi-bin/webhook/send?key=dead"
        )
        probe_dead = checks.probe_wecom(dead_cfg)
        suite.check(
            "R2.6 反证：目标端口关闭时 probe_wecom 失败（不是永远成功）",
            not probe_dead.ok,
            f"ok={probe_dead.ok}；detail={probe_dead.detail[:80]}",
        )
        notifier_dead = WeComNotifier(dead_cfg.push.wecom.webhook)
        try:
            try:
                notifier_dead.send_text(SENTINEL)
                raised = ""
            except NotifyError as exc:
                raised = type(exc).__name__
        finally:
            notifier_dead.close()
        suite.check(
            "R2.6b 反证：目标端口关闭时 WeComNotifier 抛 NotifyError",
            raised == "NotifyError",
            f"raised={raised or '（未抛出，异常！）'}",
        )

    # ---------- R2.7 函数级：企微不可用 → 邮件（claim 6 复验） ----------
    with m3.FakeSMTP() as smtp:
        email_cfg = m3.write_config(
            ws / "config-mail.yaml",
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
            email_enabled=True,
            email_overrides=m5.fake_email_cfg(smtp.port),
        )
        mail_cfg = m5.local_wecom_config(
            load_config(email_cfg), f"http://127.0.0.1:{m5.BAD_PORT}/cgi-bin/webhook/send?key=dead"
        )
        m5.clean_proxy_env()
        result6 = send_alert(mail_cfg, "第2轮复验", [SENTINEL])
        suite.evidence(
            "2-send-alert-wecom-down-email.txt",
            f"delivered={result6.delivered}\nchannels={result6.channels}\nerrors={result6.errors}\n"
            f"messages={len(smtp.messages)}",
        )
        suite.check(
            "R2.7 企微不可用（本地关闭端口）→ 自动改用邮件送达",
            result6.delivered
            and result6.channels == ["邮件"]
            and len(smtp.messages) == 1
            and SENTINEL in m5.email_text(smtp.messages[0]["data"]),
            f"channels={result6.channels}；errors={result6.errors}；messages={len(smtp.messages)}",
        )

    # ---------- R2.8/P2-1：alert 不再静默；skip 路径不受影响 ----------
    ws3 = m3.workspace("m5r-round2-p21")
    cfg_p21 = m3.write_config(
        ws3 / "config.yaml",
        data_dir=ws3 / "data",
        log_dir=ws3 / "logs",
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
        email_enabled=False,
    )
    env_clean = m5.clean_proxy_env()
    alert_result = m3.run_cli(
        ["alert", "--message", SENTINEL, "--config", str(cfg_p21)], env_extra=env_clean, timeout=120
    )
    skip_result = m3.run_cli(
        ["skip", "--reason", f"M5 第2轮复验：{SENTINEL}", "--config", str(cfg_p21)],
        env_extra=env_clean,
        timeout=120,
    )
    alerts_file = ws3 / "logs" / "ALERTS.log"
    alerts_tail = (
        alerts_file.read_text(encoding="utf-8") if alerts_file.exists() else "（无 ALERTS.log）"
    )
    suite.evidence(
        "3-p21-alert-and-skip.txt",
        alert_result.text + "\n=== skip ===\n" + skip_result.text + "\n" + alerts_tail,
    )
    suite.check(
        "P2-1 alert 渠道全灭：rc=1 且输出含「未启用」「本地兜底」（不再静默）",
        alert_result.rc == 1
        and "未启用" in alert_result.combined
        and "本地兜底" in alert_result.combined,
        f"rc={alert_result.rc}；stdout={alert_result.stdout.strip()[:160]!r}",
    )
    suite.check(
        "P2-1b skip 路径不受影响：rc=0 且打印通道为本地文件",
        skip_result.rc == 0
        and "已记录跳过" in skip_result.stdout
        and "本地文件" in skip_result.stdout,
        f"rc={skip_result.rc}；stdout={skip_result.stdout.strip()[:160]!r}",
    )
    suite.check(
        "P2-1c 两条命令的兜底告警都写入 logs/ALERTS.log（含哨兵）",
        alerts_file.exists()
        and alerts_file.read_text(encoding="utf-8").count(SENTINEL) >= 2,
        (
            f"ALERTS.log 行数={len(alerts_file.read_text(encoding='utf-8').splitlines())}"
            if alerts_file.exists()
            else "（缺失）"
        ),
    )

    m5.clean_proxy_env()
    suite.check(
        "R2.9 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
