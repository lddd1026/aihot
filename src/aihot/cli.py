"""命令行入口。"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from aihot import __version__
from aihot.checks import run_net_checks, run_quick_checks, split_failures
from aihot.config import Config, ConfigError, load_config
from aihot.logging_setup import setup_logging
from aihot.presentation.render_text import digest_date_from
from aihot.reporting import build_report, format_report
from aihot.runner import (
    ALERT_FILE_NAME,
    RunnerError,
    RunOutcome,
    build_digest_from_db,
    deliver_digest,
    digest_date_today,
    enrich_stage,
    fetch_stage,
    load_digest,
    record_skip,
    render_outputs,
    run_all,
    send_alert,
)
from aihot.store import Store
from aihot.utils import mask_url_credentials, redact_text

DEFAULT_CONFIG = "config.yaml"
DEFAULT_PREVIEW_LIMIT = 10
TITLE_PREVIEW_WIDTH = 70
WINDOW_HOURS_MIN = 1
WINDOW_HOURS_MAX = 168

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 3  # 用法错误（argparse）固定为 2，配置错误用 3，便于脚本区分


def _window_hours_arg(value: str) -> int:
    """argparse 类型：窗口小时数必须在 1~168 之间。"""
    try:
        hours = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是整数，实际: {value!r}") from None
    if not WINDOW_HOURS_MIN <= hours <= WINDOW_HOURS_MAX:
        raise argparse.ArgumentTypeError(
            f"必须在 {WINDOW_HOURS_MIN}~{WINDOW_HOURS_MAX} 之间，实际: {hours}"
        )
    return hours


def _limit_arg(value: str) -> int:
    """argparse 类型：非负整数。"""
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是整数，实际: {value!r}") from None
    if limit < 0:
        raise argparse.ArgumentTypeError(f"不能为负数，实际: {limit}")
    return limit


def _ensure_utf8_stdio() -> None:
    """把重定向的 stdout/stderr 固定为 UTF-8，避免 Windows 下 GBK 乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aihot", description="aihot · AI 每日热点雷达")
    _add_common_args(parser, DEFAULT_CONFIG)
    parser.add_argument("--version", action="version", version=f"aihot {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="命令")

    check = subparsers.add_parser("check", help="校验配置、密钥与连通性")
    check.add_argument("--net", action="store_true", help="附加网络探测（代理/企微/LLM/信息源）")
    check.add_argument(
        "--quick", action="store_true", help="快速预检：只验证关键依赖（代理/企微/LLM）"
    )

    fetch = subparsers.add_parser("fetch", help="只采集（落库，不推送）")
    fetch.add_argument("--source", action="append", metavar="ID", help="只采集指定源（可重复）")
    fetch.add_argument(
        "--window-hours", type=_window_hours_arg, metavar="N", help="覆盖采集窗口（1~168 小时）"
    )
    fetch.add_argument("--dry-run", action="store_true", help="只抓取展示，不写库")
    fetch.add_argument(
        "--limit", type=_limit_arg, default=DEFAULT_PREVIEW_LIMIT, help="预览条数（默认 10）"
    )

    enrich = subparsers.add_parser("enrich", help="只加工（预筛/聚类/LLM/排序，不推送）")
    enrich.add_argument(
        "--limit", type=_limit_arg, default=0, help="最多加工条数（0 = 用配置上限）"
    )
    enrich.add_argument("--top", type=_limit_arg, default=DEFAULT_PREVIEW_LIMIT, help="打印 Top N")
    enrich.add_argument("--no-llm", action="store_true", help="跳过 LLM（只做预筛/聚类/排序）")
    enrich.add_argument("--dry-run", action="store_true", help="不写库")

    run = subparsers.add_parser("run", help="完整流程：采集 → 加工 → 日报 → 推送")
    run.add_argument("--dry-run", action="store_true", help="只渲染不推送（产出文件供预览）")
    run.add_argument("--no-llm", action="store_true", help="跳过 LLM（不含中文摘要）")
    run.add_argument("--no-image", action="store_true", help="不渲染长图（纯文字推送）")
    run.add_argument("--top", type=_limit_arg, default=0, help="覆盖日报精选条数（0 = 用配置）")

    preview = subparsers.add_parser("preview", help="重新渲染某期日报（不推送）")
    preview.add_argument("--date", metavar="YYYY-MM-DD", help="日报日期（默认今天）")
    preview.add_argument("--no-image", action="store_true", help="不渲染长图")

    resend = subparsers.add_parser("resend", help="补发某期日报（重新渲染后推送）")
    resend.add_argument("--date", metavar="YYYY-MM-DD", help="日报日期（默认今天）")
    resend.add_argument(
        "--channel",
        choices=("all", "wecom", "email"),
        default="all",
        help="只补发指定渠道（默认全部启用渠道）",
    )

    report = subparsers.add_parser("report", help="稳定性报告（SLO 对照，观察期用）")
    report.add_argument("--days", type=_limit_arg, default=14, help="统计窗口天数（默认 14）")
    report.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")

    subparsers.add_parser("version", help="显示版本")

    alert = subparsers.add_parser("alert", help="发送一条告警（企微直连 → 邮件 → 本地文件）")
    alert.add_argument("--message", required=True, metavar="TEXT", help="告警内容")

    skip = subparsers.add_parser("skip", help="记录一次跳过运行并告警（预检失败时由脚本调用）")
    skip.add_argument("--reason", required=True, metavar="TEXT", help="跳过原因")

    # 让 --config/--debug 既可在子命令前、也可在子命令后使用
    for subparser in subparsers.choices.values():
        _add_common_args(subparser, argparse.SUPPRESS)
    return parser


def _add_common_args(parser: argparse.ArgumentParser, config_default: object) -> None:
    parser.add_argument("--config", default=config_default, help="配置文件路径（默认 config.yaml）")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=config_default if config_default is argparse.SUPPRESS else False,
        help="输出 DEBUG 日志",
    )


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "check": _cmd_check,
        "fetch": _cmd_fetch,
        "enrich": _cmd_enrich,
        "run": _cmd_run,
        "preview": _cmd_preview,
        "resend": _cmd_resend,
        "alert": _cmd_alert,
        "skip": _cmd_skip,
        "report": _cmd_report,
    }
    if args.command == "version":
        print(f"aihot {__version__}")
        return EXIT_OK
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_OK
    return handler(args)


