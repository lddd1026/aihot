"""推送层单元测试（假 HTTP / 假 SMTP，不产生真实发送）。"""

from __future__ import annotations

import base64
import hashlib
import json
import smtplib
from pathlib import Path

import httpx
import pytest

from aihot.config import EmailConfig
from aihot.delivery import EmailNotifier, NotifyError, WeComNotifier

WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake-key-123456"


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})


def make_wecom(handler) -> WeComNotifier:
    return WeComNotifier(WEBHOOK, http=httpx.Client(transport=httpx.MockTransport(handler)))


class TestWeCom:
    def test_send_text_payload(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"errcode": 0})

        notifier = make_wecom(handler)
        notifier.send_text("hello")
        assert captured["msgtype"] == "text"
        assert captured["text"]["content"] == "hello"
        notifier.close()

    def test_send_markdown_payload(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"errcode": 0})

        notifier = make_wecom(handler)
        notifier.send_markdown("**标题**")
        assert captured["msgtype"] == "markdown"
        assert captured["markdown"]["content"] == "**标题**"
        notifier.close()

    def test_markdown_over_limit_rejected(self) -> None:
        notifier = make_wecom(ok_handler)
        with pytest.raises(NotifyError, match="分片"):
            notifier.send_markdown("x" * 5000)
        notifier.close()

    def test_send_image_encodes_base64_and_md5(self, tmp_path: Path) -> None:
        image = tmp_path / "a.jpg"
        payload_bytes = b"\xff\xd8\xff" + b"x" * 100
        image.write_bytes(payload_bytes)
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"errcode": 0})

        notifier = make_wecom(handler)
        notifier.send_image(image)
        assert captured["msgtype"] == "image"
        assert base64.b64decode(captured["image"]["base64"]) == payload_bytes
        assert captured["image"]["md5"] == hashlib.md5(payload_bytes).hexdigest()
        notifier.close()

    def test_image_over_2mb_rejected(self, tmp_path: Path) -> None:
        image = tmp_path / "big.jpg"
        image.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        notifier = make_wecom(ok_handler)
        with pytest.raises(NotifyError, match="2MB"):
            notifier.send_image(image)
        notifier.close()

    def test_missing_image_file(self, tmp_path: Path) -> None:
        notifier = make_wecom(ok_handler)
        with pytest.raises(NotifyError, match="读取失败"):
            notifier.send_image(tmp_path / "nope.jpg")
        notifier.close()

    def test_errcode_nonzero_raises(self) -> None:
        notifier = make_wecom(
            lambda request: httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid"})
        )
        with pytest.raises(NotifyError, match="93000"):
            notifier.send_text("x")
        notifier.close()

    def test_http_error_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        notifier = make_wecom(handler)
        with pytest.raises(NotifyError, match="ConnectError"):
            notifier.send_text("x")
        notifier.close()

    def test_non_200_status_raises(self) -> None:
        notifier = make_wecom(lambda request: httpx.Response(502))
        with pytest.raises(NotifyError, match="502"):
            notifier.send_text("x")
        notifier.close()

    def test_empty_webhook_raises(self) -> None:
        notifier = WeComNotifier("", http=httpx.Client(transport=httpx.MockTransport(ok_handler)))
        with pytest.raises(NotifyError, match="未配置"):
            notifier.send_text("x")
        notifier.close()


class FakeSMTP:
    """最小可用的假 SMTP 连接。"""

    def __init__(self) -> None:
        self.sent: list = []
        self.logged_in: tuple[str, str] | None = None
        self.quit_called = False

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message) -> None:  # noqa: ANN001 - 测试替身
        self.sent.append(message)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        self.quit_called = True


def make_email_config(**overrides: object) -> EmailConfig:
    base: dict = {
        "enabled": True,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "use_ssl": True,
        "username": "sender@qq.com",
        "password": "auth-code",
        "from_addr": "sender@qq.com",
        "to_addrs": ("receiver@qq.com",),
    }
    base.update(overrides)
    return EmailConfig(**base)


class TestEmail:
    def test_send_html_with_inline_image(self, tmp_path: Path) -> None:
        image = tmp_path / "digest.jpg"
        image.write_bytes(b"\xff\xd8\xff" + b"y" * 50)
        fake = FakeSMTP()
        notifier = EmailNotifier(make_email_config(), smtp_factory=lambda: fake)
        notifier.send("主题", "<p>正文</p>", images=[image])
        assert fake.logged_in == ("sender@qq.com", "auth-code")
        assert fake.quit_called is True
        assert len(fake.sent) == 1
        message = fake.sent[0]
        assert message["Subject"] == "主题"
        assert message["To"] == "receiver@qq.com"
        assert message.is_multipart()
        raw = message.as_string()
        assert "digest-image-1" in raw
        assert "image/jpeg" in raw

    def test_send_without_image(self) -> None:
        fake = FakeSMTP()
        notifier = EmailNotifier(make_email_config(), smtp_factory=lambda: fake)
        notifier.send("s", "<p>x</p>")
        assert "image/" not in fake.sent[0].as_string()

    def test_no_recipients_raises(self) -> None:
        notifier = EmailNotifier(make_email_config(to_addrs=()), smtp_factory=FakeSMTP)
        with pytest.raises(NotifyError, match="收件地址"):
            notifier.send("s", "<p>x</p>")

    def test_smtp_exception_wrapped(self) -> None:
        class Boom(FakeSMTP):
            def send_message(self, message) -> None:  # noqa: ANN001 - 测试替身
                raise smtplib.SMTPException("nope")

        notifier = EmailNotifier(make_email_config(), smtp_factory=Boom)
        with pytest.raises(NotifyError, match="邮件发送失败"):
            notifier.send("s", "<p>x</p>")

    def test_connect_failure_wrapped(self) -> None:
        def factory() -> FakeSMTP:
            raise smtplib.SMTPConnectError(421, "cannot connect")

        notifier = EmailNotifier(make_email_config(), smtp_factory=factory)
        with pytest.raises(NotifyError, match="SMTP 连接或登录失败"):
            notifier.send("s", "<p>x</p>")
