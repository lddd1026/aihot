"""M5 声明 6 独立验收（第 2 轮复验版）：告警降级到邮件 / 本地文件 + P2-1 不再静默。

主控 P1 修复后企微强制直连（忽略环境代理），配置校验要求 webhook 以官方域名开头，
本套件无法在不连真实域名的前提下让"企微启用"配置走本地假服务，因此 CLI 级场景统一
关闭企微、用**邮件（本地假 SMTP）**与**本地文件**验证降级链路；
"企微不可用 → 邮件"（enabled 但连不上）用本地 webhook 的 Config 副本在
`m5r_round2_proxy.py` 进程内验证。

场景：
- 1：企微未启用 + 邮件可用 → 邮件送达（rc=0，假 SMTP 收到哨兵）。
- 2：所有可用通道不可达 → rc=1，输出含「未启用」「本地兜底」（M5 修复轮 P2-1），ALERTS.log 写哨兵。
- 3（对抗）：邮件认证失败 → 本地文件兜底。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_alert.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402

BAD_PROXY = f"http://127.0.0.1:{m5.BAD_PORT}"
SENTINEL = "M5R-独立告警哨兵-20261012"


def with_alerts(text: str, alerts: Path) -> str:
    tail = alerts.read_text(encoding="utf-8") if alerts.exists() else "（无 ALERTS.log）"
    return f"{text}\n--- ALERTS.log ---\n{tail}"


def main() -> int:
    suite = m3.Suite("m5r_alert")
    m5.ensure_certs()
    before = m3.real_state_snapshot()

    # ---------- 1：企微未启用 + 邮件可用 → 邮件 ----------
    with m3.FakeSMTP() as smtp:
        ws = m3.workspace("m5r-alert-email")
        cfg = ws / "config.yaml"
        raw = m5.build_raw(
            ws,
            proxy_url=BAD_PROXY,
            llm_base_url=f"http://127.0.0.1:{m5.BAD_PORT}/v1",
            wecom_enabled=False,
            email=m5.fake_email_cfg(smtp.port),
        )
        m5.m4.dump_config(cfg, raw)
        result = m3.run_cli(
            ["alert", "--message", SENTINEL, "--config", str(cfg)],
            env_extra=m5.clean_proxy_env({"SSL_CERT_FILE": str(m3.CA_PEM)}),
            timeout=120,
        )
        alerts = ws / "logs" / "ALERTS.log"
        suite.evidence("1-alert-fallback-email.txt", result.text)
        suite.check(
            "6.1 邮件可用：alert rc=0 并注明邮件通道",
            result.rc == 0 and "邮件" in result.stdout,
            f"rc={result.rc}",
            evidence=result.text,
        )
        suite.check(
            "6.2 假 SMTP 确实收到 1 封告警邮件（含哨兵）",
            len(smtp.messages) == 1
            and SENTINEL in m5.email_text(smtp.messages[0]["data"]),
            f"messages={len(smtp.messages)}",
        )
        suite.check(
            "6.3 邮件送达成功时不应同时写本地 ALERTS.log",
            not alerts.exists(),
            f"ALERTS.log exists={alerts.exists()}",
        )

    # ---------- 2：企微未启用 + 邮件不可达 → 本地文件（P2-1 不再静默） ----------
    ws = m3.workspace("m5r-alert-file")
    cfg = ws / "config.yaml"
    raw = m5.build_raw(
        ws,
        proxy_url=BAD_PROXY,
        llm_base_url=f"http://127.0.0.1:{m5.BAD_PORT}/v1",
        wecom_enabled=False,
        email=m5.fake_email_cfg(m5.BAD_PORT),  # 邮件端口关闭
    )
    m5.m4.dump_config(cfg, raw)
    result = m3.run_cli(
        ["alert", "--message", SENTINEL, "--config", str(cfg)],
        env_extra=m5.clean_proxy_env(),
        timeout=120,
    )
    alerts = ws / "logs" / "ALERTS.log"
    suite.evidence("2-alert-fallback-file.txt", with_alerts(result.text, alerts))
    suite.check(
        "6.4 通道全灭：rc=1 且输出含「未启用」与「本地兜底」（P2-1 修复验证）",
        result.rc == 1 and "未启用" in result.combined and "本地兜底" in result.combined,
        f"rc={result.rc}；stdout={result.stdout.strip()[:160]!r}",
        evidence=result.text,
    )
    suite.check(
        "6.5 本地兜底 logs/ALERTS.log 写入告警（含哨兵，绝不静默）",
        alerts.exists() and SENTINEL in alerts.read_text(encoding="utf-8"),
        (
            f"ALERTS.log={alerts.read_text(encoding='utf-8')[:160]!r}"
            if alerts.exists()
            else "（缺失 logs/ALERTS.log）"
        ),
    )

    # ---------- 3（对抗）：邮件认证失败 → 本地文件 ----------
    with m3.FakeSMTP(reject_auth=True) as smtp:
        ws = m3.workspace("m5r-alert-auth-fail")
        cfg = ws / "config.yaml"
        raw = m5.build_raw(
            ws,
            proxy_url=BAD_PROXY,
            llm_base_url=f"http://127.0.0.1:{m5.BAD_PORT}/v1",
            wecom_enabled=False,
            email=m5.fake_email_cfg(smtp.port),
        )
        m5.m4.dump_config(cfg, raw)
        result = m3.run_cli(
            ["alert", "--message", SENTINEL, "--config", str(cfg)],
            env_extra=m5.clean_proxy_env({"SSL_CERT_FILE": str(m3.CA_PEM)}),
            timeout=120,
        )
        alerts = ws / "logs" / "ALERTS.log"
        suite.evidence("4-alert-email-auth-fail.txt", result.text)
        suite.check(
            "6.7 邮件认证失败也必须兜底本地文件（不是静默）",
            result.rc == 1 and alerts.exists() and SENTINEL in alerts.read_text(encoding="utf-8"),
            f"rc={result.rc}；ALERTS.log exists={alerts.exists()}",
        )

    suite.check(
        "6.8 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