def _prepare(args: argparse.Namespace) -> Config | None:
    """加载配置并初始化日志；失败时打印可操作的错误并返回 None。"""
    config_path = Path(args.config)
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print()
        print(f"[ERR] 配置校验未通过（{len(exc.issues)} 个问题）:")
        for index, issue in enumerate(exc.issues, 1):
            print(f"  {index}. {redact_text(issue)}")
        return None
    try:
        setup_logging(cfg.app.log_dir, "DEBUG" if args.debug else cfg.app.log_level, cfg.secrets())
    except OSError as exc:
        print()
        print(f"[ERR] 日志目录不可用: {redact_text(str(exc))}")
        return None
    return cfg


# ---------- check ----------


def _cmd_check(args: argparse.Namespace) -> int:
    print("aihot check · 配置与连通性自检")
    print(f"配置文件: {Path(args.config).resolve()}")
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG

    logging.getLogger("aihot.cli").info(
        "check 通过: 启用源 %d/%d, wecom=%s, email=%s",
        len(cfg.enabled_sources),
        len(cfg.sources),
        cfg.push.wecom.enabled,
        cfg.push.email.enabled,
    )
    print()
    print("[OK] 配置结构校验通过")
    _print_summary(cfg)
    if cfg.warnings:
        print()
        print("警告:")
        for warning in cfg.warnings:
            print(f"  [WARN] {warning}")
    if args.net:
        print()
        print("网络探测:")
        results = run_net_checks(cfg)
        if not results:
            print("  （无需要探测的目标）")
        for result in results:
            detail = redact_text(result.detail, cfg.secrets())
            print(f"  {result.mark} {result.name}: {detail}（{result.duration_ms} ms）")
        fatal, degraded = split_failures(results)
        if degraded:
            print()
            print(f"[WARN] {len(degraded)} 项可降级失败（不阻断核心链路，见上方 [WARN]）")
        if fatal:
            print()
            print(f"[ERR] 网络探测存在 {len(fatal)} 个致命失败项，请检查网络/配置")
            return EXIT_ERROR
    elif args.quick:
        print()
        print("快速预检（关键依赖）:")
        results = run_quick_checks(cfg)
        if not results:
            print("  （无需要探测的目标）")
        for result in results:
            detail = redact_text(result.detail, cfg.secrets())
            print(f"  {result.mark} {result.name}: {detail}（{result.duration_ms} ms）")
        fatal, degraded = split_failures(results)
        if degraded:
            print()
            print(f"[WARN] {len(degraded)} 项可降级失败：代理不可用时自动降级为直连，继续运行")
        if fatal:
            print()
            print(f"[ERR] 预检存在 {len(fatal)} 个致命失败项，今日运行可能受影响")
            return EXIT_ERROR
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    """稳定性报告：从 runs / digests 汇总 SLO 指标。"""
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG
    with Store(cfg.app.data_dir / "aihot.db") as store:
        report = build_report(
            store,
            days=max(1, args.days),
            timezone_name=cfg.app.timezone,
            deliver_by=cfg.app.deliver_by,
        )
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return EXIT_OK if report.passed else EXIT_ERROR


