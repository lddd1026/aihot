"""M5 声明 7 独立验收：报告能区分「跳过（有原因）」与「完全没运行」。

构造（临时目录 + 临时库，不碰真实 data/aihot.db）：
- 今天      ：真实执行 `aihot skip --reason ...` → skipped 记录（产品自己写入）
- 今天-1    ：种子 skipped（换一种原因）
- 今天-2    ：种子 delivered run（按时送达）
- 今天-3    ：种子 failed run（运行了但没送达）
- 今天-4    ：完全没有记录 → 唯一的「缺 1 天」
对抗：
- 同一天既有 skipped 又有 delivered → 必须按「已送达」计，不计入跳过/缺失。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_report.py
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402

SKIP_REASON_REAL = "M5R 夹具：预检 LLM 不可达（真实 skip 命令写入）"
SKIP_REASON_SEED = "M5R 夹具：预检代理+LLM 同时不可用（种子）"
TODAY = m5.datetime.now(m5.CN_TZ).date()


def run_report(cfg: Path) -> tuple[dict, str]:
    result = m3.run_cli(
        ["report", "--days", "5", "--json", "--config", str(cfg)],
        env_extra=m5.clean_proxy_env(),
        timeout=120,
    )
    try:
        payload = json.loads(result.stdout[result.stdout.index("{") :])
    except (ValueError, json.JSONDecodeError):
        payload = {}
    text_result = m3.run_cli(
        ["report", "--days", "5", "--config", str(cfg)],
        env_extra=m5.clean_proxy_env(),
        timeout=120,
    )
    return payload, text_result.stdout + "\n--- stderr ---\n" + text_result.stderr


def main() -> int:
    suite = m3.Suite("m5r_report")
    before = m3.real_state_snapshot()
    ws = m3.workspace("m5r-report")
    cfg = ws / "config.yaml"
    db = ws / "data" / "aihot.db"
    raw = m5.build_raw(
        ws,
        proxy_url=f"http://127.0.0.1:{m5.BAD_PORT}",
        llm_base_url=f"http://127.0.0.1:{m5.BAD_PORT}/v1",
        wecom_enabled=False,
    )
    m5.m4.dump_config(cfg, raw)

    # ---------- 今天：真实 skip 命令 ----------
    result = m3.run_cli(
        ["skip", "--reason", SKIP_REASON_REAL, "--config", str(cfg)],
        env_extra=m5.clean_proxy_env(),
        timeout=120,
    )
    suite.evidence("0-skip-command.txt", result.text)
    skipped_rows = m3.db_one(db, "SELECT COUNT(*) FROM runs WHERE status = 'skipped'")
    suite.check(
        "7.1 真实 skip 命令 rc=0 且写入 1 条 skipped 记录",
        result.rc == 0 and skipped_rows == 1,
        f"rc={result.rc}；skipped_rows={skipped_rows}",
        evidence=result.text,
    )

    # ---------- 种子数据 ----------
    d1, d2, d3, d4 = (TODAY - timedelta(days=n) for n in (1, 2, 3, 4))
    m5.insert_run(
        db,
        run_id="m5r-seed-skip-d1",
        started_at=m5.local_moment(d1, 17, 0),
        finished_at=m5.local_moment(d1, 17, 0, 2),
        status="skipped",
        stage_stats={"stage": "skip", "reason": SKIP_REASON_SEED},
    )
    m5.insert_run(
        db,
        run_id="m5r-seed-run-d2",
        started_at=m5.local_moment(d2, 17, 0),
        finished_at=m5.local_moment(d2, 17, 1, 20),
        status="ok",
        stage_stats={
            "stage": "run",
            "digest_date": d2.isoformat(),
            "selected": 12,
            "briefs": 12,
            "papers": 2,
            "delivered": True,
            "delivery_errors": [],
            "image_ok": True,
            "duration_seconds": 80.0,
            "cost_cny": 0.15,
            "errors": [],
        },
    )
    m5.insert_run(
        db,
        run_id="m5r-seed-failed-d3",
        started_at=m5.local_moment(d3, 17, 0),
        finished_at=m5.local_moment(d3, 17, 3),
        status="failed",
        stage_stats={
            "stage": "run",
            "digest_date": d3.isoformat(),
            "selected": 0,
            "briefs": 0,
            "delivered": False,
            "delivery_errors": ["企业微信: 独立夹具模拟推送失败"],
            "image_ok": False,
            "duration_seconds": 180.0,
            "cost_cny": 0.0,
            "errors": ["日报推送失败"],
        },
    )

    payload, text = run_report(cfg)
    suite.evidence("1-report-after-seed.json", json.dumps(payload, ensure_ascii=False, indent=2))
    suite.evidence("2-report-after-seed.txt", text)

    skipped = {row["day"]: row for row in payload.get("skipped_days", [])}
    missing = payload.get("missing_days", [])
    failed = {row["day"]: row for row in payload.get("failed_days", [])}
    line = next((row for row in text.splitlines() if "有运行记录" in row), "")

    suite.check(
        "7.2 跳过日显示为「跳过 + 原因」（今天 + 昨天）",
        set(skipped) == {TODAY.isoformat(), d1.isoformat()}
        and any(SKIP_REASON_REAL in str(row["errors"]) for row in skipped.values())
        and any(SKIP_REASON_SEED in str(row["errors"]) for row in skipped.values()),
        f"skipped_days={sorted(skipped)}",
        evidence=text,
    )
    suite.check(
        "7.3 只有完全没有记录的日子计入「缺 N 天」（仅今天-4）",
        missing == [d4.isoformat()],
        f"missing_days={missing}",
    )
    suite.check(
        "7.4 文本头显示「其中跳过 2 天」与「缺 1 天」",
        "其中跳过 2 天" in line and "缺 1 天" in line,
        f"header={line.strip()}",
    )
    suite.check(
        "7.5 运行了但没送达的日子归入「未送达」而非「跳过」/「缺失」",
        set(failed) == {d3.isoformat()}
        and d3.isoformat() not in skipped
        and d3.isoformat() not in missing,
        f"failed_days={sorted(failed)}",
    )
    suite.check(
        "7.6 文本报告含跳过明细段，且原因可读",
        "跳过明细" in text and SKIP_REASON_REAL[:12] in text and SKIP_REASON_SEED[:12] in text,
        "跳过明细段落齐全",
    )
    suite.check(
        "7.7 送达日统计不被跳过干扰（delivered_days=1）",
        payload.get("delivered_days") == 1,
        f"delivered_days={payload.get('delivered_days')}",
    )

    # ---------- 对抗：同一天既有 skip 又有 delivered ----------
    m5.insert_run(
        db,
        run_id="m5r-seed-skip-d2-mixed",
        started_at=m5.local_moment(d2, 17, 5),
        finished_at=m5.local_moment(d2, 17, 5, 3),
        status="skipped",
        stage_stats={"stage": "skip", "reason": "M5R 夹具：同日第二次触发被跳过"},
    )
    payload2, text2 = run_report(cfg)
    suite.evidence(
        "3-report-same-day-mixed.json", json.dumps(payload2, ensure_ascii=False, indent=2)
    )
    skipped2 = {row["day"] for row in payload2.get("skipped_days", [])}
    suite.check(
        "7.8 同一天既有跳过又有成功 → 按「已送达」计，不计入跳过/缺失",
        payload2.get("delivered_days") == 1
        and d2.isoformat() not in skipped2
        and d2.isoformat() not in payload2.get("missing_days", []),
        f"skipped={sorted(skipped2)}；missing={payload2.get('missing_days')}",
        evidence=text2,
    )

    suite.check(
        "7.9 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
