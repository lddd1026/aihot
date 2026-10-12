"""编排层：采集 → 加工 → 组装 → 渲染 → 推送 → 存档。

CLI 的 `fetch` / `enrich` / `run` / `preview` / `resend` 都复用这里的阶段函数，
保证"手工调试"与"每日自动运行"走完全相同的代码路径。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from aihot.config import Config
from aihot.delivery import EmailNotifier, NotifyError, WeComNotifier
from aihot.models import Digest, NewsItem, RawItem, RunRecord
from aihot.net import HttpClient, resolve_proxy
from aihot.pipeline import (
    ClusterResult,
    Enricher,
    PrefilterResult,
    RankedItem,
    cluster_items,
    fallback_item,
    prefilter_items,
    rank_items,
)
from aihot.presentation import (
    DigestStats,
    RenderError,
    compose_digest,
    render_email_html,
    render_images,
    render_wecom_parts,
    save_digest_files,
)
from aihot.presentation.compose import local_date
from aihot.sources import SourceStat, build_adapters, collect_all
from aihot.store import Store
from aihot.utils import now_utc

logger = logging.getLogger("aihot.runner")

DIGEST_SUBDIR = "digests"
MAX_DELIVERY_ERRORS = 5
# 代理不可用时的降级参数：直连下墙外源注定失败，用更短超时避免拖慢整体
DEGRADED_TIMEOUT = 12.0
DEGRADED_RETRIES = 1
ALERT_FILE_NAME = "ALERTS.log"
MAX_ALERT_DETAILS = 5


class RunnerError(Exception):
    """编排阶段的硬错误（无可用源、无数据等）。"""


# ---------- 阶段结果 ----------


@dataclass(slots=True)
class FetchOutcome:
    stats: list[SourceStat] = field(default_factory=list)
    new_items: list[RawItem] = field(default_factory=list)
    saved: int = 0
    skipped: list[str] = field(default_factory=list)
    window_hours: int = 24
    since: datetime | None = None
    db_total: int = 0
    proxy_mode: str = "direct"  # direct / proxy / degraded

    @property
    def all_failed(self) -> bool:
        return bool(self.stats) and all(not stat.ok for stat in self.stats)

    @property
    def failed(self) -> list[SourceStat]:
        return [stat for stat in self.stats if not stat.ok]


@dataclass(slots=True)
class EnrichOutcome:
    pending: int = 0
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[str]] = field(default_factory=dict)
    clusters: int = 0
    multi_source_clusters: int = 0
    max_cluster_size: int = 0
    news_items: list[NewsItem] = field(default_factory=list)
    ranked: list[RankedItem] = field(default_factory=list)
    enriched: int = 0
    filtered: int = 0
    summary: str = ""
    llm_stats: dict[str, Any] = field(default_factory=dict)
    llm_failed: bool = False
    no_llm: bool = False
    persisted: bool = False

    @property
    def top_score(self) -> float:
        return self.ranked[0].score if self.ranked else 0.0


@dataclass(slots=True)
class RenderOutcome:
    wecom_parts: list[str] = field(default_factory=list)
    email_html: str = ""
    image_paths: list[Path] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    image_error: str = ""


@dataclass(slots=True)
class RunOutcome:
    status: str = "ok"  # ok / partial / failed
    digest: Digest | None = None
    fetch: FetchOutcome | None = None
    enrich: EnrichOutcome | None = None
    rendered: RenderOutcome | None = None
    errors: list[str] = field(default_factory=list)
    delivered: bool = False
    started_at: datetime = field(default_factory=now_utc)
    finished_at: datetime | None = None

    @property
    def summary_text(self) -> str:
        if self.digest is None:
            return "未生成日报"
        digest = self.digest
        return (
            f"{digest.digest_date.isoformat()}："
            f"精选 {len(digest.top_items)} 条，简讯 {len(digest.briefs)} 条"
        )


# ---------- ① 采集 ----------


def fetch_stage(
    cfg: Config,
    *,
    window_hours: int | None = None,
    only: set[str] | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> FetchOutcome:
    """采集所有启用源并落库（幂等）。

    代理可用则用代理；代理不可用**自动降级为直连**（只影响墙外源），
    不再因为"Clash 没开"而整日跳过。
    """
    hours = window_hours or cfg.app.window_hours
    since = now_utc() - timedelta(hours=hours)
    proxy, proxy_mode = resolve_proxy(cfg.proxy.enabled, cfg.proxy.url)
    if proxy_mode == "degraded":
        logger.warning(
            "代理不可用或节点异常（%s），本次采集降级为直连：国内源照常，墙外源将失败",
            cfg.proxy.url,
        )
        http = HttpClient(proxy=None, retries=DEGRADED_RETRIES, timeout=DEGRADED_TIMEOUT)
    else:
        http = HttpClient(proxy=proxy, retries=2)
    try:
        adapters, skipped = build_adapters(cfg, http, only=only)
        if not adapters:
            raise RunnerError("没有可用的信息源（检查 sources 配置或 --source 参数）")
        collected = collect_all(adapters, since)
    finally:
        http.close()

    outcome = FetchOutcome(
        stats=collected.stats,
        skipped=skipped,
        window_hours=hours,
        since=since,
        proxy_mode=proxy_mode,
    )
    db_path = cfg.app.data_dir / "aihot.db"
    if dry_run and not db_path.exists():
        # dry-run 且库不存在：不创建空库文件，直接返回抓取结果
        outcome.new_items = collected.items
        return outcome
    with Store(db_path) as store:
        outcome.new_items = store.filter_new(collected.items)
        outcome.db_total = store.total_items()
        if not dry_run:
            outcome.saved = store.save_items(outcome.new_items)
            outcome.db_total = store.total_items()
            if outcome.all_failed:
                fetch_status = "failed"
            elif outcome.failed:
                fetch_status = "partial"
            else:
                fetch_status = "ok"
            store.log_run(
                _run_record(
                    "fetch",
                    status=fetch_status,
                    stats={
                        "window_hours": hours,
                        "sources_ok": len(outcome.stats) - len(outcome.failed),
                        "sources_failed": len(outcome.failed),
                        "fetched": len(collected.items),
                        "saved": outcome.saved,
                        "skipped": skipped,
                        "proxy_mode": proxy_mode,
                    },
                )
            )
    if limit is not None and limit > 0:
        outcome.new_items = outcome.new_items[:limit]
    return outcome


# ---------- ② 加工 ----------


def enrich_stage(
    cfg: Config,
    *,
    limit: int | None = None,
    no_llm: bool = False,
    dry_run: bool = False,
    summary_count: int = 30,
) -> EnrichOutcome:
    """预筛 → 聚类 → LLM 加工 → 排序，并写回结果。"""
    max_items = limit or cfg.pipeline.enrich.max_items
    with Store(cfg.app.data_dir / "aihot.db") as store:
        pending = store.unenriched_items(limit=max_items)
        first_cluster_id = store.next_cluster_id()
    if not pending:
        return EnrichOutcome(pending=0)

    pre: PrefilterResult = prefilter_items(pending, cfg)
    clustering: ClusterResult = cluster_items(
        pre.items,
        threshold=cfg.pipeline.dedup.title_similarity,
        max_hours_apart=cfg.pipeline.dedup.max_hours_apart,
    )

    enricher: Enricher | None = None
    if not no_llm:
        enricher = Enricher(cfg)
        if cfg.pipeline.dedup.llm_refine:
            clustering = enricher.refine_clusters(clustering)

    _remap_cluster_ids(clustering, first_cluster_id)

    if enricher is None:
        news_items = [
            fallback_item(cluster, clustering.members, cfg.source_weights)
            for cluster in clustering.clusters
        ]
    else:
        news_items = enricher.enrich_clusters(clustering, cfg.source_weights)

    ranked = rank_items(
        news_items,
        weights=cfg.source_weights,
        interest_bonus=cfg.pipeline.rank.interest_bonus,
    )
    summary = ""
    if enricher is not None and ranked:
        top_names = [entry.item for entry in ranked[:summary_count]]
        summary = enricher.summarize(top_names)

    outcome = EnrichOutcome(
        pending=len(pending),
        kept=pre.stats.kept,
        dropped={
            "blacklist": pre.stats.dropped_blacklist,
            "offtopic": pre.stats.dropped_offtopic,
            "duplicate": pre.stats.dropped_duplicate,
            "topic": pre.stats.dropped_topic,
        },
        samples=pre.stats.dropped_samples,
        clusters=len(clustering.clusters),
        multi_source_clusters=clustering.stats.multi_source_clusters,
        max_cluster_size=clustering.stats.max_cluster_size,
        news_items=news_items,
        ranked=ranked,
        summary=summary,
        no_llm=no_llm,
        llm_stats=enricher.stats.as_dict() if enricher is not None else _no_llm_stats(),
        llm_failed=bool(
            enricher is not None and enricher.stats.batches and not enricher.stats.succeeded
        ),
    )

    if dry_run:
        return outcome

    rows = _enrichment_rows(clustering, news_items)
    filtered_rows = _filtered_rows(pending, pre.items)
    outcome.enriched = len(rows)
    with Store(cfg.app.data_dir / "aihot.db") as store:
        outcome.enriched = store.mark_enriched(rows)
        outcome.filtered = store.mark_filtered(filtered_rows)
        store.save_clusters(clustering.clusters)
        outcome.persisted = True
        store.log_run(
            _run_record(
                "enrich",
                status="failed" if outcome.llm_failed else "ok",
                stats={
                    "pending": outcome.pending,
                    "prefilter_kept": outcome.kept,
                    "clusters": outcome.clusters,
                    "enriched": outcome.enriched,
                    "filtered": outcome.filtered,
                    # 预筛剔除细分（可观测性：主题剔除也要有数字）
                    "dropped_blacklist": outcome.dropped.get("blacklist", 0),
                    "dropped_offtopic": outcome.dropped.get("offtopic", 0),
                    "dropped_topic": outcome.dropped.get("topic", 0),
                    "dropped_duplicate": outcome.dropped.get("duplicate", 0),
                    **outcome.llm_stats,
                },
            )
        )
    return outcome


# ---------- ③ 组装 / 渲染 ----------


def build_digest(
    cfg: Config,
    outcome: EnrichOutcome,
    *,
    top_n: int | None = None,
    digest_date: date | None = None,
) -> Digest:
    """把排序结果组装成日报对象。

    - 正常运行时以数据库为准（包含窗口内此前已加工的条目，最完整）
    - dry-run 时不写库，因此直接用本轮内存结果（否则会读到旧数据）
    """
    stats = DigestStats(
        scanned=outcome.pending,
        kept=outcome.kept,
        clusters=outcome.clusters,
        cost_cny=float(outcome.llm_stats.get("cost_cny") or 0.0),
        sources=len(cfg.enabled_sources),
    )
    if outcome.persisted or not outcome.news_items:
        # 正常运行时以库为准（最完整）；dry-run 无新条目时也回落到库
        return build_digest_from_db(
            cfg,
            digest_date=digest_date,
            top_n=top_n,
            summary=outcome.summary,
            stats=stats,
        )
    ranked = rank_items(
        outcome.news_items,
        weights=cfg.source_weights,
        interest_bonus=cfg.pipeline.rank.interest_bonus,
    )
    if not stats.clusters:
        stats.clusters = len({item.cluster_id for item in outcome.news_items if item.cluster_id})
    return compose_digest(
        ranked,
        digest_date=digest_date or local_date(now_utc(), cfg.app.timezone),
        top_n=top_n or cfg.app.top_n,
        summary=outcome.summary,
        stats=stats,
    )


def build_digest_from_db(
    cfg: Config,
    *,
    digest_date: date | None = None,
    top_n: int | None = None,
    summary: str = "",
    stats: DigestStats | None = None,
) -> Digest:
    """从数据库读取窗口内已加工条目并组装日报。

    这样"每日自动运行"与"事后预览/补发"使用同一份数据来源，不会出现两套结果。
    """
    since = now_utc() - timedelta(hours=cfg.app.window_hours)
    with Store(cfg.app.data_dir / "aihot.db") as store:
        items = store.recent_enriched_items(since=since)
    ranked = rank_items(
        items,
        weights=cfg.source_weights,
        interest_bonus=cfg.pipeline.rank.interest_bonus,
    )
    resolved_stats = stats or DigestStats(sources=len(cfg.enabled_sources))
    if not resolved_stats.kept:
        resolved_stats.kept = len(items)
    if not resolved_stats.clusters:
        resolved_stats.clusters = len({item.cluster_id for item in items if item.cluster_id})
    if not resolved_stats.scanned:
        resolved_stats.scanned = len(items)
    return compose_digest(
        ranked,
        digest_date=digest_date or local_date(now_utc(), cfg.app.timezone),
        top_n=top_n or cfg.app.top_n,
        summary=summary,
        stats=resolved_stats,
    )


def render_outputs(cfg: Config, digest: Digest, *, with_image: bool = True) -> RenderOutcome:
    """渲染文字 + 长图并落盘（图片失败不影响文字）。"""
    rendered = RenderOutcome()
    rendered.wecom_parts = render_wecom_parts(digest)
    email_cid = "digest-image-1" if cfg.push.email.attach_image else None
    rendered.email_html = render_email_html(digest, image_cid=email_cid)

    out_dir = cfg.app.data_dir / DIGEST_SUBDIR
    if with_image:
        try:
            rendered.image_paths = render_images(digest, out_dir)
        except RenderError as exc:
            rendered.image_error = str(exc)
            logger.warning("长图渲染失败，降级为纯文字推送: %s", exc)

    rendered.files = save_digest_files(digest, out_dir, image_paths=rendered.image_paths)
    html_path = out_dir / f"{digest.digest_date.isoformat()}.html"
    html_path.write_text(rendered.email_html, encoding="utf-8")
    rendered.files["html"] = str(html_path)

    with Store(cfg.app.data_dir / "aihot.db") as store:
        store.save_digest(digest)
    return rendered


def deliver_digest(
    cfg: Config,
    digest: Digest,
    rendered: RenderOutcome,
    *,
    channels: set[str] | None = None,
) -> list[str]:
    """推送到所有启用的渠道，返回错误列表（空列表 = 全部成功）。

    channels 为 None 时推送到所有已启用渠道；也可只指定 {"wecom"} / {"email"}。
    """
    errors: list[str] = []
    want_wecom = (channels is None or "wecom" in channels) and cfg.push.wecom.enabled
    want_email = (channels is None or "email" in channels) and cfg.push.email.enabled

    if want_wecom and cfg.push.wecom.webhook:
        # 强制直连：qyapi 是国内服务，走代理会在代理不可用时一并失联
        notifier = WeComNotifier(cfg.push.wecom.webhook)
        try:
            for path in rendered.image_paths:
                try:
                    notifier.send_image(path)
                except NotifyError as exc:
                    _collect_error(errors, f"企业微信图片({path.name})", exc)
            for index, part in enumerate(rendered.wecom_parts, start=1):
                content = part
                if index == 1 and rendered.image_error:
                    # 长图失败要让你看得见，而不是静默只发文字
                    content = f"⚠️ 今日长图渲染失败，以下是文字版\n\n{part}"
                try:
                    notifier.send_markdown(content)
                except NotifyError as exc:
                    _collect_error(errors, f"企业微信文字第{index}片", exc)
        finally:
            notifier.close()
    elif want_wecom:
        errors.append("企业微信: webhook 未配置")

    if want_email:
        notifier = EmailNotifier(cfg.push.email)
        subject = f"AI 热点日报 · {digest.digest_date.isoformat()}"
        attachments = rendered.image_paths if cfg.push.email.attach_image else []
        try:
            notifier.send(subject, rendered.email_html, images=attachments)
        except NotifyError as exc:
            _collect_error(errors, "邮件", exc)

    return errors


def _collect_error(errors: list[str], label: str, exc: Exception) -> None:
    """收集推送错误（最多保留 5 条，避免刷屏）。"""
    if len(errors) < MAX_DELIVERY_ERRORS:
        errors.append(f"{label}: {exc}")
    elif len(errors) == MAX_DELIVERY_ERRORS:
        errors.append("（更多错误已省略）")


def run_all(
    cfg: Config,
    *,
    dry_run: bool = False,
    no_llm: bool = False,
    with_image: bool = True,
    top_n: int | None = None,
) -> RunOutcome:
    """完整跑一轮：采集 → 加工 → 组装 → 渲染 →（非 dry-run 时）推送。"""
    outcome = RunOutcome()
    delivery_errors: list[str] = []
    try:
        outcome.fetch = fetch_stage(cfg, dry_run=dry_run)
        if outcome.fetch.all_failed:
            raise RunnerError("全部信息源采集失败")
        if outcome.fetch.proxy_mode == "degraded" and not dry_run:
            # 降级不阻断出报，但必须让你知情（M5 期间"静默失败"的教训）
            send_alert(
                cfg,
                "运行降级提示",
                ["代理不可用，今日已降级为直连采集（部分墙外源缺失，日报照常发送）"],
            )
        outcome.enrich = enrich_stage(cfg, no_llm=no_llm, dry_run=dry_run)
        # 当天没有新内容时：dry-run 仍出预览，真实运行则跳过（避免重复推送旧内容）
        if outcome.enrich.pending == 0 and not dry_run:
            logger.info("没有待加工条目，本轮不生成日报")
            outcome.status = "partial"
            outcome.errors.append("没有待加工条目（先执行 aihot fetch）")
            return outcome
        outcome.digest = build_digest(cfg, outcome.enrich, top_n=top_n)
        if not outcome.digest.summary and not no_llm and outcome.digest.top_items:
            outcome.digest.summary = _summarize_digest(cfg, outcome.digest)
        outcome.rendered = render_outputs(cfg, outcome.digest, with_image=with_image)
        if outcome.rendered.image_error:
            outcome.errors.append(f"长图渲染失败（已降级文字）: {outcome.rendered.image_error}")

        if not dry_run:
            delivery_errors = deliver_digest(cfg, outcome.digest, outcome.rendered)
            outcome.errors.extend(delivery_errors)
            if delivery_errors:
                _alert(cfg, "日报推送失败", delivery_errors)
        outcome.status = "partial" if outcome.errors else "ok"
    except RunnerError as exc:
        outcome.status = "failed"
        outcome.errors.append(str(exc))
        _alert(cfg, "日报生成失败", [str(exc)])
    except RenderError as exc:
        outcome.status = "failed"
        outcome.errors.append(f"渲染失败: {exc}")
        _alert(cfg, "日报渲染失败", [f"长图渲染失败: {exc}"])
    except Exception as exc:  # 兜底：编排层任何未预期异常都要告警，且不留 traceback 给用户
        logger.exception("日报生成出现未预期异常")
        outcome.status = "failed"
        outcome.errors.append(f"{type(exc).__name__}: {exc}")
        _alert(cfg, "日报生成异常", [f"{type(exc).__name__}: {exc}"])
    finally:
        outcome.finished_at = now_utc()
        if not dry_run:
            outcome.delivered = outcome.digest is not None and not delivery_errors
            with Store(cfg.app.data_dir / "aihot.db") as store:
                store.log_run(
                    RunRecord(
                        run_id=f"{outcome.started_at.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:6]}",
                        started_at=outcome.started_at,
                        finished_at=outcome.finished_at,
                        status=outcome.status,
                        stage_stats=_run_stage_stats(outcome, delivery_errors),
                    )
                )
    return outcome


def _run_stage_stats(outcome: RunOutcome, delivery_errors: list[str]) -> dict[str, Any]:
    """run 阶段的统计（SLO 报告的数据来源）。"""
    digest = outcome.digest
    rendered = outcome.rendered
    enrich = outcome.enrich
    finished = outcome.finished_at or now_utc()
    return {
        "stage": "run",
        "digest_date": digest.digest_date.isoformat() if digest else "",
        "selected": len(digest.top_items) if digest else 0,
        "briefs": len(digest.briefs) if digest else 0,
        "papers": len(digest.papers) if digest else 0,
        "delivered": bool(digest is not None and not delivery_errors),
        "delivery_errors": delivery_errors[:3],
        "image_ok": bool(rendered is not None and not rendered.image_error),
        "duration_seconds": round((finished - outcome.started_at).total_seconds(), 1),
        "cost_cny": float((enrich.llm_stats.get("cost_cny") if enrich else 0) or 0.0),
        "errors": outcome.errors[:5],
    }


def _summarize_digest(cfg: Config, digest: Digest) -> str:
    """为"从库里重建的日报"补一段综述（含成本统计）。"""
    enricher = Enricher(cfg)
    summary = enricher.summarize(list(digest.top_items))
    if enricher.stats.cost_cny:
        digest.stats["cost_cny"] = round(
            float(digest.stats.get("cost_cny") or 0.0) + enricher.stats.cost_cny, 4
        )
    return summary


@dataclass(slots=True)
class AlertResult:
    """告警发送结果：成功通道 + 各通道失败原因。"""

    delivered: bool = False
    channels: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def send_alert(cfg: Config, title: str, details: list[str]) -> AlertResult:
    """发送运维告警：企微（直连）→ 邮件（直连）→ 本地告警文件，逐级降级、绝不静默。

    为什么不全走代理：企微 webhook 与 SMTP 都是国内直连服务，代理不可用时
    若强制走代理，连"告诉你出事了"这条消息都发不出去（M5 观察期的真实教训）。
    """
    result = AlertResult()
    body_lines = [f"- {line}" for line in details[:MAX_ALERT_DETAILS]]
    text = f"⚠️ aihot 告警：{title}\n" + "\n".join(body_lines)

    if cfg.push.wecom.enabled and cfg.push.wecom.webhook:
        notifier = WeComNotifier(cfg.push.wecom.webhook)  # 直连
        try:
            notifier.send_text(text)
            result.delivered = True
            result.channels.append("企业微信")
        except NotifyError as exc:
            result.errors.append(f"企业微信: {exc}")
        finally:
            notifier.close()

    if cfg.push.email.enabled and cfg.push.email.to_addrs:
        try:
            EmailNotifier(cfg.push.email).send(f"[aihot 告警] {title}", _alert_html(title, details))
            result.delivered = True
            result.channels.append("邮件")
        except NotifyError as exc:
            result.errors.append(f"邮件: {exc}")

    if not result.delivered:
        path = _append_alert_file(cfg, title, details)
        if path is not None:
            result.channels.append(f"本地文件({path.name})")
        if not result.channels:
            result.errors.append("未配置任何可用的告警通道")

    for error in result.errors:
        logger.error("告警通道失败: %s", error)
    if result.delivered:
        logger.info("告警已发送（%s）: %s", " + ".join(result.channels), title)
    return result


def _alert(cfg: Config, title: str, details: list[str]) -> None:
    """失败告警（供编排层调用；告警本身失败只记日志，不再抛错）。"""
    send_alert(cfg, title, details)


def _alert_html(title: str, details: list[str]) -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in details[:MAX_ALERT_DETAILS])
    return f"<h3>{escape(title)}</h3><ul>{items}</ul>"


def _append_alert_file(cfg: Config, title: str, details: list[str]) -> Path | None:
    """所有通道都失败时的最后兜底：写本地告警文件（下次运行/排障时可见）。"""
    try:
        cfg.app.log_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.app.log_dir / ALERT_FILE_NAME
        stamp = datetime.now(ZoneInfo(cfg.app.timezone)).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {title}\n")
            for line in details[:MAX_ALERT_DETAILS]:
                handle.write(f"  - {line}\n")
        return path
    except OSError as exc:
        logger.error("本地告警文件写入失败: %s", exc)
        return None


def record_skip(cfg: Config, reason: str) -> None:
    """记录一次"跳过运行"（预检失败等）。

    这样 SLO 报告能区分"跳过（有记录+原因）"与"完全没运行（无记录）"。
    """
    with Store(cfg.app.data_dir / "aihot.db") as store:
        store.log_run(_run_record("skip", status="skipped", stats={"reason": reason}))


# ---------- 内部工具 ----------


def _run_record(stage: str, *, status: str, stats: dict[str, Any]) -> RunRecord:
    now = now_utc()
    return RunRecord(
        run_id=f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:6]}",
        started_at=now,
        finished_at=now,
        status=status,
        stage_stats={"stage": stage, **stats},
    )


def _remap_cluster_ids(clustering: ClusterResult, start: int) -> None:
    """把本轮簇编号映射为全局单调 ID（必须在所有簇变更之后调用）。"""
    remapped: dict[int, list[RawItem]] = {}
    for offset, cluster in enumerate(clustering.clusters):
        old_id = cluster.cluster_id
        new_id = start + offset
        cluster.cluster_id = new_id
        remapped[new_id] = clustering.members.get(old_id, [])
    clustering.members = remapped


def _enrichment_rows(
    clustering: ClusterResult, news_items: list[NewsItem]
) -> list[tuple[Any, ...]]:
    by_cluster = {item.cluster_id: item for item in news_items if item.cluster_id is not None}
    rows: list[tuple[Any, ...]] = []
    for cluster in clustering.clusters:
        news = by_cluster.get(cluster.cluster_id)
        if news is None:
            continue
        extra_json = json.dumps(news.extra, ensure_ascii=False)
        for member in clustering.members.get(cluster.cluster_id, []):
            rows.append(
                (
                    member.source_id,
                    member.url_hash,
                    cluster.cluster_id,
                    news.category,
                    news.importance,
                    news.cn_title,
                    news.cn_summary,
                    extra_json,
                )
            )
    return rows


def _filtered_rows(all_items: list[RawItem], kept: list[RawItem]) -> list[tuple[str, str]]:
    kept_keys = {(item.source_id, item.url_hash) for item in kept}
    return [
        (item.source_id, item.url_hash)
        for item in all_items
        if (item.source_id, item.url_hash) not in kept_keys
    ]


def _no_llm_stats() -> dict[str, Any]:
    return {
        "no_llm": True,
        "refine_calls": 0,
        "refine_failed": 0,
        "refine_rejected": 0,
        "batches": 0,
        "succeeded": 0,
        "failed_batches": 0,
        "fallback_items": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_cny": 0.0,
        "cost_limit_hit": False,
        "time_budget_hit": False,
    }


def digest_date_today(cfg: Config) -> date:
    return local_date(now_utc(), cfg.app.timezone)


def load_digest(cfg: Config, digest_date: date) -> Digest:
    """读取某期日报存档。"""
    with Store(cfg.app.data_dir / "aihot.db") as store:
        digest = store.load_digest(digest_date)
    if digest is None:
        raise RunnerError(f"没有 {digest_date.isoformat()} 的日报存档（先运行 aihot run）")
    return digest
