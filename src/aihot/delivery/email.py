"""邮件推送：SMTP_SSL / STARTTLS，支持内嵌日报长图。"""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path

from aihot.config import EmailConfig
from aihot.delivery.base import NotifyError

logger = logging.getLogger("aihot.delivery.email")

SMTP_TIMEOUT = 20.0
IMAGE_CID_TEMPLATE = "digest-image-{index}"


class EmailNotifier:
    """SMTP 邮件推送。`smtp_factory` 可注入假连接用于测试。"""

    name = "email"

    def __init__(
        self,
        config: EmailConfig,
        *,
        smtp_factory: Callable[[], smtplib.SMTP] | None = None,
    ) -> None:
        self.config = config
        self._factory = smtp_factory

    def send(self, subject: str, html: str, *, images: list[Path] | None = None) -> None:
        """发送 HTML 邮件；images 以内嵌（cid）方式附加。"""
        if not self.config.to_addrs:
            raise NotifyError("收件地址为空")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.from_addr or self.config.username
        message["To"] = ", ".join(self.config.to_addrs)
        message.set_content("本邮件为 HTML 格式，请使用支持 HTML 的邮件客户端查看。")
        message.add_alternative(html, subtype="html")

        attachment_paths = [Path(path) for path in (images or [])]
        if attachment_paths:
            html_part = message.get_payload()[-1]
            for index, path in enumerate(attachment_paths, start=1):
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    raise NotifyError(f"内嵌图片读取失败: {exc}") from exc
                subtype = "jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "png"
                html_part.add_related(
                    data,
                    maintype="image",
                    subtype=subtype,
                    cid=f"<{IMAGE_CID_TEMPLATE.format(index=index)}>",
                    filename=path.name,
                )

        try:
            with self._connect() as client:
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotifyError(f"邮件发送失败: {type(exc).__name__}: {exc}") from exc
        logger.info("邮件已发送至 %s", ", ".join(self.config.to_addrs))

    def _connect(self) -> smtplib.SMTP:
        try:
            client = self._factory() if self._factory is not None else self._new_client()
            client.login(self.config.username, self.config.password)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotifyError(f"SMTP 连接或登录失败: {type(exc).__name__}: {exc}") from exc
        return client

    def _new_client(self) -> smtplib.SMTP:
        if self.config.use_ssl:
            return smtplib.SMTP_SSL(
                self.config.smtp_host, self.config.smtp_port, timeout=SMTP_TIMEOUT
            )
        client = smtplib.SMTP(
            self.config.smtp_host, self.config.smtp_port, timeout=SMTP_TIMEOUT
        )
        client.starttls()
        return client
