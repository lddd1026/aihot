"""logging_setup 模块单元测试。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aihot.logging_setup import JsonFormatter, SecretFilter, TextFormatter, setup_logging

SECRET = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=super-secret-123"


def make_record(msg: str, args: tuple[object, ...] = ()) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)


def test_secret_filter_scrubs_message() -> None:
    record = make_record("sending to %s", (SECRET,))
    SecretFilter([SECRET]).filter(record)
    message = record.getMessage()
    assert "super-secret-123" not in message
    assert "***" in message


def test_secret_filter_scrubs_patterns() -> None:
    record = make_record("key=abcdef123456 and sk-abcdefgh12345")
    SecretFilter([]).filter(record)
    message = record.getMessage()
    assert "key=***" in message
    assert "sk-***" in message


def test_json_formatter_outputs_valid_json() -> None:
    record = make_record("hello")
    record.stage = "collect"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello"
    assert payload["stage"] == "collect"
    assert payload["level"] == "INFO"


class FakeURL:
    """模拟 httpx.URL 这类“密钥藏在对象里”的参数。"""

    def __init__(self, url: str) -> None:
        self._url = url

    def __str__(self) -> str:
        return self._url


def test_text_formatter_redacts_object_args() -> None:
    record = make_record("HTTP Request: GET %s", (FakeURL(SECRET),))
    formatted = TextFormatter("%(message)s", secrets=[SECRET]).format(record)
    assert "super-secret-123" not in formatted
    assert "***" in formatted


def test_json_formatter_redacts_object_args() -> None:
    record = make_record("HTTP Request: GET %s", (FakeURL(SECRET),))
    payload = json.loads(JsonFormatter(secrets=[SECRET]).format(record))
    assert "super-secret-123" not in payload["msg"]


def test_setup_logging_silences_httpx_info(tmp_path: Path) -> None:
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    try:
        setup_logging(tmp_path, "INFO", [])
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING
    finally:
        for handler in list(logging.getLogger().handlers):
            handler.close()
            logging.getLogger().removeHandler(handler)
        for handler in old_handlers:
            logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(old_level)


def test_setup_logging_writes_redacted_json(tmp_path: Path) -> None:
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    try:
        logger = setup_logging(tmp_path, "INFO", [SECRET])
        logger.info("webhook is %s", SECRET)
        for handler in logger.handlers:
            handler.flush()
        content = (tmp_path / "aihot.log").read_text(encoding="utf-8")
        assert SECRET not in content
        assert "super-secret-123" not in content
        lines = [line for line in content.splitlines() if line.strip()]
        assert lines
        payload = json.loads(lines[-1])
        assert payload["msg"].startswith("webhook is")
    finally:
        for handler in list(logging.getLogger().handlers):
            handler.close()
            logging.getLogger().removeHandler(handler)
        for handler in old_handlers:
            logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(old_level)
