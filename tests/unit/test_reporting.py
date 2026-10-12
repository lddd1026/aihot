"""稳定性报告单元测试（不触网）。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from aihot.models import Digest, RunRecord
from aihot.reporting import SloReport, build_report, format_report
from aihot.store import Store

TZ = ZoneInfo("Asia/Shanghai")


def local_dt(day: date, hour: int, minute: int = 0) -> datetime:
    """构造某天本地时间的 UTC 表示。"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).astimezone(UTC)


def log_run(
    store: Store,
    day: date,
    *,
    finished_hour: int = 7,
    delivered: bool = True,
    duration: float = 60.0,
    cost: float = 0.15,
    selected: int = 12,
    image_ok: bool = True,
    errors: list[str] | None = None,
) -> None:
    started = local_dt(day, 7, 30)
    if delivered:
        store.save_digest(
            Digest(
                digest_date=day,
                summary="综述",
                top_items=[],
                briefs=[],
                stats={"selected": selected},
                files={},
            )
        )
    store.log_run(
        RunRecord(
            run_id=f"{day.isoformat()}-{uuid4().hex[:8]}",
            started_at=started,
            finished_at=local_dt(day, finished_hour, 45),
            status="ok" if delivered else "failed",
            stage_stats={
                "stage": "run",
                "digest_date": day.isoformat(),
                "selected": selected,
                "briefs": 12,
                "papers": 2,
                "delivered": delivered,
                "delivery_errors": [] if delivered else ["企业微信: boom"],
                "image_ok": image_ok,
                "duration_seconds": duration,
                "cost_cny": cost,
                "errors": errors or [],
            },
        )
    )


def test_report_all_good(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        for offset in range(3):
            log_run(store, today - timedelta(days=offset))
        report = build_report(store, days=3, timezone_name="Asia/Shanghai")

    assert report.days == 3
    assert report.delivered_days == 3
    assert report.on_time_days == 3
    assert report.delivery_rate == 1.0
    assert report.daily_cost_cny == 0.15
    assert report.passed is True
    assert report.duplicate_days == []


def test_report_counts_missing_and_failed_days(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today)  # 正常
        log_run(store, today - timedelta(days=1), delivered=False, errors=["推送失败"])
        # today-2 完全没有运行记录
        report = build_report(store, days=3, timezone_name="Asia/Shanghai")

    assert report.delivered_days == 1
    assert report.delivery_rate == 1 / 3
    assert [day.day for day in report.failed_days] == [today - timedelta(days=1)]
    assert report.missing_days == [today - timedelta(days=2)]
    assert report.passed is False


def test_report_detects_duplicate_push(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today)
        log_run(store, today, finished_hour=7)  # 同一天第二次推送
        report = build_report(store, days=1, timezone_name="Asia/Shanghai")

    assert report.duplicate_days == [today]
    assert report.delivered_days == 1  # 天数仍按天计
    assert any("幂等" in name and not ok for name, ok, _ in report.checks())


def test_report_late_delivery_counts_not_on_time(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today, finished_hour=9)  # 9:45 才送达
        report = build_report(store, days=1, timezone_name="Asia/Shanghai")

    assert report.delivered_days == 1
    assert report.on_time_days == 0
    assert report.on_time_rate == 0.0


def test_report_on_time_respects_custom_deadline(tmp_path: Path) -> None:
    """送达时间基准可配置（17:00 发报的场景）。"""
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today, finished_hour=17)  # 17:45 送达
        afternoon = build_report(
            store, days=1, timezone_name="Asia/Shanghai", deliver_by="18:00"
        )
        morning = build_report(
            store, days=1, timezone_name="Asia/Shanghai", deliver_by="08:00"
        )

    assert afternoon.on_time_days == 1
    assert morning.on_time_days == 0
    assert "18:00" in format_report(afternoon)


def test_parse_clock_fallback() -> None:
    from aihot.reporting import parse_clock

    assert parse_clock("17:30") == (17, 30)
    assert parse_clock("bad") == (8, 0)
    assert parse_clock("") == (8, 0)


def test_report_ignores_non_run_stages(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        store.log_run(
            RunRecord(
                run_id="fetch-only",
                started_at=local_dt(today, 7, 30),
                finished_at=local_dt(today, 7, 31),
                status="ok",
                stage_stats={"stage": "fetch", "fetched": 100},
            )
        )
        report = build_report(store, days=1, timezone_name="Asia/Shanghai")

    assert report.run_days == []


def test_report_json_is_serializable(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today, image_ok=False, cost=0.2)
        report = build_report(store, days=1, timezone_name="Asia/Shanghai")

    payload = json.loads(json.dumps(report.as_dict(), ensure_ascii=False))
    assert payload["delivered_days"] == 1
    assert payload["image_fail_days"] == [today.isoformat()]
    assert payload["total_cost_cny"] == 0.2
    assert isinstance(payload["checks"], list)


def test_format_report_contains_key_lines(tmp_path: Path) -> None:
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today)
        report = build_report(store, days=1, timezone_name="Asia/Shanghai")

    text = format_report(report)
    assert "SLO 对照" in text
    assert "送达成功率" in text
    assert "日均成本" in text
    assert "结论" in text


def test_empty_store_report(tmp_path: Path) -> None:
    with Store(tmp_path / "db.sqlite") as store:
        report = build_report(store, days=7, timezone_name="Asia/Shanghai")
    assert report.delivered_days == 0
    assert len(report.missing_days) == 7
    assert report.passed is False
    assert isinstance(report, SloReport)


def test_report_distinguishes_skipped_from_missing(tmp_path: Path) -> None:
    """M5 修复：被跳过的天数有记录+原因，不再混入"无运行记录"。"""
    today = datetime.now(TZ).date()
    yesterday = today - timedelta(days=1)
    with Store(tmp_path / "db.sqlite") as store:
        log_run(store, today)
        store.log_run(
            RunRecord(
                run_id="skip-1",
                started_at=local_dt(yesterday, 17, 0),
                finished_at=local_dt(yesterday, 17, 0),
                status="skipped",
                stage_stats={
                    "stage": "skip",
                    "reason": "preflight failed: LLM or WeCom unreachable",
                },
            )
        )
        report = build_report(store, days=3, timezone_name="Asia/Shanghai")

    assert report.delivered_days == 1
    assert [day.day for day in report.skipped_days] == [yesterday]
    assert yesterday not in report.missing_days, "跳过的日子不应算作「没运行」"
    assert report.failed_days == [], "跳过不是「运行失败」，两者要分开"
    assert report.missing_days == [today - timedelta(days=2)]

    text = format_report(report)
    assert "跳过" in text
    assert "preflight failed" in text
    payload = report.as_dict()
    assert payload["skipped_days"][0]["day"] == yesterday.isoformat()


def test_report_skip_does_not_mask_delivery(tmp_path: Path) -> None:
    """同一天既跳过又成功送达时，仍算送达日（skipped=False）。"""
    today = datetime.now(TZ).date()
    with Store(tmp_path / "db.sqlite") as store:
        store.log_run(
            RunRecord(
                run_id="skip-2",
                started_at=local_dt(today, 17, 0),
                finished_at=local_dt(today, 17, 0),
                status="skipped",
                stage_stats={"stage": "skip", "reason": "preflight failed"},
            )
        )
        log_run(store, today, finished_hour=18)  # 手动补跑成功
        report = build_report(store, days=1, timezone_name="Asia/Shanghai")

    assert report.delivered_days == 1
    assert report.skipped_days == []
    assert report.failed_days == []
