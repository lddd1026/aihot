"""cli 模块单元测试。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from aihot.cli import main

MINIMAL_CONFIG = """
app:
  timezone: Asia/Shanghai
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
proxy:
  enabled: false
push:
  wecom:
    enabled: false
  email:
    enabled: false
sources:
  - id: qbitai
    type: rss
    url: https://www.qbitai.com/feed
    enabled: true
    weight: 1.0
"""


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    yield
    for handler in list(logging.getLogger().handlers):
        handler.close()
        logging.getLogger().removeHandler(handler)
    for handler in old_handlers:
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(old_level)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


ENRICH_CONFIG = """
app:
  timezone: Asia/Shanghai
  data_dir: "{data_dir}"
  log_dir: "{log_dir}"
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
push:
  wecom:
    enabled: false
pipeline:
  dedup:
    llm_refine: false
sources:
  - id: s1
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
"""


def _make_raw_item(index: int):
    from datetime import UTC, datetime

    from aihot.models import RawItem

    return RawItem(
        source_id="s1",
        title=f"OpenAI 发布新模型 {index}",
        url=f"https://example.com/{index}",
        published_at=datetime(2026, 9, 24, 8, index % 60, tzinfo=UTC),
        raw_summary="摘要",
    )


def _cluster_ids(db_path: Path) -> list[int]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT cluster_id FROM clusters ORDER BY cluster_id").fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def test_enrich_cluster_ids_are_monotonic_across_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P1 回归：重复运行时簇编号必须全局单调，绝不能重置为 1..n 覆盖历史簇。"""
    from aihot.store import Store

    data_dir = tmp_path / "data"
    config_path = write_config(
        tmp_path,
        ENRICH_CONFIG.format(
            data_dir=data_dir.as_posix(),
            log_dir=(tmp_path / "logs").as_posix(),
        ),
    )
    db_path = data_dir / "aihot.db"
    with Store(db_path) as store:
        store.save_items([_make_raw_item(index) for index in range(3)])

    assert main(["enrich", "--no-llm", "--config", str(config_path)]) == 0
    first_ids = _cluster_ids(db_path)
    assert first_ids == [1, 2, 3]

    # 重置为未加工，模拟第二轮
    with Store(db_path) as store:
        store._conn.execute("UPDATE items SET enriched_at = NULL")  # noqa: SLF001
        store._conn.commit()

    assert main(["enrich", "--no-llm", "--config", str(config_path)]) == 0
    second_ids = _cluster_ids(db_path)
    assert len(second_ids) == 6  # 历史簇保留 + 本轮新增 3 个
    assert second_ids[:3] == first_ids, "历史簇被覆盖"
    assert min(second_ids[3:]) > max(first_ids), f"簇编号被重置: {second_ids}"
    capsys.readouterr()


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert "aihot" in capsys.readouterr().out


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_unknown_command_exits_two() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["no-such-command"])
    assert exc_info.value.code == 2


def test_check_valid_config_after_subcommand(tmp_path: Path) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    assert main(["check", "--config", str(path)]) == 0


def test_check_valid_config_before_subcommand(tmp_path: Path) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    assert main(["--config", str(path), "check"]) == 0


def test_check_invalid_config_exit_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, "app:\n  window_hours: -1\n")
    assert main(["check", "--config", str(path)]) == 3
    output = capsys.readouterr().out
    assert "配置校验未通过" in output
    assert "window_hours" in output


def test_check_missing_config_exit_code_3(tmp_path: Path) -> None:
    assert main(["check", "--config", str(tmp_path / "missing.yaml")]) == 3


def test_check_yaml_error_hides_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "LEAKTEST0123456789abcdef"
    text = (
        "push:\n"
        "  wecom:\n"
        "    webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
        f"{secret}: oops\n"
    )
    path = write_config(tmp_path, text)
    assert main(["check", "--config", str(path)]) == 3
    output = capsys.readouterr().out
    assert secret not in output
    assert "YAML 解析失败" in output


def test_check_masks_proxy_credentials(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = MINIMAL_CONFIG.replace(
        "proxy:\n  enabled: false",
        "proxy:\n  enabled: true\n  url: http://m0user:m0pass@127.0.0.1:7890",
    )
    path = write_config(tmp_path, text)
    assert main(["check", "--config", str(path)]) == 0
    output = capsys.readouterr().out
    assert "m0pass" not in output
    assert "http://***@127.0.0.1:7890" in output


def test_check_log_dir_under_file_reports_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "blocker").write_text("i am a file", encoding="utf-8")
    text = MINIMAL_CONFIG.replace(
        "app:\n  timezone: Asia/Shanghai",
        "app:\n  timezone: Asia/Shanghai\n  log_dir: blocker/logs",
    )
    path = write_config(tmp_path, text)
    assert main(["check", "--config", str(path)]) == 3
    output = capsys.readouterr().out
    assert "日志目录不可用" in output


def test_invalid_proxy_url_value_is_truncated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "B" * 200
    text = MINIMAL_CONFIG.replace(
        "proxy:\n  enabled: false",
        f"proxy:\n  enabled: true\n  url: not-a-url-{token}",
    )
    path = write_config(tmp_path, text)
    assert main(["check", "--config", str(path)]) == 3
    output = capsys.readouterr().out
    assert token not in output


def test_fetch_window_hours_out_of_range_exits_two(tmp_path: Path) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    for value in ("0", "-5", "200", "abc"):
        with pytest.raises(SystemExit) as exc_info:
            main(["fetch", "--config", str(path), "--window-hours", value, "--dry-run"])
        assert exc_info.value.code == 2


