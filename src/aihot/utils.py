"""通用工具：URL 规范化、文本清洗、时间处理、脱敏。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

# 常见跟踪参数：去除后 URL 才算“规范”，用于去重
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "spm",
        "scm",
        "from",
        "from_source",
        "ref",
        "ref_src",
        "share_token",
        "share_source",
        "wxshare",
        "weibo_id",
        "fbclid",
        "gclid",
        "yclid",
        "igshid",
        "mc_cid",
        "mc_eid",
    }
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(key|token|password|secret|api_key)=([A-Za-z0-9\-_.]{6,})"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{8,}"),
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def detect_lang(text: str) -> str:
    """粗略判断语言：含中日韩统一表意文字视为中文（zh），否则 en。"""
    return "zh" if _CJK_RE.search(text or "") else "en"


def normalize_url(url: str) -> str:
    """URL 规范化：去 fragment、去跟踪参数、统一大小写与尾部斜杠、排序查询参数。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = re.sub(r"/{2,}", "/", parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query_pairs = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    )
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    """规范化 URL 的稳定哈希（去重键）。"""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    """文本哈希（标题等）。"""
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()


def clean_html(raw: str, limit: int = 1000) -> str:
    """去除 HTML 标签与脚本样式，压缩空白，截断到 limit 字符。"""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    return text[:limit]


def now_utc() -> datetime:
    """当前 UTC 时间（统一时间基准）。"""
    return datetime.now(UTC)


def parse_datetime(value: object) -> datetime | None:
    """解析各种格式时间为 UTC datetime；无时区按 UTC 处理；失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = dateutil_parser.parse(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def within_window(published: datetime | None, hours: int, *, now: datetime | None = None) -> bool:
    """判断发布时间是否落在最近 hours 小时内（无时间视为不通过）。"""
    if published is None:
        return False
    reference = now or now_utc()
    return published >= reference - timedelta(hours=hours)


def to_local(dt: datetime, tz_name: str) -> datetime:
    """UTC 时间转指定时区。"""
    return dt.astimezone(ZoneInfo(tz_name))


def mask_url_credentials(url: str) -> str:
    """隐藏 URL 中的 user:pass@ 凭据（用于终端与日志展示）。无法解析时整体打码。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))


def redact_text(text: object, secrets: Iterable[str] = ()) -> str:
    """把已知密钥与常见密钥模式替换为 ***，用于日志与输出脱敏。"""
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 6:
            out = out.replace(secret, "***")
    out = _SECRET_PATTERNS[0].sub(r"\1=***", out)
    return _SECRET_PATTERNS[1].sub("sk-***", out)
