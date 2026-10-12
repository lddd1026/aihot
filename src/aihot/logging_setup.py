"""日志初始化：JSON lines 落盘 + 控制台输出 + 密钥脱敏（双层防护）。"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from aihot.utils import redact_text

_EXTRA_FIELDS = ("stage", "source_id", "run_id", "duration_ms", "count")
_NOISY_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")


def _clean_secrets(secrets: list[str] | tuple[str, ...]) -> list[str]:
    return [s for s in secrets if isinstance(s, str) and len(s) >= 6]


class SecretFilter(logging.Filter):
    """记录级脱敏：替换消息与字符串参数中的密钥。"""

    def __init__(self, secrets: list[str] | tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = _clean_secrets(secrets)

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value, self.secrets)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: self._scrub(value) for key, value in record.args.items()}
            else:
                record.args = tuple(self._scrub(value) for value in record.args)
        return True


class TextFormatter(logging.Formatter):
    """控制台文本格式。输出前兜底脱敏，覆盖对象参数绕过过滤器的情况。"""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        secrets: list[str] | tuple[str, ...] = (),
    ) -> None:
        super().__init__(fmt, datefmt)
        self.secrets = _clean_secrets(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record), self.secrets)


class JsonFormatter(logging.Formatter):
    """JSON lines 格式。输出前兜底脱敏。"""

    def __init__(self, secrets: list[str] | tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = _clean_secrets(secrets)

    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, tz=UTC).astimezone()
        payload: dict[str, Any] = {
            "ts": created.isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact_text(json.dumps(payload, ensure_ascii=False), self.secrets)


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    secrets: list[str] | tuple[str, ...] = (),
) -> logging.Logger:
    """配置根日志：控制台 + 轮转文件（5MB x 7）。可重复调用（幂等）。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    numeric_level = getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(numeric_level)

    secret_values = _clean_secrets(secrets)
    secret_filter = SecretFilter(secret_values)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S", secret_values)
    )
    console.addFilter(secret_filter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "aihot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter(secret_values))
    file_handler.addFilter(secret_filter)
    root.addHandler(file_handler)

    # 第三方库的请求日志噪音大，且可能携带含密钥的 URL，统一降级
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return root
