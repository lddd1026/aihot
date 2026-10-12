"""配置加载与校验。"""

from __future__ import annotations

import os
import re
import zoneinfo
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from aihot.utils import redact_text

VALID_SOURCE_TYPES = frozenset(
    {"rss", "hackernews", "reddit", "arxiv", "hf_papers", "html", "github", "github_trending"}
)
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
DEFAULT_GATE_SOURCES = ("hackernews",)
DEFAULT_INTEREST_BONUS: dict[str, float] = {
    "模型发布": 8.0,
    "产品更新": 5.0,
    "开源工具": 5.0,
}
DEFAULT_TITLE_SIMILARITY = 88.0
DEFAULT_MAX_HOURS_APART = 72
DEFAULT_ENRICH_MAX_ITEMS = 200
DEFAULT_ENRICH_TIME_BUDGET = 600
DEFAULT_PRICE_INPUT = 2.0
DEFAULT_PRICE_OUTPUT = 8.0
DEFAULT_LLM_REFINE = False  # 默认关闭：LLM 精修需先验证误合并风险
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(Exception):
    """配置错误。携带全部问题清单，便于一次性修复。"""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True, slots=True)
class AppConfig:
    timezone: str
    window_hours: int
    top_n: int
    log_level: str
    data_dir: Path
    log_dir: Path
    deliver_by: str = "08:00"  # 预期送达时间（准点率统计基准，HH:MM）


