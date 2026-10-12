"""发布闸门脚本的单元测试（不触网、不读真实 config.yaml）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_gate  # noqa: E402


def test_test_emails_are_ignored() -> None:
    for address in (
        "test@qq.com",
        "sender@qq.com",
        "receiver@qq.com",
        "a@b.com",
        "someone@example.com",
        "noreply@test.com",
    ):
        assert publish_gate._is_test_email(address) is True, address


def test_real_looking_emails_are_flagged() -> None:
    for address in (
        "1036179451@qq.com",
        "zhangsan.li@foxmail.com",
        "real.person@gmail.com",
    ):
        assert publish_gate._is_test_email(address) is False, address


def test_collect_targets_reads_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  api_key: sk-realkey-1234567890
push:
  wecom:
    webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123def456"
  email:
    username: "someone@qq.com"
    password: "smtpauthcode123"
    to_addrs: ["someone@qq.com"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(publish_gate, "CONFIG", config)

    targets = publish_gate.collect_targets()

    assert targets["LLM API key"] == "sk-realkey-1234567890"
    assert targets["企业微信 key"] == "abc123def456"
    assert targets["SMTP 授权码"] == "smtpauthcode123"
    assert any(name.startswith("收件人") for name in targets)


def test_collect_targets_without_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(publish_gate, "CONFIG", tmp_path / "missing.yaml")
    assert publish_gate.collect_targets() == {}