def test_fetch_limit_negative_exits_two(tmp_path: Path) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    with pytest.raises(SystemExit) as exc_info:
        main(["fetch", "--config", str(path), "--limit", "-1", "--dry-run"])
    assert exc_info.value.code == 2


def test_check_bad_proxy_ipv6_reports_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = MINIMAL_CONFIG.replace(
        "proxy:\n  enabled: false", 'proxy:\n  enabled: true\n  url: "http://[::1"'
    )
    path = write_config(tmp_path, text)
    assert main(["check", "--config", str(path)]) == 3
    output = capsys.readouterr().out
    assert "proxy.url" in output
    assert "Traceback" not in output


def test_check_bad_proxy_port_reports_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = MINIMAL_CONFIG.replace(
        "proxy:\n  enabled: false", 'proxy:\n  enabled: true\n  url: "http://127.0.0.1:abc"'
    )
    path = write_config(tmp_path, text)
    assert main(["check", "--config", str(path)]) == 3
    assert "proxy.url" in capsys.readouterr().out


def test_preview_invalid_date_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    assert main(["preview", "--config", str(path), "--date", "2026-13-45"]) == 1
    assert "日期格式不正确" in capsys.readouterr().out


def test_resend_invalid_date_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    assert main(["resend", "--config", str(path), "--date", "not-a-date"]) == 1
    assert "日期格式不正确" in capsys.readouterr().out


def test_preview_without_archive_builds_from_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(tmp_path, MINIMAL_CONFIG)
    assert main(["preview", "--config", str(path), "--no-image"]) == 0
    output = capsys.readouterr().out
    assert "改用数据库最近窗口重新组装" in output


SKIP_CONFIG = """
app:
  timezone: Asia/Shanghai
  data_dir: "{data_dir}"
  log_dir: "{log_dir}"
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
push:
  wecom:
    enabled: false
  email:
    enabled: false
sources:
  - id: s1
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
"""


def test_check_quick_proxy_down_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """M5 修复：代理不可用只是可降级警告，不再拦截每日运行。"""
    from aihot import cli
    from aihot.checks import CheckResult

    path = write_config(tmp_path, MINIMAL_CONFIG)
    monkeypatch.setattr(
        cli,
        "run_quick_checks",
        lambda cfg: [
            CheckResult("代理", False, "连接失败（将降级为直连）", 10, fatal=False),
            CheckResult("企业微信 webhook", True, "errcode=93017", 20),
        ],
    )
    assert main(["check", "--quick", "--config", str(path)]) == 0
    assert "[WARN]" in capsys.readouterr().out


def test_check_quick_fatal_failure_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aihot import cli
    from aihot.checks import CheckResult

    path = write_config(tmp_path, MINIMAL_CONFIG)
    monkeypatch.setattr(
        cli,
        "run_quick_checks",
        lambda cfg: [CheckResult("LLM API", False, "连接失败", 10)],
    )
    assert main(["check", "--quick", "--config", str(path)]) == 1
    assert "[ERR]" in capsys.readouterr().out


def test_skip_command_records_run_and_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """跳过不再静默：写 runs 表 + 发告警。"""
    from datetime import UTC, datetime, timedelta

    from aihot import cli
    from aihot.runner import AlertResult
    from aihot.store import Store

    data_dir = tmp_path / "data"
    path = write_config(
        tmp_path,
        SKIP_CONFIG.format(
            data_dir=data_dir.as_posix(), log_dir=(tmp_path / "logs").as_posix()
        ),
    )
    alerts: list[str] = []
    monkeypatch.setattr(
        cli,
        "send_alert",
        lambda cfg, title, details: (
            alerts.append(title),
            AlertResult(delivered=True, channels=["企业微信"]),
        )[1],
    )

    assert main(["--config", str(path), "skip", "--reason", "preflight failed: test"]) == 0

    with Store(data_dir / "aihot.db") as store:
        rows = store.runs_since(datetime.now(UTC) - timedelta(hours=1))
    assert rows and rows[-1]["status"] == "skipped"
    assert alerts == ["今日运行已跳过"]
    assert "已记录跳过" in capsys.readouterr().out


def test_alert_command_prints_fallback_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P2-1 回归：全部通道不可用时，命令必须给出可见提示（不能静默）。"""
    data_dir = tmp_path / "data"
    path = write_config(
        tmp_path,
        SKIP_CONFIG.format(
            data_dir=data_dir.as_posix(), log_dir=(tmp_path / "logs").as_posix()
        ),
    )
    assert main(["--config", str(path), "alert", "--message", "回归验证"]) == 1
    output = capsys.readouterr().out
    assert "未启用" in output
    assert "本地兜底" in output
    assert (tmp_path / "logs" / "ALERTS.log").exists()


def test_enrich_output_shows_topic_drops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M7-2：预筛摘要行必须显示「主题不符」计数（可观测性）。"""
    from datetime import UTC, datetime

    from aihot.models import RawItem
    from aihot.store import Store

    data_dir = tmp_path / "data"
    path = write_config(
        tmp_path,
        ENRICH_CONFIG.format(
            data_dir=data_dir.as_posix(), log_dir=(tmp_path / "logs").as_posix()
        ),
    )
    with Store(data_dir / "aihot.db") as store:
        store.save_items(
            [
                RawItem(
                    source_id="s1",
                    title="OpenAI 发布新模型",
                    url="https://example.com/keep",
                    published_at=datetime.now(UTC),
                ),
                RawItem(
                    source_id="s1",
                    title="某公司完成 B 轮融资",
                    url="https://example.com/drop",
                    published_at=datetime.now(UTC),
                ),
            ]
        )

    assert main(["enrich", "--no-llm", "--dry-run", "--config", str(path)]) == 0
    assert "主题不符 1" in capsys.readouterr().out
