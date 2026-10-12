"""M5 声明 5 独立验收（第 2 轮复验版）：预检致命项失败 → 不进入 run 段 + skip 留痕 + 告警尝试。

主控 P1 修复后企微强制直连（忽略环境代理），配置校验又要求 webhook 以官方域名开头，
因此本套件用**邮件**（本地假 SMTP）观察"告警必须尝试发送"，用本地文件观察"兜底绝不静默"。
企微推送路径的直连验证见 `m5r_round2_proxy.py`。

场景（staged：run_daily.bat 字节复制 + .venv 联接 + 临时 config，全程 localhost）：
- A：llm.base_url 不可达（致命项）；企微关闭、邮件指向本地假 SMTP →
    bat 退出码 2、无 run 段、runs 有 skipped+原因、假 SMTP 收到跳过告警、无日报半成品。
- B（对抗）：邮件也不可达（关闭端口）→ logs/ALERTS.log 兜底写入跳过原因。
- C（对抗）：配置损坏 → bat 仍走 skip 分支，ALERTS.log 兜底写入。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_skip.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402

BAD_PROXY = f"http://127.0.0.1:{m5.BAD_PORT}"
UNREACHABLE_LLM = f"http://127.0.0.1:{m5.BAD_PORT}/v1"
REASON = "preflight failed: LLM or WeCom unreachable"


def runs_rows(root: Path) -> list[dict[str, object]]:
    return m3.db_query(
        root / "data" / "aihot.db",
        "SELECT run_id, status, stage_stats, error FROM runs ORDER BY started_at DESC",
    )


def evidence_with_alerts(text: str, alerts: Path) -> str:
    tail = alerts.read_text(encoding="utf-8") if alerts.exists() else "（无 ALERTS.log）"
    return f"{text}\n--- ALERTS.log ---\n{tail}"


def main() -> int:
    suite = m3.Suite("m5r_skip")
    m5.ensure_certs()
    before = m3.real_state_snapshot()
    bat_sha = m5.m4.bat_sha256()

    # ---------- A：致命项失败 + 邮件告警本地可观察 ----------
    root = m5.TEMPROOT / "m4-bat" / "m5r-skip-a"
    with m3.FakeSMTP() as smtp:
        ws = m3.workspace("m5r-skip-alert-email")
        raw = m5.build_raw(
            ws,
            proxy_url=BAD_PROXY,
            llm_base_url=UNREACHABLE_LLM,
            wecom_enabled=False,
            email=m5.fake_email_cfg(smtp.port),
            data_dir=root / "data",
            log_dir=root / "logs",
        )
        root = m5.m4.stage_bat("m5r-skip-a", raw)
        suite.check(
            "5.0 staged run_daily.bat 与项目 bat 字节一致（sha256 记录）",
            (root / "run_daily.bat").read_bytes() == (m5.PROJECT / "run_daily.bat").read_bytes(),
            f"sha256={bat_sha[:16]}…",
            evidence=bat_sha,
        )
        env = m5.clean_proxy_env({"SSL_CERT_FILE": str(m3.CA_PEM)})
        result = m5.m4.run_bat(root, [], timeout=300, env_extra=env)
        alerts = root / "logs" / "ALERTS.log"
        suite.evidence(
            "1-bat-skip-email.txt", evidence_with_alerts(result["log"] + result["stdout"], alerts)
        )
        log = result["log"]
        suite.check(
            "5.1 预检致命项失败时 bat 退出码=2",
            result["rc"] == 2,
            f"rc={result['rc']}（{result['seconds']}s）",
        )
        suite.check(
            "5.2 bat 未进入 run 段，且标记 PREFLIGHT FAILED",
            "PREFLIGHT FAILED - skip today" in log and "run =====" not in log,
            "无 run 段",
        )
        rows = runs_rows(root)
        skipped = [row for row in rows if row["status"] == "skipped"]
        suite.check(
            "5.3 runs 表出现 status='skipped' 记录",
            len(skipped) == 1,
            f"skipped 行数={len(skipped)}；全部状态={[row['status'] for row in rows]}",
            evidence=str(rows),
        )
        reason_field = next(
            (str(row["stage_stats"]) for row in skipped if REASON[:30] in str(row["stage_stats"])),
            "",
        )
        suite.check(
            "5.4 跳过记录含跳过原因（stage_stats.reason）",
            bool(reason_field) and '"stage": "skip"' in reason_field,
            reason_field[:220] or "（未找到原因）",
        )
        mail_text = m5.email_text(smtp.messages[0]["data"]) if smtp.messages else ""
        suite.check(
            "5.5 告警已尝试发送：本地假 SMTP 收到跳过告警（含原因）",
            len(smtp.messages) == 1 and "跳过" in mail_text and "unreachable" in mail_text,
            f"messages={len(smtp.messages)}",
            evidence=mail_text[:800] or "（无邮件）",
        )
        digests = root / "data" / "digests"
        names = [path.name for path in digests.glob("*")] if digests.exists() else []
        suite.check(
            "5.6 跳过后没有产生日报半成品（无 digests 产物）",
            not names,
            f"digests={names}",
        )
        suite.check(
            "5.6b 邮件送达成功时不应同时写本地 ALERTS.log",
            not alerts.exists(),
            f"ALERTS.log exists={alerts.exists()}",
        )

    # ---------- B：致命项失败 + 告警通道全灭 → 本地告警文件兜底 ----------
    root = m5.TEMPROOT / "m4-bat" / "m5r-skip-b"
    ws = m3.workspace("m5r-skip-alert-file")
    raw = m5.build_raw(
        ws,
        proxy_url=BAD_PROXY,
        llm_base_url=UNREACHABLE_LLM,
        wecom_enabled=False,
        email=m5.fake_email_cfg(m5.BAD_PORT),  # 邮件端口也不可达
        data_dir=root / "data",
        log_dir=root / "logs",
    )
    root = m5.m4.stage_bat("m5r-skip-b", raw)
    result = m5.m4.run_bat(root, [], timeout=300, env_extra=m5.clean_proxy_env())
    alerts = root / "logs" / "ALERTS.log"
    suite.evidence(
        "2-bat-skip-local-file.txt",
        evidence_with_alerts(result["log"] + result["stdout"], alerts),
    )
    suite.check(
        "5.7 告警通道全灭时 bat 仍不进入 run 段（rc=2）",
        result["rc"] == 2 and "run =====" not in result["log"],
        f"rc={result['rc']}",
    )
    suite.check(
        "5.8 跳过后告警降级写本地 logs/ALERTS.log（绝不静默）",
        alerts.exists() and "跳过" in alerts.read_text(encoding="utf-8"),
        (
            f"ALERTS.log 存在={alerts.exists()}；内容="
            f"{alerts.read_text(encoding='utf-8')[:150]!r}"
            if alerts.exists()
            else "（缺失）"
        ),
    )
    rows = runs_rows(root)
    suite.check(
        "5.9 告警全灭时跳过记录仍写入 runs 表（skipped + 原因）",
        any(row["status"] == "skipped" and REASON[:30] in str(row["stage_stats"]) for row in rows),
        f"状态={[row['status'] for row in rows]}",
    )

    # ---------- C：配置损坏 → bat 仍走 skip 分支并兜底留痕 ----------
    ws = m3.workspace("m5r-skip-broken-config")
    raw = m5.build_raw(ws, proxy_url=BAD_PROXY, llm_base_url=UNREACHABLE_LLM)
    root = m5.m4.stage_bat("m5r-skip-c", raw)
    (root / "config.yaml").write_text("app: [unclosed\n  window_hours: 24\n", encoding="utf-8")
    result = m5.m4.run_bat(root, [], timeout=300, env_extra=m5.clean_proxy_env())
    alerts = root / "logs" / "ALERTS.log"
    suite.evidence(
        "3-bat-skip-broken-config.txt",
        evidence_with_alerts(result["log"] + result["stdout"], alerts),
    )
    suite.check(
        "5.10 配置损坏时 bat 仍跳过运行（rc=2，无 run 段）",
        result["rc"] == 2 and "run =====" not in result["log"],
        f"rc={result['rc']}",
    )
    suite.check(
        "5.11 配置损坏时跳过原因兜底写入 logs/ALERTS.log",
        alerts.exists() and "跳过" in alerts.read_text(encoding="utf-8"),
        (
            f"内容={alerts.read_text(encoding='utf-8')[:160]!r}"
            if alerts.exists()
            else "（缺失 logs/ALERTS.log）"
        ),
    )

    suite.check(
        "5.12 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
