"""M3 验收项 7：幂等（同日重复 run 不重复推送；--dry-run 不发送但产出三件套）。

独立于 m3_e2e 的重复验证：
- 连续 3 次 run：推送/LLM/条目/日报行数均不增长
- 中间插入 dry-run：不发送、三件套产出、不写 runs 行
- fetch 重复执行：条目幂等
- resend 重复执行：按「补发」语义每次发送（记录实测）
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    real_state_snapshot,
    rfc822,
    run_cli,
    workspace,
    write_config,
)


def main() -> int:
    suite = Suite("m3_idempotent")
    before = real_state_snapshot()
    root = workspace("idempotent")
    data_dir = root / "data"
    digests_dir = data_dir / "digests"
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    entries = [
        {
            "title": f"幂等测试 AI 新闻 {index}：模型与产品更新",
            "link": f"https://example.com/idem/{index}",
            "description": "AI model update",
            "pubDate": rfc822(30 + index),
        }
        for index in range(1, 11)
    ]

    with (
        FakeRSS(entries) as rss,
        FakeLLM() as llm,
        FakeWeCom("ok", proxy=True) as wecom,
        FakeSMTP() as smtp,
    ):
        llm.generator = default_llm_generator
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
            wecom_enabled=True,
            wecom_webhook=wecom.url,
            email_enabled=True,
            email_overrides={"smtp_host": "127.0.0.1", "smtp_port": smtp.port, "use_ssl": False},
        )
        env = {"SSL_CERT_FILE": str(CA_PEM), "HTTPS_PROXY": f"http://127.0.0.1:{wecom.port}"}

        first = run_cli(["run", "--config", str(cfg_path)], env_extra=env, timeout=600)
        suite.evidence("1-first-run.txt", first.text)
        snapshot = {
            "wecom": len(wecom.requests),
            "email": len(smtp.messages),
            "llm": llm.request_count,
            "items": db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM items"),
            "digests": db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM digests"),
            "runs": db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM runs"),
        }
        suite.check(
            "7.1 首次 run 成功并产生完整推送与存档",
            first.rc == 0
            and snapshot["wecom"] >= 2
            and snapshot["email"] == 1
            and snapshot["items"] == 10
            and snapshot["digests"] == 1,
            f"snapshot={snapshot}",
        )

        # 连续两次重复 run
        repeats = []
        for index in (2, 3):
            result = run_cli(["run", "--config", str(cfg_path)], env_extra=env, timeout=600)
            repeats.append(result)
            suite.evidence(f"{index}-repeat-run.txt", result.text)
        after = {
            "wecom": len(wecom.requests),
            "email": len(smtp.messages),
            "llm": llm.request_count,
            "items": db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM items"),
            "digests": db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM digests"),
            "runs": db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM runs"),
        }
        suite.check(
            "7.2 连续重复 run：推送数不增长（不重复推送）",
            after["wecom"] == snapshot["wecom"] and after["email"] == snapshot["email"],
            f"wecom {snapshot['wecom']}→{after['wecom']} "
            f"email {snapshot['email']}→{after['email']}",
        )
        suite.check(
            "7.3 连续重复 run：LLM 调用不增长（不重复花钱）",
            after["llm"] == snapshot["llm"],
            f"llm {snapshot['llm']}→{after['llm']}",
        )
        suite.check(
            "7.4 连续重复 run：条目/日报不重复写入",
            after["items"] == snapshot["items"] == 10
            and after["digests"] == snapshot["digests"] == 1,
            f"items {snapshot['items']}→{after['items']} "
            f"digests {snapshot['digests']}→{after['digests']}",
        )

        def stage_count(stage: str) -> int:
            return db_one(
                data_dir / "aihot.db",
                "SELECT COUNT(*) FROM runs WHERE stage_stats LIKE ?",
                (f'%"stage": "{stage}"%',),
            )

        suite.check(
            "7.5 连续重复 run：runs 表按阶段留痕（run/fetch 各 +2）且状态 partial",
            stage_count("run") == 3
            and stage_count("fetch") == 3
            and stage_count("enrich") == 1
            and all("状态: partial" in r.stdout for r in repeats),
            f"run={stage_count('run')} fetch={stage_count('fetch')} enrich={stage_count('enrich')} "
            f"statuses={['partial' if '状态: partial' in r.stdout else '?' for r in repeats]}",
        )

        # dry-run 幂等（不发送、产出三件套、不写 runs）
        for suffix in ("jpg", "md", "html"):
            (digests_dir / f"{today}.{suffix}").unlink()
        runs_before_dry = db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM runs")
        dry = run_cli(["run", "--config", str(cfg_path), "--dry-run"], env_extra=env, timeout=600)
        suite.evidence("4-dry-run.txt", dry.text)
        runs_after_dry = db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM runs")
        suite.check(
            "7.6 dry-run：不发送 + 三件套产出",
            dry.rc == 0
            and len(wecom.requests) == after["wecom"]
            and len(smtp.messages) == after["email"]
            and all((digests_dir / f"{today}.{s}").exists() for s in ("jpg", "md", "html")),
            f"wecom={len(wecom.requests)} email={len(smtp.messages)} "
            f"files={sorted(p.name for p in digests_dir.iterdir())}",
        )
        suite.check(
            "7.7 dry-run：不写 runs 记录",
            runs_after_dry == runs_before_dry,
            f"runs {runs_before_dry}→{runs_after_dry}",
        )
        dry_again = run_cli(
            ["run", "--config", str(cfg_path), "--dry-run"], env_extra=env, timeout=600
        )
        suite.check(
            "7.8 dry-run 重复执行仍不发送（幂等）",
            dry_again.rc == 0
            and len(wecom.requests) == after["wecom"]
            and len(smtp.messages) == after["email"],
            f"wecom={len(wecom.requests)} email={len(smtp.messages)}",
        )

        # fetch 幂等
        fetch1 = run_cli(["fetch", "--config", str(cfg_path)], timeout=300)
        fetch2 = run_cli(["fetch", "--config", str(cfg_path)], timeout=300)
        suite.check(
            "7.9 fetch 重复执行：条目数不变（0 新增）",
            fetch1.rc == 0
            and fetch2.rc == 0
            and db_one(data_dir / "aihot.db", "SELECT COUNT(*) FROM items") == 10
            and "新增 0 条" in fetch2.stdout,
            [line for line in fetch2.stdout.splitlines() if "合计" in line],
        )

        # resend 语义：每次显式补发都会发送
        before_resend = (len(wecom.requests), len(smtp.messages))
        resend1 = run_cli(["resend", "--config", str(cfg_path)], env_extra=env, timeout=600)
        resend2 = run_cli(["resend", "--config", str(cfg_path)], env_extra=env, timeout=600)
        suite.check(
            "7.10 resend 重复执行：按补发语义每次发送（实测记录）",
            resend1.rc == 0
            and resend2.rc == 0
            and len(wecom.requests) > before_resend[0]
            and len(smtp.messages) > before_resend[1],
            f"wecom {before_resend[0]}→{len(wecom.requests)} "
            f"email {before_resend[1]}→{len(smtp.messages)}",
        )

    after_state = real_state_snapshot()
    suite.check("7.11 真实库/存档未改动", before == after_state, f"db={after_state['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