@dataclass(frozen=True, slots=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    batch_size: int
    daily_cost_limit_cny: float


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    enabled: bool
    url: str


@dataclass(frozen=True, slots=True)
class SourceConfig:
    id: str
    name: str
    type: str
    url: str
    enabled: bool
    weight: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WeComConfig:
    enabled: bool
    webhook: str


@dataclass(frozen=True, slots=True)
class EmailConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    use_ssl: bool
    username: str
    password: str
    from_addr: str
    to_addrs: tuple[str, ...]
    attach_image: bool = False


@dataclass(frozen=True, slots=True)
class PrefilterConfig:
    gate_sources: tuple[str, ...]
    blacklist_keywords: tuple[str, ...]
    extra_ai_keywords: tuple[str, ...]
    use_builtin_blacklist: bool = True


@dataclass(frozen=True, slots=True)
class DedupConfig:
    title_similarity: float
    max_hours_apart: int
    llm_refine: bool


@dataclass(frozen=True, slots=True)
class EnrichConfig:
    max_items: int
    price_input_per_million: float
    price_output_per_million: float
    time_budget_seconds: int = 600


@dataclass(frozen=True, slots=True)
class RankConfig:
    interest_bonus: dict[str, float]


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    prefilter: PrefilterConfig
    dedup: DedupConfig
    enrich: EnrichConfig
    rank: RankConfig


@dataclass(frozen=True, slots=True)
class PushConfig:
    wecom: WeComConfig
    email: EmailConfig


@dataclass(frozen=True, slots=True)
class Config:
    path: Path
    app: AppConfig
    llm: LLMConfig
    proxy: ProxyConfig
    sources: tuple[SourceConfig, ...]
    push: PushConfig
    pipeline: PipelineConfig
    warnings: tuple[str, ...] = ()

    @property
    def enabled_sources(self) -> tuple[SourceConfig, ...]:
        return tuple(source for source in self.sources if source.enabled)

    @property
    def source_weights(self) -> dict[str, float]:
        return {source.id: source.weight for source in self.sources}

    def secrets(self) -> list[str]:
        """收集所有非空密钥，用于日志脱敏。"""
        candidates = [
            self.llm.api_key,
            self.push.wecom.webhook,
            self.push.email.password,
        ]
        return [value for value in candidates if value]


def load_config(path: str | Path) -> Config:
    """加载并校验配置；有错误时抛出 ConfigError（含完整问题清单）。"""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError([f"配置文件不存在: {config_path}"])
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError([_yaml_error_message(exc)]) from exc
    except OSError as exc:
        raise ConfigError([f"配置文件读取失败: {exc}"]) from exc
    if raw is None:
        raise ConfigError(["配置文件为空"])
    if not isinstance(raw, dict):
        raise ConfigError([f"配置顶层必须是映射（dict），实际为 {type(raw).__name__}"])
    errors, warnings = validate(raw)
    if errors:
        raise ConfigError(errors)
    return _build(config_path, raw, warnings)


def validate(raw: dict[str, Any]) -> tuple[list[str], list[str]]:
    """校验配置结构，返回 (错误列表, 警告列表)。"""
    errors: list[str] = []
    warnings: list[str] = []

    app = _section(raw, "app", errors)
    timezone_name = str(app.get("timezone", "Asia/Shanghai"))
    if not _valid_timezone(timezone_name):
        errors.append(f"app.timezone 无效: {timezone_name!r}（示例: Asia/Shanghai）")
    _check_int(errors, "app.window_hours", app.get("window_hours", 24), 1, 168)
    _check_int(errors, "app.top_n", app.get("top_n", 12), 1, 50)
    for key in ("data_dir", "log_dir"):
        value = app.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"app.{key} 必须是字符串路径，实际: {value!r}")
    log_level = str(app.get("log_level", "INFO")).upper()
    if log_level not in VALID_LOG_LEVELS:
        allowed = "/".join(sorted(VALID_LOG_LEVELS))
        errors.append(f"app.log_level 必须是 {allowed} 之一，实际: {log_level!r}")
    deliver_by = str(app.get("deliver_by", "08:00")).strip()
    if not _valid_clock(deliver_by):
        errors.append(f"app.deliver_by 必须是 HH:MM 格式（例如 17:30），实际: {deliver_by!r}")

    proxy = _section(raw, "proxy", errors)
    _check_bool(errors, "proxy.enabled", proxy.get("enabled"))
    if proxy.get("enabled") is True:
        proxy_url = str(proxy.get("url") or "").strip()
        if not proxy_url:
            errors.append("proxy.enabled 为 true 但 proxy.url 为空")
        else:
            problem = _proxy_url_problem(proxy_url)
            if problem:
                errors.append(f"proxy.url 无效: {problem}")

    llm = _section(raw, "llm", errors)
    base_url = str(llm.get("base_url") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        errors.append(f"llm.base_url 必须是 http(s) 地址，实际: {base_url!r}")
    if not str(llm.get("model") or "").strip():
        errors.append("llm.model 不能为空")
    _check_int(errors, "llm.batch_size", llm.get("batch_size", 15), 1, 50)
    cost_limit = llm.get("daily_cost_limit_cny", 1.0)
    is_number = isinstance(cost_limit, (int, float)) and not isinstance(cost_limit, bool)
    if not is_number or float(cost_limit) <= 0:
        errors.append(f"llm.daily_cost_limit_cny 必须是正数，实际: {cost_limit!r}")
    if not _resolve_secret(llm, "api_key"):
        warnings.append("llm.api_key 未配置：M2 加工阶段前需补齐（或设置环境变量 AIHOT_LLM_KEY）")

    _validate_sources(raw, errors)

    push = _section(raw, "push", errors)
    wecom = push.get("wecom") if isinstance(push.get("wecom"), dict) else {}
    email = push.get("email") if isinstance(push.get("email"), dict) else {}
    _check_bool(errors, "push.wecom.enabled", wecom.get("enabled"))
    _check_bool(errors, "push.email.enabled", email.get("enabled"))
    wecom_enabled = wecom.get("enabled") is True
    email_enabled = email.get("enabled") is True

    if wecom_enabled:
        webhook = _resolve_secret(wecom, "webhook")
        if not webhook:
            errors.append(
                "push.wecom.enabled 为 true 但未配置 webhook（或 webhook_env 环境变量为空）"
            )
        elif not webhook.startswith("https://qyapi.weixin.qq.com/"):
            errors.append("push.wecom.webhook 格式不正确，应以 https://qyapi.weixin.qq.com/ 开头")

    _validate_email(errors, email, enabled=email_enabled)

    _validate_pipeline(raw, errors)

    if not wecom_enabled and not email_enabled:
        warnings.append("没有启用任何推送渠道：日报将只落盘、不发送")

    return errors, warnings


def _validate_pipeline(raw: dict[str, Any], errors: list[str]) -> None:
    pipeline = _section(raw, "pipeline", errors)
    prefilter = pipeline.get("prefilter") if isinstance(pipeline.get("prefilter"), dict) else {}
    dedup = pipeline.get("dedup") if isinstance(pipeline.get("dedup"), dict) else {}
    enrich = pipeline.get("enrich") if isinstance(pipeline.get("enrich"), dict) else {}
    rank = pipeline.get("rank") if isinstance(pipeline.get("rank"), dict) else {}

    for key in ("gate_sources", "blacklist_keywords", "extra_ai_keywords"):
        value = prefilter.get(key)
        if value is not None and (
            not isinstance(value, list) or any(not isinstance(entry, str) for entry in value)
        ):
            errors.append(f"pipeline.prefilter.{key} 必须是字符串列表（可为空列表）")
    _check_number(
        errors,
        "pipeline.dedup.title_similarity",
        dedup.get("title_similarity", DEFAULT_TITLE_SIMILARITY),
        0,
        100,
    )
    _check_int(
        errors,
        "pipeline.dedup.max_hours_apart",
        dedup.get("max_hours_apart", DEFAULT_MAX_HOURS_APART),
        1,
        720,
    )
    _check_bool(errors, "pipeline.dedup.llm_refine", dedup.get("llm_refine"))
    _check_int(
        errors,
        "pipeline.enrich.max_items",
        enrich.get("max_items", DEFAULT_ENRICH_MAX_ITEMS),
        1,
        2000,
    )
    _check_int(
        errors,
        "pipeline.enrich.time_budget_seconds",
        enrich.get("time_budget_seconds", DEFAULT_ENRICH_TIME_BUDGET),
        60,
        7200,
    )
    _check_bool(
        errors,
        "pipeline.prefilter.use_builtin_blacklist",
        prefilter.get("use_builtin_blacklist"),
    )
    _check_number(
        errors,
        "pipeline.enrich.price_input_per_million",
        enrich.get("price_input_per_million", DEFAULT_PRICE_INPUT),
        0,
        10000,
    )
    _check_number(
        errors,
        "pipeline.enrich.price_output_per_million",
        enrich.get("price_output_per_million", DEFAULT_PRICE_OUTPUT),
        0,
        10000,
    )
    bonus = rank.get("interest_bonus")
    if bonus is not None:
        if not isinstance(bonus, dict):
            errors.append("pipeline.rank.interest_bonus 必须是「分类: 数字」映射")
        else:
            for category, value in bonus.items():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    errors.append(
                        f"pipeline.rank.interest_bonus.{category} 必须是数字，实际: {value!r}"
                    )
                    break


def _yaml_error_message(exc: yaml.YAMLError) -> str:
    """构造 YAML 错误说明。

    安全要求：**绝不回显配置原文片段**（PyYAML 默认错误信息带上下文行，可能含密钥），
    只给出原因与行列号。
    """
    problem = redact_text(str(getattr(exc, "problem", "") or "").strip())
    mark = getattr(exc, "problem_mark", None)
    if problem and mark is not None:
        location = f"第 {mark.line + 1} 行，第 {mark.column + 1} 列"
        return f"YAML 解析失败: {problem}（{location}，已隐藏原文以防泄露密钥）"
    return f"YAML 解析失败: {type(exc).__name__}（已隐藏原文以防泄露密钥）"


def _section(raw: dict[str, Any], name: str, errors: list[str]) -> dict[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name}: 必须是映射（dict），实际为 {type(value).__name__}")
        return {}
    return value


def _valid_timezone(name: str) -> bool:
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _valid_clock(value: str) -> bool:
    """HH:MM 格式校验（00:00 ~ 23:59）。"""
    if len(value) != 5 or value[2] != ":":
        return False
    hours, minutes = value[:2], value[3:]
    if not (hours.isdigit() and minutes.isdigit()):
        return False
    return 0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59


def _proxy_url_problem(url: str) -> str:
    """检查代理地址；合法返回空字符串，否则返回问题描述（不回显完整地址）。"""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        return f"{exc}（示例: http://127.0.0.1:7890）"
    if parts.scheme not in ("http", "https", "socks5", "socks5h"):
        return f"不支持的协议 {parts.scheme!r}（可选: http/https/socks5/socks5h）"
    if not parts.hostname:
        return "缺少主机名（示例: http://127.0.0.1:7890）"
    if port is not None and not 1 <= port <= 65535:
        return f"端口越界: {port}"
    return ""


def _check_int(errors: list[str], path: str, value: object, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        errors.append(f"{path} 必须是 {low}~{high} 的整数，实际: {value!r}")


def _check_bool(errors: list[str], path: str, value: object) -> None:
    """布尔字段必须是真正的 true/false（拒绝 "false"、"0" 等字符串，避免静默误判）。"""
    if value is not None and not isinstance(value, bool):
        errors.append(f"{path} 必须是布尔值 true/false，实际: {value!r}")


def _check_number(
    errors: list[str], path: str, value: object, low: float, high: float
) -> None:
    """数值字段范围校验（拒绝 bool）。"""
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    if not is_number or not low <= float(value) <= high:
        errors.append(f"{path} 必须是 {low}~{high} 的数字，实际: {value!r}")


def _validate_sources(raw: dict[str, Any], errors: list[str]) -> None:
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources 必须是非空列表")
        return
    seen_ids: set[str] = set()
    for index, item in enumerate(sources):
        if not isinstance(item, dict):
            errors.append(f"sources[{index}]: 必须是映射（dict）")
            continue
        source_id = str(item.get("id") or "").strip()
        prefix = f"sources[{index}] (id={source_id or '?'})"
        if not source_id:
            errors.append(f"{prefix}: 缺少 id")
        elif source_id in seen_ids:
            errors.append(f"{prefix}: id 重复")
        else:
            seen_ids.add(source_id)
        source_type = str(item.get("type") or "").strip()
        if source_type not in VALID_SOURCE_TYPES:
            allowed = ", ".join(sorted(VALID_SOURCE_TYPES))
            errors.append(f"{prefix}: type 不支持 {source_type!r}（可选: {allowed}）")
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            errors.append(f"{prefix}: url 必须是 http(s) 地址，实际: {url!r}")
        weight = item.get("weight", 1.0)
        weight_ok = (
            isinstance(weight, (int, float))
            and not isinstance(weight, bool)
            and 0 < float(weight) <= 10
        )
        if not weight_ok:
            errors.append(f"{prefix}: weight 必须是 (0, 10] 的数字，实际: {weight!r}")
        _check_bool(errors, f"{prefix}: enabled", item.get("enabled"))
        extra = item.get("extra")
        if extra is not None and not isinstance(extra, dict):
            errors.append(f"{prefix}: extra 必须是映射（dict），实际: {type(extra).__name__}")


def _validate_email(errors: list[str], email: dict[str, Any], *, enabled: bool) -> None:
    """类型校验始终执行；完整性校验仅在 enabled 时执行。"""
    port = email.get("smtp_port", 465)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        errors.append(f"push.email.smtp_port 必须是 1~65535 的整数，实际: {port!r}")
    to_addrs = email.get("to_addrs")
    if to_addrs is not None and (
        not isinstance(to_addrs, list) or any(not isinstance(a, str) for a in to_addrs)
    ):
        errors.append("push.email.to_addrs 必须是邮箱字符串列表")
    _check_bool(errors, "push.email.use_ssl", email.get("use_ssl"))
    _check_bool(errors, "push.email.attach_image", email.get("attach_image"))
    if not enabled:
        return
    if not str(email.get("smtp_host") or "").strip():
        errors.append("push.email.enabled 为 true 但 smtp_host 未配置")
    if not str(email.get("username") or "").strip():
        errors.append("push.email.enabled 为 true 但 username 未配置")
    if not _resolve_secret(email, "password"):
        errors.append("push.email.enabled 为 true 但未配置 password（SMTP 授权码）")
    from_addr = str(email.get("from_addr") or "").strip()
    if not _EMAIL_RE.match(from_addr):
        errors.append(f"push.email.from_addr 不是有效邮箱，实际: {from_addr!r}")
    if not to_addrs:
        errors.append("push.email.enabled 为 true 但 to_addrs 为空")
    elif isinstance(to_addrs, list):
        for address in to_addrs:
            if not _EMAIL_RE.match(str(address).strip()):
                errors.append(f"push.email.to_addrs 含无效邮箱: {address!r}")


def _resolve_secret(section: dict[str, Any], key: str) -> str:
    """优先读字段值，其次读 <key>_env 指向的环境变量。"""
    value = str(section.get(key) or "").strip()
    if value:
        return value
    env_name = str(section.get(f"{key}_env") or "").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def _resolve_dir(base: Path, value: object, label: str) -> Path:
    text = str(value or "").strip() or "."
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ConfigError([f"{label} 指向已存在的文件，应为目录: {resolved}"])
    return resolved


def _string_tuple(value: object, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """字符串列表解析：None 用默认值；空列表就是空（不能回退到默认）。"""
    if value is None:
        return default
    if not isinstance(value, list):
        return default
    return tuple(str(entry).strip() for entry in value if str(entry).strip())


def _build_pipeline(raw: dict[str, Any]) -> PipelineConfig:
    pipeline_raw = raw.get("pipeline") if isinstance(raw.get("pipeline"), dict) else {}
    prefilter_raw = (
        pipeline_raw.get("prefilter") if isinstance(pipeline_raw.get("prefilter"), dict) else {}
    )
    dedup_raw = pipeline_raw.get("dedup") if isinstance(pipeline_raw.get("dedup"), dict) else {}
    enrich_raw = pipeline_raw.get("enrich") if isinstance(pipeline_raw.get("enrich"), dict) else {}
    rank_raw = pipeline_raw.get("rank") if isinstance(pipeline_raw.get("rank"), dict) else {}
    bonus_raw = rank_raw.get("interest_bonus")
    bonus = (
        {str(key): float(value) for key, value in bonus_raw.items()}
        if isinstance(bonus_raw, dict)
        else dict(DEFAULT_INTEREST_BONUS)
    )
    return PipelineConfig(
        prefilter=PrefilterConfig(
            gate_sources=_string_tuple(
                prefilter_raw.get("gate_sources"), default=DEFAULT_GATE_SOURCES
            ),
            blacklist_keywords=_string_tuple(prefilter_raw.get("blacklist_keywords")),
            extra_ai_keywords=_string_tuple(prefilter_raw.get("extra_ai_keywords")),
            use_builtin_blacklist=bool(prefilter_raw.get("use_builtin_blacklist", True)),
        ),
        dedup=DedupConfig(
            title_similarity=float(dedup_raw.get("title_similarity", DEFAULT_TITLE_SIMILARITY)),
            max_hours_apart=int(dedup_raw.get("max_hours_apart", DEFAULT_MAX_HOURS_APART)),
            llm_refine=bool(dedup_raw.get("llm_refine", DEFAULT_LLM_REFINE)),
        ),
        enrich=EnrichConfig(
            max_items=int(enrich_raw.get("max_items", DEFAULT_ENRICH_MAX_ITEMS)),
            price_input_per_million=float(
                enrich_raw.get("price_input_per_million", DEFAULT_PRICE_INPUT)
            ),
            price_output_per_million=float(
                enrich_raw.get("price_output_per_million", DEFAULT_PRICE_OUTPUT)
            ),
            time_budget_seconds=int(
                enrich_raw.get("time_budget_seconds", DEFAULT_ENRICH_TIME_BUDGET)
            ),
        ),
        rank=RankConfig(interest_bonus=bonus),
    )


def _build(path: Path, raw: dict[str, Any], warnings: list[str]) -> Config:
    base = path.parent

    app_raw = raw.get("app") if isinstance(raw.get("app"), dict) else {}
    app = AppConfig(
        timezone=str(app_raw.get("timezone", "Asia/Shanghai")),
        window_hours=int(app_raw.get("window_hours", 24)),
        top_n=int(app_raw.get("top_n", 12)),
        log_level=str(app_raw.get("log_level", "INFO")).upper(),
        data_dir=_resolve_dir(base, app_raw.get("data_dir", "data"), "app.data_dir"),
        log_dir=_resolve_dir(base, app_raw.get("log_dir", "logs"), "app.log_dir"),
        deliver_by=str(app_raw.get("deliver_by", "08:00")).strip(),
    )

    llm_raw = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    llm = LLMConfig(
        provider=str(llm_raw.get("provider", "deepseek")),
        # base_url / model 由 validate() 保证非空，这里不留默认值，避免出现"死默认值"
        base_url=str(llm_raw.get("base_url") or "").rstrip("/"),
        model=str(llm_raw.get("model") or ""),
        api_key=_resolve_secret(llm_raw, "api_key"),
        batch_size=int(llm_raw.get("batch_size", 15)),
        daily_cost_limit_cny=float(llm_raw.get("daily_cost_limit_cny", 1.0)),
    )

    proxy_raw = raw.get("proxy") if isinstance(raw.get("proxy"), dict) else {}
    proxy = ProxyConfig(
        enabled=bool(proxy_raw.get("enabled")),
        url=str(proxy_raw.get("url") or "").strip(),
    )

    sources = tuple(
        SourceConfig(
            id=str(item["id"]).strip(),
            name=str(item.get("name") or item["id"]).strip(),
            type=str(item["type"]).strip(),
            url=str(item["url"]).strip(),
            enabled=bool(item.get("enabled", False)),
            weight=float(item.get("weight", 1.0)),
            extra=dict(item.get("extra") or {}),
        )
        for item in raw.get("sources", [])
    )

    push_raw = raw.get("push") if isinstance(raw.get("push"), dict) else {}
    wecom_raw = push_raw.get("wecom") if isinstance(push_raw.get("wecom"), dict) else {}
    email_raw = push_raw.get("email") if isinstance(push_raw.get("email"), dict) else {}

    push = PushConfig(
        wecom=WeComConfig(
            enabled=bool(wecom_raw.get("enabled")),
            webhook=_resolve_secret(wecom_raw, "webhook"),
        ),
        email=EmailConfig(
            enabled=bool(email_raw.get("enabled")),
            smtp_host=str(email_raw.get("smtp_host") or "").strip(),
            smtp_port=int(email_raw.get("smtp_port", 465)),
            use_ssl=bool(email_raw.get("use_ssl", True)),
            username=str(email_raw.get("username") or "").strip(),
            password=_resolve_secret(email_raw, "password"),
            from_addr=str(email_raw.get("from_addr") or "").strip(),
            to_addrs=tuple(str(address).strip() for address in (email_raw.get("to_addrs") or [])),
            attach_image=bool(email_raw.get("attach_image", False)),
        ),
    )

    pipeline = _build_pipeline(raw)

    return Config(
        path=path,
        app=app,
        llm=llm,
        proxy=proxy,
        sources=sources,
        push=push,
        pipeline=pipeline,
        warnings=tuple(warnings),
    )
