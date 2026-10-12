"""企业微信机器人推送：图片消息 + markdown（渲染阶段已分片）。"""

from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

import httpx

from aihot.delivery.base import NotifyError

logger = logging.getLogger("aihot.delivery.wecom")

WECOM_MAX_IMAGE_BYTES = 2 * 1024 * 1024
WECOM_MAX_MARKDOWN_BYTES = 4096
SEND_TIMEOUT = 20.0


class WeComNotifier:
    """企业微信群机器人。"""

    name = "wecom"

    def __init__(
        self,
        webhook: str,
        *,
        proxy: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.webhook = webhook
        # trust_env=False：推送一律按显式配置连接，不被 HTTP(S)_PROXY 等环境变量暗中接管
        self._client = http or httpx.Client(
            proxy=proxy, timeout=SEND_TIMEOUT, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    # ---------- 对外 ----------

    def send_text(self, content: str) -> None:
        self._post({"msgtype": "text", "text": {"content": content}}, "文本消息")

    def send_markdown(self, content: str) -> None:
        size = len(content.encode("utf-8"))
        if size > WECOM_MAX_MARKDOWN_BYTES:
            raise NotifyError(
                f"markdown 超过 {WECOM_MAX_MARKDOWN_BYTES} 字节（{size}），应在渲染阶段分片"
            )
        self._post({"msgtype": "markdown", "markdown": {"content": content}}, "markdown 消息")

    def send_image(self, path: Path | str) -> None:
        image_path = Path(path)
        try:
            data = image_path.read_bytes()
        except OSError as exc:
            raise NotifyError(f"图片读取失败: {exc}") from exc
        if not data:
            raise NotifyError(f"图片为空: {image_path}")
        if len(data) > WECOM_MAX_IMAGE_BYTES:
            limit_mb = WECOM_MAX_IMAGE_BYTES // 1024 // 1024
            raise NotifyError(
                f"图片超过 {limit_mb}MB（{len(data)} 字节）: {image_path.name}"
            )
        payload = {
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(data).decode("ascii"),
                "md5": hashlib.md5(data).hexdigest(),  # noqa: S324 - 企微接口要求 md5 校验值
            },
        }
        self._post(payload, f"图片消息（{image_path.name}）")

    # ---------- 内部 ----------

    def _post(self, payload: dict, what: str) -> None:
        if not self.webhook:
            raise NotifyError("企业微信 webhook 未配置")
        try:
            response = self._client.post(self.webhook, json=payload)
        except httpx.HTTPError as exc:
            raise NotifyError(f"{what}发送失败: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise NotifyError(f"{what}发送失败: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise NotifyError(f"{what}发送失败: 响应不是合法 JSON") from exc
        errcode = int(body.get("errcode") or 0)
        if errcode != 0:
            raise NotifyError(f"{what}发送失败: errcode={errcode} errmsg={body.get('errmsg')}")
        logger.info("%s已发送（请求体 %d 字节）", what, len(response.request.content or b""))
