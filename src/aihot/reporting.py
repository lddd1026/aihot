"""稳定性报告（M5）：把 runs / digests 汇总成 SLO 对照表。

供观察期使用：`aihot report --days 14`，直接对照 PLAN.md §2 的验收标准。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aihot.store import Store
from aihot.utils import parse_datetime

SLO_DELIVERY_RATE = 0.99
SLO_ON_TIME_RATE = 0.95
SLO_DAILY_COST_CNY = 1.0
SLO_MAX_DURATION_SECONDS = 25 * 60
SLO_ARCHIVE_DAYS = 90
DEFAULT_DELIVER_BY = "08:00"


def parse_clock(value: str) -> tuple[int, int]:
    """把 HH:MM 解析成 (小时, 分钟)；非法时回退到 08:00。"""
    try:
        hours, minutes = value.strip().split(":")
        return int(hours), int(minutes)
    except (ValueError, AttributeError):
        return 8, 0


@dataclass(slots=True)
class RunDay:
    """某一天的运行汇总。"""

    day: date
    runs: int = 0
    delivered_runs: int = 0
    on_time_runs: int = 0
    skipped_runs: int = 0
    duration_seconds: float = 0.0
    cost_cny: float = 0.0
    selected: int = 0
    briefs: int = 0
    papers: int = 0
    image_ok: bool = True
    status: str = "unknown"
    errors: list[str] = field(default_factory=list)

    @property
    def delivered(self) -> bool:
        return self.delivered_runs > 0

    @property
    def skipped(self) -> bool:
        """当天被跳过（预检失败等）且最终没有送达。"""
        return self.skipped_runs > 0 and self.delivered_runs == 0

    @property
    def on_time(self) -> bool:
        return self.on_time_runs > 0

    @property
    def duplicate(self) -> bool:
        """同一天推送多次（幂等性违背）。"""
        return self.delivered_runs > 1


@dataclass(slots=True)
class SloReport:
    window_start: date
    window_end: date
    days: int
    run_days: list[RunDay]
    archive_days: int
    deliver_by: str = DEFAULT_DELIVER_BY

    # ---------- 指标 ----------

    @property
    def delivered_days(self) -> int:
        return sum(1 for day in self.run_days if day.delivered)

    @property
    def on_time_days(self) -> int:
        return sum(1 for day in self.run_days if day.delivered and day.on_time)

    @property
    def delivery_rate(self) -> float:
        return self.delivered_days / self.days if self.days else 0.0

    @property
    def on_time_rate(self) -> float:
        return self.on_time_days / self.days if self.days else 0.0

    @property
    def duplicate_days(self) -> list[date]:
        return [day.day for day in self.run_days if day.duplicate]

    @property
    def missing_days(self) -> list[date]:
        seen = {day.day for day in self.run_days}
        span = [self.window_start + timedelta(days=offset) for offset in range(self.days)]
        return [day for day in span if day not in seen]

    @property
    def skipped_days(self) -> list[RunDay]:
        """被跳过且未送达的天数（预检失败等，有记录+原因）。"""
        return [day for day in self.run_days if day.skipped]

    @property
    def failed_days(self) -> list[RunDay]:
        """运行了但未送达的天数（不含被跳过的）。"""
        return [day for day in self.run_days if not day.delivered and not day.skipped]

    @property
    def total_cost_cny(self) -> float:
        return sum(day.cost_cny for day in self.run_days)

    @property
    def daily_cost_cny(self) -> float:
        return self.total_cost_cny / self.days if self.days else 0.0

    @property
    def max_duration_seconds(self) -> float:
        return max((day.duration_seconds for day in self.run_days), default=0.0)

    @property
    def avg_duration_seconds(self) -> float:
        if not self.run_days:
            return 0.0
        return sum(day.duration_seconds for day in self.run_days) / len(self.run_days)

    @property
    def image_fail_days(self) -> list[date]:
        return [day.day for day in self.run_days if not day.image_ok]

    # ---------- 判定 ----------

    def checks(self) -> list[tuple[str, bool, str]]:
        """返回 (指标名, 是否达标, 说明)。"""
        return [
            (
                "送达成功率",
                self.delivery_rate >= SLO_DELIVERY_RATE,
                f"{self.delivered_days}/{self.days} = {self.delivery_rate:.1%}"
                f"（目标 ≥ {SLO_DELIVERY_RATE:.0%}）",
            ),
            (
                f"准点率({self.deliver_by}前)",
                self.on_time_rate >= SLO_ON_TIME_RATE,
                f"{self.on_time_days}/{self.days} = {self.on_time_rate:.1%}"
                f"（目标 ≥ {SLO_ON_TIME_RATE:.0%}）",
            ),
            (
                "幂等(无重复推送)",
                not self.duplicate_days,
                "无重复" if not self.duplicate_days else f"重复天数: {self.duplicate_days}",
            ),
            (
                "端到端耗时",
                self.max_duration_seconds < SLO_MAX_DURATION_SECONDS,
                f"平均 {self.avg_duration_seconds:.0f}s / 最大 {self.max_duration_seconds:.0f}s"
                f"（目标 < {SLO_MAX_DURATION_SECONDS // 60} 分钟）",
            ),
            (
                "日均成本",
                self.daily_cost_cny <= SLO_DAILY_COST_CNY,
                f"¥{self.daily_cost_cny:.3f}/天（目标 ≤ ¥{SLO_DAILY_COST_CNY:.0f}）",
            ),
            (
                "存档完整(内容不丢)",
                self.archive_days >= self.delivered_days,
                f"{self.archive_days} 期存档 / {self.delivered_days} 天送达"
                f"（长期目标 ≥ {SLO_ARCHIVE_DAYS} 天）",
            ),
        ]

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks())

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": [self.window_start.isoformat(), self.window_end.isoformat()],
            "days": self.days,
            "delivered_days": self.delivered_days,
            "on_time_days": self.on_time_days,
            "delivery_rate": round(self.delivery_rate, 4),
            "on_time_rate": round(self.on_time_rate, 4),
            "duplicate_days": [day.isoformat() for day in self.duplicate_days],
            "missing_days": [day.isoformat() for day in self.missing_days],
            "skipped_days": [
                {"day": day.day.isoformat(), "errors": day.errors} for day in self.skipped_days
            ],
            "failed_days": [
                {"day": day.day.isoformat(), "errors": day.errors} for day in self.failed_days
            ],
            "image_fail_days": [day.isoformat() for day in self.image_fail_days],
            "total_cost_cny": round(self.total_cost_cny, 4),
            "daily_cost_cny": round(self.daily_cost_cny, 4),
            "avg_duration_seconds": round(self.avg_duration_seconds, 1),
            "max_duration_seconds": round(self.max_duration_seconds, 1),
            "archive_days": self.archive_days,
            "checks": [
                {"name": name, "passed": ok, "detail": detail} for name, ok, detail in self.checks()
            ],
            "passed": self.passed,
        }


def build_report(
    store: Store,
    *,
    days: int = 14,
    timezone_name: str = "Asia/Shanghai",
    deliver_by: str = DEFAULT_DELIVER_BY,
) -> SloReport:
    """从 runs/digests 汇总最近 days 天的 SLO 报告。

    deliver_by 是"预期送达时间"（HH:MM，按 timezone_name 时区），用于准点率统计。
    """
    tz = ZoneInfo(timezone_name)
    deadline = parse_clock(deliver_by)
    now_local = datetime.now(tz)
    window_end = now_local.date()
    window_start = window_end - timedelta(days=days - 1)
    start_dt = datetime.combine(window_start, datetime.min.time(), tzinfo=tz)
    since = start_dt.astimezone(UTC)

    by_day: dict[date, RunDay] = {}
    for row in store.runs_since(since):
        try:
            stats = json.loads(row.get("stage_stats") or "{}")
        except json.JSONDecodeError:
            stats = {}
        started = parse_datetime(row.get("started_at"))
        if started is None:
            continue
        local_day = started.astimezone(tz).date()
        stage = stats.get("stage")
        if stage == "skip":
            # 跳过运行（预检失败等）：记录在案，方便区分"跳过"与"没运行"
            entry = by_day.setdefault(local_day, RunDay(day=local_day))
            entry.runs += 1
            entry.skipped_runs += 1
            entry.errors.append(str(stats.get("reason") or row.get("error") or "已跳过"))
            entry.status = str(row.get("status") or "skipped")
            continue
        if stage != "run":
            continue
        finished = parse_datetime(row.get("finished_at")) or started
        entry = by_day.setdefault(local_day, RunDay(day=local_day))
        entry.runs += 1
        delivered = stats.get("delivered")
        if delivered is None:
            # 兼容早期记录（尚无 delivered 字段）：按运行状态推断
            delivered = str(row.get("status") or "") == "ok"
        if delivered:
            entry.delivered_runs += 1
        if finished is not None:
            finished_local = finished.astimezone(tz)
            if (finished_local.hour, finished_local.minute) <= deadline:
                entry.on_time_runs += 1
        entry.duration_seconds = max(
            entry.duration_seconds, float(stats.get("duration_seconds") or 0)
        )
        entry.cost_cny += float(stats.get("cost_cny") or 0)
        entry.selected = max(entry.selected, int(stats.get("selected") or 0))
        entry.briefs = max(entry.briefs, int(stats.get("briefs") or 0))
        entry.papers = max(entry.papers, int(stats.get("papers") or 0))
        entry.image_ok = entry.image_ok and bool(stats.get("image_ok", True))
        entry.status = str(row.get("status") or "unknown")
        for error in stats.get("errors") or []:
            if isinstance(error, str):
                entry.errors.append(error)

    return SloReport(
        window_start=window_start,
        window_end=window_end,
        days=days,
        run_days=[by_day[key] for key in sorted(by_day)],
        archive_days=store.digest_count(),
        deliver_by=deliver_by,
    )


def format_report(report: SloReport) -> str:
    """把报告格式化成终端可读文本。"""
    lines = [
        f"aihot report · 稳定性报告"
        f"（{report.window_start} ~ {report.window_end}，{report.days} 天）",
        "",
        f"有运行记录: {len(report.run_days)} 天"
        + (f"（其中跳过 {len(report.skipped_days)} 天）" if report.skipped_days else "")
        + (f"（缺 {len(report.missing_days)} 天）" if report.missing_days else ""),
        f"成功送达  : {report.delivered_days} 天",
        f"按时送达  : {report.on_time_days} 天（{report.deliver_by} 前）",
        "",
        "── SLO 对照 ──",
    ]
    for name, ok, detail in report.checks():
        mark = "[OK ]" if ok else "[ERR]"
        lines.append(f"  {mark} {name:<14} {detail}")

    if report.failed_days:
        lines += ["", "── 未送达明细 ──"]
        for day in report.failed_days:
            reason = "; ".join(day.errors[:2]) if day.errors else day.status
            lines.append(f"  {day.day}  {reason[:80]}")
    if report.skipped_days:
        lines += ["", "── 跳过明细（有记录，含原因）──"]
        for day in report.skipped_days:
            reason = "; ".join(day.errors[:2]) if day.errors else "已跳过"
            lines.append(f"  {day.day}  {reason[:80]}")
    if report.image_fail_days:
        days_text = ", ".join(str(item) for item in report.image_fail_days)
        lines += ["", f"── 长图降级日（已发文字版）: {days_text} ──"]
    if report.missing_days:
        lines += ["", f"── 无运行记录: {', '.join(str(d) for d in report.missing_days)} ──"]

    lines += ["", f"结论: {'✅ 全部达标' if report.passed else '❌ 存在未达标项'}"]
    return "\n".join(lines)
