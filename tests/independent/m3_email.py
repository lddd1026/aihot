"""M3 验收项 5：邮件推送（假 SMTP）——主题/收件人/HTML 正确，attach_image 控制内嵌图 cid。

覆盖：
- STARTTLS（587 路径）与 SMTP_SSL（465 路径）两条连接方式
- 主题/发件人/收件人正确；中文主题 RFC2047 解码正确
- HTML 正文与渲染结果一致（含综述/链接/统计）
- attach_image=true → multipart/related 内嵌图，Content-ID == HTML 中 cid，字节与文件一致
- attach_image=false → 无 image/* 部件
- 认证失败/收件人为空 → NotifyError（不崩溃）
- 编排层 deliver_digest 走真实 SMTP 连接（含内嵌图）
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    FakeSMTP,
    Suite,
    make_digest,
    parse_email,
    real_state_snapshot,
    render_image_if_available,
    workspace,
    write_config,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.config import EmailConfig, WeComConfig, load_config  # noqa: E402
from aihot.delivery import EmailNotifier, NotifyError  # noqa: E402
from aihot.runner import deliver_digest, render_outputs  # noqa: E402


def email_parts(message) -> dict[str, object]:
    """拆解邮件结构：纯文本/HTML/内嵌图。"""
    result: dict[str, object] = {"html": "", "plain": "", "images": [], "content_types": []}
    images = result["images"]
    content_types = result["content_types"]
    for part in message.walk():
        ctype = part.get_content_type()
        content_types.append(ctype)
        if ctype == "text/html":
            result["html"] = part.get_content()
        elif ctype == "text/plain":
            result["plain"] = part.get_content()
        elif ctype.startswith("image/"):
            images.append(
                {
                    "content_type": ctype,
                    "cid": (part.get("Content-ID") or "").strip("<>"),
                    "filename": part.get_filename(),
                    "bytes": part.get_payload(decode=True),
                }
            )
    return result


def main() -> int:
    suite = Suite("m3_email")
    before = real_state_snapshot()
    root = workspace("email")
    digest = make_digest(top_count=12, brief_count=12)
    image = render_image_if_available(make_digest(top_count=8, brief_count=4), root / "digests")
    if image is None:
        suite.check("5.0 需要一张渲染出的长图", False, "Edge 不可用，无法渲染")
        return suite.finish()
    image_bytes = image.read_bytes()

    # ---------- 1. 纯文字邮件（attach_image=false） ----------
    with FakeSMTP() as smtp:
        notifier = EmailNotifier(
            EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp.port,
                use_ssl=False,
                username="sender@example.com",
                password="fake-auth-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com",),
                attach_image=False,
            )
        )
        notifier.send("AI 热点日报 · 2026-09-24", "<p>正文中文</p>")
        message = parse_email(smtp.messages[0]["data"])
        parts = email_parts(message)
        suite.check(
            "5.1 主题/发件人/收件人正确",
            message["Subject"] == "AI 热点日报 · 2026-09-24"
            and message["From"] == "sender@example.com"
            and message["To"] == "receiver@example.com",
            f"subject={message['Subject']} to={message['To']}",
        )
        suite.check(
            "5.2 STARTTLS 连接 + 登录成功",
            smtp.auth_attempts
            and smtp.auth_attempts[0]["tls"]
            and smtp.auth_attempts[0]["user"] == "sender@example.com",
            f"auth={smtp.auth_attempts}",
        )
        suite.check(
            "5.3 无内嵌图（attach_image=false）",
            parts["images"] == [] and "image/" not in " ".join(parts["content_types"]),
            f"content_types={parts['content_types']}",
        )
        suite.check(
            "5.4 含纯文本兜底 + HTML 正文",
            "HTML" in str(parts["plain"]) and str(parts["html"]).strip() == "<p>正文中文</p>",
            f"plain={str(parts['plain'])[:40]!r}",
        )

    # ---------- 2. 内嵌图邮件（attach_image=true） ----------
    with FakeSMTP() as smtp:
        notifier = EmailNotifier(
            EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp.port,
                use_ssl=False,
                username="sender@example.com",
                password="fake-auth-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com",),
                attach_image=True,
            )
        )
        html = '<p>日报</p><img src="cid:digest-image-1">'
        notifier.send("主题", html, images=[image])
        message = parse_email(smtp.messages[0]["data"])
        parts = email_parts(message)
        images = parts["images"]
        suite.check(
            "5.5 multipart/related 内嵌图 cid 匹配",
            len(images) == 1
            and images[0]["cid"] == "digest-image-1"
            and f"cid:{images[0]['cid']}" in str(parts["html"]),
            f"images={[(i['content_type'], i['cid']) for i in images]}",
        )
        suite.check(
            "5.6 内嵌图字节与文件一致",
            images[0]["bytes"] == image_bytes and len(images[0]["bytes"]) == len(image_bytes),
            f"sent={len(images[0]['bytes'])} file={len(image_bytes)}",
        )
        suite.check(
            "5.7 内嵌图 Content-Type 为 image/jpeg",
            images[0]["content_type"] == "image/jpeg" and images[0]["filename"] == image.name,
            f"ctype={images[0]['content_type']} filename={images[0]['filename']}",
        )

    # ---------- 3. SSL 直连（465 路径） ----------
    with FakeSMTP(use_ssl=True) as smtp_ssl:
        notifier = EmailNotifier(
            EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp_ssl.port,
                use_ssl=True,
                username="sender@example.com",
                password="fake-auth-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com", "second@example.com"),
                attach_image=False,
            )
        )
        notifier.send("SSL 主题", "<p>ssl</p>")
        message = parse_email(smtp_ssl.messages[0]["data"])
        suite.check(
            "5.8 SMTP_SSL 路径可用 + 多收件人",
            len(smtp_ssl.messages) == 1
            and message["To"] == "receiver@example.com, second@example.com"
            and smtp_ssl.messages[0]["tls"] is True,
            f"to={message['To']} tls={smtp_ssl.messages[0]['tls']}",
        )

    # ---------- 4. 失败路径 ----------
    with FakeSMTP(reject_auth=True) as smtp_reject:
        notifier = EmailNotifier(
            EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp_reject.port,
                use_ssl=False,
                username="sender@example.com",
                password="wrong-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com",),
            )
        )
        try:
            notifier.send("s", "<p>x</p>")
            suite.check("5.9 认证失败 → NotifyError", False, "未抛错")
        except NotifyError as exc:
            suite.check("5.9 认证失败 → NotifyError", True, str(exc)[:80])
        except Exception as exc:  # noqa: BLE001
            suite.check("5.9 认证失败 → NotifyError", False, f"{type(exc).__name__}: {exc}")

    notifier = EmailNotifier(
        EmailConfig(
            enabled=True,
            smtp_host="127.0.0.1",
            smtp_port=1,
            use_ssl=False,
            username="sender@example.com",
            password="fake-auth-code",
            from_addr="sender@example.com",
            to_addrs=(),
        )
    )
    try:
        notifier.send("s", "<p>x</p>")
        suite.check("5.10 收件人为空 → NotifyError", False, "未抛错")
    except NotifyError as exc:
        suite.check("5.10 收件人为空 → NotifyError", True, str(exc))

    notifier = EmailNotifier(
        EmailConfig(
            enabled=True,
            smtp_host="127.0.0.1",
            smtp_port=1,  # 无人监听
            use_ssl=False,
            username="sender@example.com",
            password="fake-auth-code",
            from_addr="sender@example.com",
            to_addrs=("receiver@example.com",),
        )
    )
    try:
        notifier.send("s", "<p>x</p>")
        suite.check("5.11 连接失败 → NotifyError", False, "未抛错")
    except NotifyError as exc:
        suite.check("5.11 连接失败 → NotifyError", True, str(exc)[:80])

    # ---------- 5. 编排层端到端（真实 SMTP 连接 + 内嵌图） ----------
    cfg_path = write_config(root / "config.yaml", data_dir=root / "data", log_dir=root / "logs")
    base_cfg = load_config(cfg_path)
    with FakeSMTP() as smtp:
        push = dataclasses.replace(
            base_cfg.push,
            wecom=WeComConfig(enabled=False, webhook=""),
            email=EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp.port,
                use_ssl=False,
                username="sender@example.com",
                password="fake-auth-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com",),
                attach_image=True,
            ),
        )
        cfg = dataclasses.replace(base_cfg, push=push)
        rendered = render_outputs(cfg, digest, with_image=True)
        errors = deliver_digest(cfg, digest, rendered)
        message = parse_email(smtp.messages[0]["data"]) if smtp.messages else None
        parts = (
            email_parts(message)
            if message is not None
            else {"images": [], "html": "", "content_types": []}
        )
        suite.check(
            "5.12 编排层：deliver_digest 邮件发送成功（0 错误）",
            errors == [] and message is not None,
            f"errors={errors}",
        )
        from aihot.presentation.compose import item_links  # noqa: E402

        expected_links = [link["url"] for item in digest.top_items for link in item_links(item)]
        suite.check(
            "5.13 编排层：主题含日报日期 + HTML 含综述与全部 Top 链接",
            message is not None
            and message["Subject"] == "AI 热点日报 · 2026-09-24"
            and digest.summary in str(parts["html"])
            and all(url in str(parts["html"]) for url in expected_links),
            f"subject={message['Subject'] if message else None} links={len(expected_links)} "
            f"missing={[u for u in expected_links if u not in str(parts['html'])]}",
        )
        suite.check(
            "5.14 编排层：attach_image=true → 内嵌图 cid 与 HTML 一致",
            len(parts["images"]) == 1
            and parts["images"][0]["cid"] == "digest-image-1"
            and "cid:digest-image-1" in str(parts["html"]),
            f"images={len(parts['images'])} "
            f"cid={parts['images'][0]['cid'] if parts['images'] else None}",
        )

    # attach_image=false 时不应有内嵌图（编排层）
    with FakeSMTP() as smtp:
        push = dataclasses.replace(
            base_cfg.push,
            wecom=WeComConfig(enabled=False, webhook=""),
            email=EmailConfig(
                enabled=True,
                smtp_host="127.0.0.1",
                smtp_port=smtp.port,
                use_ssl=False,
                username="sender@example.com",
                password="fake-auth-code",
                from_addr="sender@example.com",
                to_addrs=("receiver@example.com",),
                attach_image=False,
            ),
        )
        cfg = dataclasses.replace(base_cfg, push=push)
        rendered = render_outputs(cfg, digest, with_image=True)
        deliver_digest(cfg, digest, rendered)
        message = parse_email(smtp.messages[0]["data"])
        parts = email_parts(message)
        suite.check(
            "5.15 编排层：attach_image=false → 无内嵌图",
            parts["images"] == [] and "cid:" not in str(parts["html"]),
            f"content_types={parts['content_types']}",
        )

    after = real_state_snapshot()
    suite.check("5.16 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