def _cmd_alert(args: argparse.Namespace) -> int:
    """发送一条运维告警（企微直连 → 邮件 → 本地文件，逐级降级）。"""
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG
    result = send_alert(cfg, "运维消息", [args.message])
    if result.delivered:
        print(f"[OK] 告警已发送（{' + '.join(result.channels)}）")
        return EXIT_OK
    if result.channels:
        # 兜底文件已写入也要说清楚，避免"命令失败但没有一句提示"（M5 修复轮 P2-1）
        print(
            "[WARN] 告警未送达：可用通道均失败或未启用，"
            f"已写入本地兜底（{' + '.join(result.channels)}）"
        )
    for error in result.errors:
        print(f"[ERR] 告警发送失败: {redact_text(error, cfg.secrets())}")
    return EXIT_ERROR


def _cmd_skip(args: argparse.Namespace) -> int:
    """记录一次"跳过运行"并告警（预检失败时由 run_daily.bat 调用）。

    目的：跳过不再是"无记录"——SLO 报告能区分"跳过"与"没运行"，
    且告警走直连通道 + 本地文件兜底，杜绝静默失败。
    """
    cfg = _prepare(args)
    if cfg is None:
        # 配置都坏了：尽力留下本地痕迹（相对当前目录，bat 已 cd 到项目根）
        _append_skip_fallback(args.reason)
        print("[ERR] 配置不可用，跳过原因已写入 logs/ALERTS.log")
        return EXIT_CONFIG
    record_skip(cfg, args.reason)
    result = send_alert(cfg, "今日运行已跳过", [args.reason, "详情见 logs\\run_daily.log"])
    channel = " + ".join(result.channels) if result.channels else "无"
    print(f"[OK] 已记录跳过（通道: {channel}）")
    for error in result.errors:
        print(f"[WARN] 告警通道失败: {redact_text(error, cfg.secrets())}")
    return EXIT_OK


def _append_skip_fallback(reason: str) -> None:
    """配置不可用时的兜底：把跳过原因写到 logs/ALERTS.log。"""
    try:
        path = Path("logs") / ALERT_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] 跳过运行（配置不可用）\n  - {reason}\n")
    except OSError:
        pass


# ---------- fetch ----------


def _cmd_fetch(args: argparse.Namespace) -> int:
    print("aihot fetch · 采集一次（只落库，不推送）")
    print(f"配置文件: {Path(args.config).resolve()}")
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG

    only = set(args.source) if args.source else None
    try:
        outcome = fetch_stage(
            cfg, window_hours=args.window_hours, only=only, dry_run=args.dry_run
        )
    except RunnerError as exc:
        print()
        print(f"[ERR] {exc}")
        return EXIT_ERROR

    since_local = outcome.since.astimezone().isoformat(timespec="seconds") if outcome.since else ""
    print()
    print(f"采集窗口: 最近 {outcome.window_hours} 小时（起始 {since_local}）")
    skipped_note = f"，跳过 {len(outcome.skipped)} 个" if outcome.skipped else ""
    print(f"信息源  : {len(outcome.stats)} 个{skipped_note}")
    print()
    print("采集结果:")
    for stat in outcome.stats:
        mark = "[OK ]" if stat.ok else "[ERR]"
        detail = f"{stat.count} 条" if stat.ok else stat.error
        print(f"  {mark} {stat.source_id:<18} {detail}（{stat.duration_ms} ms）")

    fetched = sum(stat.count for stat in outcome.stats)
    if args.dry_run:
        print(f"\n合计: 抓取 {fetched} 条（dry-run，不写库）")
    else:
        print(
            f"\n合计: 抓取 {fetched} 条 → 新增 {outcome.saved} 条"
            f"（去重 {fetched - outcome.saved} 条）→ 库内共 {outcome.db_total} 条"
        )
    _print_sample(outcome.new_items, limit=max(0, args.limit), tz_name=cfg.app.timezone)

    if outcome.all_failed:
        print()
        print("[ERR] 全部信息源采集失败，请检查网络或源可用性")
        return EXIT_ERROR
    return EXIT_OK


