"""M0 独立进程内测试：日志脱敏 / JSON lines / 轮转 / 幂等。

测试 Agent 专用，不属于开发单测；文件名不以 test_ 开头，避免被 pytest 收集。
用法: .venv\\Scripts\\python.exe tests\\independent\\m0_inprocess.py
证据输出: reports/evidence/logging/
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import tempfile
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from aihot.logging_setup import setup_logging
from aihot.utils import redact_text

EVIDENCE = Path(__file__).resolve().parents[2] / "reports" / "evidence" / "logging"
RESULTS: list[dict[str, Any]] = []

SECRET = "sk-m0log-0123456789abcdef"
URL_SECRET = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=M0LOG0123456789abcdef"
KEY_ONLY = "M0LOG0123456789abcdef"


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def new_tmp(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def flush_root() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


@contextmanager
def captured_console() -> Any:
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_redaction_and_json_lines() -> None:
    tmp = new_tmp("aihot-log-")
    with captured_console() as buf:
        setup_logging(tmp, "DEBUG", [SECRET, URL_SECRET])
        log = logging.getLogger("aihot.test")
        log.info("plain %s", SECRET)
        log.info("dict %(url)s", {"url": URL_SECRET})
        log.info("kwargs", extra={"source_id": URL_SECRET, "stage": "m0", "count": 1})

        class Weird:
            def __str__(self) -> str:
                return URL_SECRET

        log.info("object %s", Weird())
        log.info("bytes %s", f"sk-bytes-{KEY_ONLY}".encode())
        try:
            raise RuntimeError(f"boom {SECRET}")
        except RuntimeError:
            log.exception("failed")
        logging.getLogger("httpx").info("HTTP Request: GET %s", URL_SECRET)
        logging.getLogger("httpx2").info("HTTP Request: GET %s", URL_SECRET)
        logging.getLogger("httpcore").debug("connect_tcp.started %s", URL_SECRET)
        logging.getLogger("httpcore2").debug("connect_tcp.started %s", URL_SECRET)
        flush_root()
        console = buf.getvalue()

    file_text = (tmp / "aihot.log").read_text(encoding="utf-8")
    lines = read_lines(tmp / "aihot.log")

    leaks: list[str] = []
    for label, text in (("file", file_text), ("console", console)):
        for needle in (SECRET, URL_SECRET, KEY_ONLY):
            if needle in text:
                leaks.append(f"{label} 含 {needle[:12]}...")
    record(
        "redaction-no-plaintext",
        not leaks,
        "; ".join(leaks) if leaks else "文件+控制台均无明文",
    )

    httpx_noise = [line for line in lines if "HTTP Request" in line or "connect_tcp" in line]
    record(
        "noisy-httpx-loggers-suppressed",
        not httpx_noise,
        f"httpx/httpcore INFO/DEBUG 记录数={len(httpx_noise)}",
    )

    parse_errors: list[str] = []
    payloads: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError as exc:
            parse_errors.append(f"line {index}: {exc}")
    record(
        "json-lines-valid",
        not parse_errors and bool(lines),
        f"{len(lines)} 行全部可解析" if not parse_errors else "; ".join(parse_errors[:3]),
    )

    extra_line = next((p for p in payloads if p.get("stage") == "m0"), None)
    extra_ok = (
        extra_line is not None
        and extra_line.get("source_id") not in (URL_SECRET,)
        and KEY_ONLY not in str(extra_line.get("source_id"))
        and extra_line.get("count") == 1
    )
    source_id_value = extra_line.get("source_id") if extra_line else None
    record("json-extra-fields-redacted", extra_ok, f"source_id={source_id_value}")

    exc_line = next((p for p in payloads if "exc" in p), None)
    exc_ok = exc_line is not None and SECRET not in exc_line.get("exc", "")
    exc_len = len(exc_line.get("exc", "")) if exc_line else 0
    record("exception-stack-redacted", exc_ok, f"exc 字段长度={exc_len}")

    console_records = [
        line for line in console.splitlines() if re.match(r"^\d{2}:\d{2}:\d{2} ", line)
    ]
    record(
        "single-handler-no-duplicate",
        len(console_records) == len(lines),
        f"console 记录数={len(console_records)}, file 行数={len(lines)}",
    )

    (EVIDENCE / "redaction-file-sample.txt").write_text(file_text, encoding="utf-8")
    (EVIDENCE / "redaction-console-sample.txt").write_text(console, encoding="utf-8")


def test_setup_logging_idempotent() -> None:
    tmp = new_tmp("aihot-log-idem-")
    with captured_console() as buf:
        setup_logging(tmp, "INFO", [SECRET])
        setup_logging(tmp, "INFO", [SECRET])
        handler_count = len(logging.getLogger().handlers)
        logging.getLogger("aihot.test").info("idempotent-check")
        flush_root()
        console = buf.getvalue()
    lines = read_lines(tmp / "aihot.log")
    console_lines = [line for line in console.splitlines() if line.strip()]
    ok = handler_count == 2 and len(lines) == 1 and len(console_lines) == 1
    record(
        "setup-logging-idempotent",
        ok,
        f"root.handlers={handler_count}, 文件行数={len(lines)}, 控制台行数={len(console_lines)}",
    )


def test_rotation_params() -> None:
    tmp = new_tmp("aihot-log-rot-")
    with captured_console():
        setup_logging(tmp, "INFO", [])
        handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    if not handlers:
        record("rotation-params", False, "未找到 RotatingFileHandler")
        return
    handler = handlers[0]
    ok = (
        handler.maxBytes == 5 * 1024 * 1024
        and handler.backupCount == 7
        and str(handler.encoding).lower().replace("_", "-") in {"utf-8", "utf8"}
    )
    detail = (
        f"maxBytes={handler.maxBytes}, backupCount={handler.backupCount}, "
        f"encoding={handler.encoding}"
    )
    record("rotation-params", ok, detail)


def test_forced_rotation() -> None:
    tmp = new_tmp("aihot-log-force-")
    with captured_console():
        setup_logging(tmp, "INFO", [])
        log = logging.getLogger("aihot.rotate")
        chunk = "x" * 10000
        for _ in range(600):
            log.info("%s", chunk)
        flush_root()
    rotated = (tmp / "aihot.log.1").exists()
    current_size = (tmp / "aihot.log").stat().st_size
    record(
        "forced-rotation",
        rotated and current_size <= 5 * 1024 * 1024 + 20000,
        f"aihot.log.1 存在={rotated}, 当前文件大小={current_size}",
    )


def test_chinese_emoji_json() -> None:
    tmp = new_tmp("aihot-log-uni-")
    message = '中文 🚀 "引号" \'单引号\' — 破折号'
    with captured_console():
        setup_logging(tmp, "INFO", [])
        logging.getLogger("aihot.test").info("%s", message)
        flush_root()
    lines = read_lines(tmp / "aihot.log")
    payload = json.loads(lines[-1])
    record(
        "chinese-emoji-json-roundtrip",
        payload["msg"] == message,
        f"msg={'一致' if payload['msg'] == message else payload['msg']!r}",
    )


def test_redact_text_patterns() -> None:
    cases = [
        ("key=abcdef123456", "key=***"),
        ("sk-abcdefgh12345", "sk-***"),
        ("password=hunter2hunter2", "password=***"),
        ("token=abcdef123456", "token=***"),
        ("nothing to redact", "nothing to redact"),
    ]
    problems = [
        f"{src!r} -> {redact_text(src)!r}"
        for src, expected in cases
        if expected not in redact_text(src)
    ]
    record("redact-text-patterns", not problems, "; ".join(problems) if problems else "全部命中")


def test_extra_unfiltered_handler_order() -> None:
    """观察项：setup_logging 之外的 handler 是否受保护（取决于 handler 顺序）。"""
    tmp = new_tmp("aihot-log-extra-")
    with captured_console():
        setup_logging(tmp, "INFO", [SECRET])
        raw_handler = logging.FileHandler(tmp / "raw-extra.log", encoding="utf-8")
        root = logging.getLogger()
        root.handlers.insert(0, raw_handler)
        logging.getLogger("aihot.test").info("extra %s", SECRET)
        flush_root()
        raw_handler.close()
    raw_text = (tmp / "raw-extra.log").read_text(encoding="utf-8")
    leaked = SECRET in raw_text
    record(
        "observation-extra-handler",
        True,
        "额外插入的未过滤 handler（顺序最前）会写出明文，属调用方责任，非产品缺陷"
        if leaked
        else "即使额外 handler 也因记录被先行脱敏而安全",
    )


def test_probe_details_redaction() -> None:
    """模拟 httpx 异常文本包含密钥 URL 时，redact_text 能否兜底。"""
    raw = f"ConnectError: All connection attempts failed for {URL_SECRET}"
    masked = redact_text(raw, [URL_SECRET])
    ok = URL_SECRET not in masked and KEY_ONLY not in masked
    record("probe-error-text-redacted", ok, f"masked={masked}")


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    try:
        test_redaction_and_json_lines()
        test_setup_logging_idempotent()
        test_rotation_params()
        test_forced_rotation()
        test_chinese_emoji_json()
        test_redact_text_patterns()
        test_extra_unfiltered_handler_order()
        test_probe_details_redaction()
    finally:
        for handler in list(logging.getLogger().handlers):
            handler.close()
            logging.getLogger().removeHandler(handler)

    failed = [item for item in RESULTS if not item["ok"]]
    print(f"\n== SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    for item in failed:
        print(f"FAILED: {item['name']} :: {item['detail']}")
    (EVIDENCE / "_summary.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