# ---------- enrich ----------


def _cmd_enrich(args: argparse.Namespace) -> int:
    print("aihot enrich · AI 加工（预筛 → 聚类 → LLM → 排序）")
    print(f"配置文件: {Path(args.config).resolve()}")
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG

    outcome = enrich_stage(
        cfg, limit=args.limit or None, no_llm=args.no_llm, dry_run=args.dry_run
    )
    if outcome.pending == 0:
        print()
        print("没有待加工的条目（先运行 aihot fetch 采集）")
        return EXIT_OK

    print()
    print(f"待加工: {outcome.pending} 条（上限 {args.limit or cfg.pipeline.enrich.max_items}）")
    print()
    print("预筛:")
    print(
        f"  输入 {outcome.pending} → 保留 {outcome.kept}"
        f"（垃圾 {outcome.dropped.get('blacklist', 0)}"
        f" · 与 AI 无关 {outcome.dropped.get('offtopic', 0)}"
        f" · 主题不符 {outcome.dropped.get('topic', 0)}"
        f" · 批内重复 {outcome.dropped.get('duplicate', 0)}）"
    )
    for reason, samples in outcome.samples.items():
        for sample in samples[:2]:
            print(f"    [{reason}] {_short(sample, 46)}")
    print()
    print(
        f"聚类: {outcome.kept} 条 → {outcome.clusters} 簇"
        f"（多源簇 {outcome.multi_source_clusters} · 最大簇 {outcome.max_cluster_size} 条）"
    )
    _print_ranked(outcome, limit=args.top)
    if outcome.summary:
        print()
        print(f"今日综述: {_short(outcome.summary, 90)}")
    _print_llm_stats(outcome)

    if args.dry_run:
        print()
        print("（dry-run，未写库）")
        return EXIT_OK
    print()
    print(f"已写回: {outcome.enriched} 条加工 · {outcome.filtered} 条标记为过滤")
    if outcome.llm_failed:
        print("[ERR] 全部 LLM 批次失败（条目已按原文降级写入，请检查网络/密钥/额度）")
        return EXIT_ERROR
    return EXIT_OK


# ---------- run / preview / resend ----------


def _cmd_run(args: argparse.Namespace) -> int:
    tail = "渲染, dry-run 不推送）" if args.dry_run else "推送）"
    print(f"aihot run · 完整流程（采集 → 加工 → 日报 → {tail}")
    print(f"配置文件: {Path(args.config).resolve()}")
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG

    outcome = run_all(
        cfg,
        dry_run=args.dry_run,
        no_llm=args.no_llm,
        with_image=not args.no_image,
        top_n=args.top or None,
    )
    _print_run_outcome(cfg, outcome, dry_run=args.dry_run)
    return EXIT_ERROR if outcome.status == "failed" else EXIT_OK


def _resolve_target_date(cfg: Config, value: str | None) -> date | None:
    """解析 --date 参数；非法时打印提示并返回 None。"""
    if not value:
        return digest_date_today(cfg)
    try:
        return digest_date_from(value)
    except ValueError:
        print()
        print(f"[ERR] 日期格式不正确: {value!r}（应为 YYYY-MM-DD，例如 2026-09-24）")
        return None


def _cmd_preview(args: argparse.Namespace) -> int:
    print("aihot preview · 重新渲染某期日报（不推送）")
    print(f"配置文件: {Path(args.config).resolve()}")
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG

    target = _resolve_target_date(cfg, args.date)
    if target is None:
        return EXIT_ERROR
    try:
        digest = load_digest(cfg, target)
    except (RunnerError, ValueError):
        digest = build_digest_from_db(cfg, digest_date=target)
        print(f"（没有 {target.isoformat()} 的存档，改用数据库最近窗口重新组装）")

    rendered = render_outputs(cfg, digest, with_image=not args.no_image)
    print()
    print(f"日报日期: {digest.digest_date.isoformat()}（精选 {len(digest.top_items)} 条）")
    _print_files(rendered)
    if rendered.image_error:
        print(f"  [WARN] 长图渲染失败: {rendered.image_error}")
    print()
    print("—— 企微 markdown 预览 ——")
    print(rendered.wecom_parts[0] if rendered.wecom_parts else "（空）")
    return EXIT_OK


def _cmd_resend(args: argparse.Namespace) -> int:
    print("aihot resend · 补发某期日报")
    print(f"配置文件: {Path(args.config).resolve()}")
    cfg = _prepare(args)
    if cfg is None:
        return EXIT_CONFIG

    target = _resolve_target_date(cfg, args.date)
    if target is None:
        return EXIT_ERROR
    try:
        digest = load_digest(cfg, target)
    except (RunnerError, ValueError) as exc:
        print()
        print(f"[ERR] {exc}")
        return EXIT_ERROR

    rendered = render_outputs(cfg, digest, with_image=True)
    channels = None if args.channel == "all" else {args.channel}
    errors = deliver_digest(cfg, digest, rendered, channels=channels)
    print()
    print(f"日报日期: {digest.digest_date.isoformat()}，渠道: {args.channel}")
    _print_files(rendered)
    if errors:
        print()
        for error in errors:
            print(f"  [ERR] {redact_text(error, cfg.secrets())}")
        return EXIT_ERROR
    print()
    print("[OK] 补发完成")
    return EXIT_OK


# ---------- 输出辅助 ----------


def _print_run_outcome(cfg: Config, outcome: RunOutcome, *, dry_run: bool) -> None:
    fetch = outcome.fetch
    if fetch is not None:
        fetched = sum(stat.count for stat in fetch.stats)
        print()
        print(
            f"① 采集: {len(fetch.stats) - len(fetch.failed)}/{len(fetch.stats)} 源成功"
            f"，抓取 {fetched} 条 → 新增 {fetch.saved} 条"
        )
        for stat in fetch.failed:
            print(f"   [ERR] {stat.source_id}: {_short(stat.error, 70)}")
    enrich = outcome.enrich
    if enrich is not None and enrich.pending:
        llm_cost = float(enrich.llm_stats.get("cost_cny") or 0.0)
        print(
            f"② 加工: {enrich.pending} 条 → 预筛保留 {enrich.kept} → {enrich.clusters} 簇"
            f"；LLM 批次 {enrich.llm_stats.get('batches', 0)}"
            f"（失败 {enrich.llm_stats.get('failed_batches', 0)}）· 成本 ¥{llm_cost:.4f}"
        )
    if outcome.digest is not None:
        digest = outcome.digest
        print(
            f"③ 日报: {digest.digest_date.isoformat()} · 精选 {len(digest.top_items)} 条"
            f" + 简讯 {len(digest.briefs)} 条"
            + (f" · 综述 {len(digest.summary)} 字" if digest.summary else " · 无综述")
        )
    if outcome.rendered is not None:
        print("④ 渲染:", end=" ")
        if outcome.rendered.image_paths:
            sizes = ", ".join(
                f"{path.name} {path.stat().st_size // 1024} KB"
                for path in outcome.rendered.image_paths
            )
            print(f"长图 {len(outcome.rendered.image_paths)} 张（{sizes}）", end="")
        else:
            print("纯文字（未渲染长图）", end="")
        print(f" · 企微分片 {len(outcome.rendered.wecom_parts)} · md/html 已存档")
    if dry_run:
        print("⑤ 推送: 已跳过（dry-run）")
    elif outcome.digest is not None:
        errors = outcome.errors
        wecom_state = "✅" if not errors else "⚠️"
        email_state = "邮件已启用" if cfg.push.email.enabled else "邮件未启用"
        print(f"⑤ 推送: 企业微信 {wecom_state} · {email_state}")
    if outcome.errors:
        print()
        for error in outcome.errors:
            print(f"  [WARN] {redact_text(error, cfg.secrets())}")
    print()
    print(f"状态: {outcome.status}")


def _print_files(rendered: object) -> None:
    files = getattr(rendered, "files", {}) or {}
    for key, value in files.items():
        print(f"  {key:<9}: {value}")


def _print_ranked(outcome: object, *, limit: int) -> None:
    ranked = getattr(outcome, "ranked", [])
    if not ranked or limit <= 0:
        return
    top = ranked[:limit]
    print()
    print(f"Top {len(top)}:")
    for index, entry in enumerate(top, 1):
        item = entry.item
        mention = int(item.extra.get("mention_count") or 1)
        sources = len(item.extra.get("source_ids") or [])
        print(
            f"  {index:>2}. [{entry.score:>5.1f}] ({item.category})"
            f" {_short(item.cn_title or item.title, 58)}"
        )
        if item.cn_summary:
            print(f"      {_short(item.cn_summary, 72)}")
        print(f"      提及 {mention} 条 · 来源 {sources} 家")


def _print_llm_stats(outcome: object) -> None:
    stats = getattr(outcome, "llm_stats", {}) or {}
    if not stats:
        return
    print()
    print(
        f"LLM: 批次 {stats.get('batches', 0)}（失败 {stats.get('failed_batches', 0)}）· "
        f"token {stats.get('prompt_tokens', 0)}+{stats.get('completion_tokens', 0)} · "
        f"成本约 ¥{float(stats.get('cost_cny') or 0):.4f}"
    )
    if stats.get("cost_limit_hit"):
        print("  [WARN] 已达每日成本上限，部分条目降级为原文")
    if stats.get("time_budget_hit"):
        print("  [WARN] 已达加工时间上限，部分条目降级为原文")


def _short(text: str, width: int) -> str:
    clean = " ".join((text or "").split())
    return clean if len(clean) <= width else clean[:width] + "…"


def _print_sample(items: list, limit: int, tz_name: str) -> None:
    if limit <= 0:
        return
    if not items:
        print("\n（没有新条目）")
        return
    from aihot.utils import to_local

    ordered = sorted(items, key=lambda item: item.published_at, reverse=True)[:limit]
    print(f"\n最新条目预览（前 {len(ordered)} 条）:")
    for index, item in enumerate(ordered, 1):
        published = to_local(item.published_at, tz_name).strftime("%m-%d %H:%M")
        print(f"  {index:>2}. [{item.source_id}] {_short(item.title, TITLE_PREVIEW_WIDTH)}")
        print(f"      {item.url}（{published}）")


def _print_summary(cfg: Config) -> None:
    print()
    print("运行参数:")
    print(f"  时区      : {cfg.app.timezone}")
    print(f"  采集窗口  : 最近 {cfg.app.window_hours} 小时")
    print(f"  精选条数  : Top {cfg.app.top_n}")
    print(f"  数据目录  : {cfg.app.data_dir}")
    print(f"  日志目录  : {cfg.app.log_dir}")
    proxy_display = "未启用"
    if cfg.proxy.enabled:
        proxy_display = redact_text(mask_url_credentials(cfg.proxy.url), cfg.secrets())
    print(f"  代理      : {proxy_display}")
    print()
    print(f"信息源（{len(cfg.enabled_sources)}/{len(cfg.sources)} 启用）:")
    for source in cfg.sources:
        mark = "on " if source.enabled else "off"
        print(f"  [{mark}] {source.id:<18} {source.name}")
    print()
    print("推送通道:")
    print(f"  [{'on ' if cfg.push.wecom.enabled else 'off'}] 企业微信机器人")
    email_target = cfg.push.email.username or "未配置"
    print(f"  [{'on ' if cfg.push.email.enabled else 'off'}] 邮件（{email_target}）")
    print()
    print("密钥状态:")
    _print_secret("LLM API key", cfg.llm.api_key, "M2 加工阶段前需补齐")
    _print_secret("企业微信 webhook", cfg.push.wecom.webhook, "推送前需补齐")
    _print_secret("邮箱 SMTP 授权码", cfg.push.email.password, "邮件推送前需补齐")


def _print_secret(name: str, value: str, hint: str) -> None:
    if value:
        print(f"  [OK ] {name} 已配置")
    else:
        print(f"  [WARN] {name} 未配置（{hint}）")


if __name__ == "__main__":
    raise SystemExit(main())
