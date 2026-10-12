"""M3 验收项 4：企业微信推送（假服务）——base64+md5、markdown 分片、失败处理、条数上限。

覆盖：
- 图片消息 base64 与 md5 正确（真实渲染出的 JPEG + 合成字节）
- markdown 分片逐条 ≤4096 字节，顺序为「先图后文」
- errcode≠0 / HTTP 502 / 连接失败 / 非 JSON / 连接被断 → NotifyError（不崩溃）
- 客户端上限守卫（>2MB 图片、>4096B markdown、空 webhook、文件缺失）
- 发送条数：真实形态日报 ≤20 条/分钟限流；极端形态实测并登记
- 图片发送失败时文字推送的行为（观测项）
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3_support import (  # noqa: E402
    FakeSMTP,
    FakeWeCom,
    Suite,
    make_digest,
    make_item,
    real_state_snapshot,
    render_image_if_available,
    workspace,
    write_config,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aihot.config import EmailConfig, WeComConfig, load_config  # noqa: E402
from aihot.delivery import NotifyError, WeComNotifier  # noqa: E402
from aihot.presentation.render_text import render_wecom_parts  # noqa: E402
from aihot.runner import deliver_digest, render_outputs  # noqa: E402

WECOM_LIMIT = 4096
RATE_LIMIT = 20


def expect_notify_error(suite: Suite, name: str, fn) -> None:
    try:
        fn()
        suite.check(name, False, "未抛 NotifyError")
    except NotifyError as exc:
        suite.check(name, True, f"NotifyError: {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        suite.check(name, False, f"抛出了非 NotifyError: {type(exc).__name__}: {exc}")


def main() -> int:
    suite = Suite("m3_wecom")
    before = real_state_snapshot()
    root = workspace("wecom")

    # ---------- 1. 图片消息 base64 + md5 ----------
    image = root / "sample.jpg"
    payload_bytes = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8 + b"\xff\xd9"
    image.write_bytes(payload_bytes)
    with FakeWeCom("ok") as wecom:
        notifier = WeComNotifier(wecom.local_url)
        notifier.send_image(image)
        notifier.close()
        request = wecom.image_requests[0]
        body = request["payload"]["image"]
        decoded = base64.b64decode(body["base64"])
        suite.check(
            "4.1 图片 base64 解码 == 文件字节",
            decoded == payload_bytes,
            f"bytes={len(decoded)} raw_len={request['raw_len']}",
        )
        suite.check(
            "4.2 图片 md5 == 文件 md5",
            body["md5"] == hashlib.md5(payload_bytes).hexdigest(),
            f"md5={body['md5']}",
        )

    # 真实渲染出的 JPEG 也走一遍
    rendered_image = render_image_if_available(
        make_digest(top_count=6, brief_count=3), root / "digests"
    )
    if rendered_image is not None:
        with FakeWeCom("ok") as wecom:
            notifier = WeComNotifier(wecom.local_url)
            notifier.send_image(rendered_image)
            notifier.close()
            body = wecom.image_requests[0]["payload"]["image"]
            suite.check(
                "4.3 真实 JPEG：base64+md5 与文件一致",
                base64.b64decode(body["base64"]) == rendered_image.read_bytes()
                and body["md5"] == hashlib.md5(rendered_image.read_bytes()).hexdigest(),
                f"size={rendered_image.stat().st_size} md5={body['md5'][:12]}…",
            )
    else:
        suite.note("跳过 4.3：Edge 不可用（图片渲染失败）")

    # ---------- 2. markdown 分片 + 顺序 ----------
    digest = make_digest(top_count=12, brief_count=12)
    parts = render_wecom_parts(digest)
    with FakeWeCom("ok") as wecom:
        notifier = WeComNotifier(wecom.local_url)
        if rendered_image is not None:
            notifier.send_image(rendered_image)
        for part in parts:
            notifier.send_markdown(part)
        notifier.close()
        order = [r["payload"]["msgtype"] for r in wecom.requests]
        sizes = [
            len(r["payload"]["markdown"]["content"].encode("utf-8"))
            for r in wecom.markdown_requests
        ]
        suite.check(
            "4.4 发送顺序：先图后文",
            order == ["image", *(["markdown"] * len(parts))],
            f"order={order}",
        )
        suite.check(
            "4.5 每条 markdown ≤4096 字节",
            all(size <= WECOM_LIMIT for size in sizes),
            f"parts={len(parts)} sizes={sizes}",
        )
        suite.check(
            "4.6 送达内容与渲染分片逐字一致",
            [r["payload"]["markdown"]["content"] for r in wecom.markdown_requests] == parts,
            f"parts={len(parts)}",
        )
        suite.check(
            "4.7 单轮条数 ≤20（限流）",
            len(wecom.requests) <= RATE_LIMIT,
            f"总条数={len(wecom.requests)}（图 1 + 文 {len(parts)}）",
        )

    # 极端形态（12 条 3800B 上限 + 长简讯标题）：实测条数
    huge_top = [
        make_item(800 + i, title=f"极端条目 {i}：" + "长标题" * 60, summary="长摘要" * 1200)
        for i in range(1, 13)
    ]
    huge_briefs = [
        make_item(850 + i, title=f"极端简讯 {i}：" + "很长" * 200, category="行业动态")
        for i in range(1, 13)
    ]
    huge = make_digest(
        top_count=0, brief_count=0, top_items=huge_top, briefs=huge_briefs, summary="极端综述" * 900
    )
    huge_parts = render_wecom_parts(huge)
    suite.check(
        "4.8 极端内容：每条 ≤4096 字节",
        all(len(part.encode("utf-8")) <= WECOM_LIMIT for part in huge_parts),
        f"parts={len(huge_parts)} max={max(len(p.encode('utf-8')) for p in huge_parts)}",
    )
    suite.check(
        "4.9 极端内容：总条数 ≤20（含图）",
        len(huge_parts) + 1 <= RATE_LIMIT,
        f"总条数={len(huge_parts) + 1}（图 1 + 文 {len(huge_parts)}）",
    )

    # ---------- 3. 客户端守卫 ----------
    with FakeWeCom("ok") as wecom:
        notifier = WeComNotifier(wecom.local_url)
        big = root / "big.jpg"
        big.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        expect_notify_error(suite, "4.10 >2MB 图片被拒", lambda: notifier.send_image(big))
        expect_notify_error(
            suite, "4.11 图片文件缺失被拒", lambda: notifier.send_image(root / "nope.jpg")
        )
        expect_notify_error(
            suite, "4.12 >4096B markdown 被拒", lambda: notifier.send_markdown("x" * 5000)
        )
        notifier.close()
    expect_notify_error(suite, "4.13 空 webhook 被拒", lambda: WeComNotifier("").send_text("x"))

    # ---------- 4. 服务端错误模式 → NotifyError ----------
    for mode, label in (
        ("errcode", "errcode≠0"),
        ("http502", "HTTP 502"),
        ("nonjson", "非 JSON 响应"),
        ("drop", "连接被服务端断开"),
    ):
        with FakeWeCom(mode) as wecom:
            notifier = WeComNotifier(wecom.local_url)
            expect_notify_error(
                suite, f"4.14 {label} → NotifyError", lambda n=notifier: n.send_markdown("测试")
            )
            notifier.close()
    # 连接失败：端口无人监听
    notifier = WeComNotifier("http://127.0.0.1:1/cgi-bin/webhook/send?key=x")
    expect_notify_error(
        suite, "4.15 连接失败 → NotifyError", lambda: notifier.send_markdown("测试")
    )
    notifier.close()

    # ---------- 5. 编排层：推送失败不崩溃 + 图片失败时文字行为 ----------
    cfg_path = write_config(root / "config.yaml", data_dir=root / "data", log_dir=root / "logs")
    base_cfg = load_config(cfg_path)

    with FakeWeCom("errcode") as bad_wecom, FakeSMTP() as smtp:
        push = dataclasses.replace(
            base_cfg.push,
            wecom=WeComConfig(enabled=True, webhook=bad_wecom.local_url),
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
        rendered = render_outputs(cfg, digest, with_image=False)
        errors = deliver_digest(cfg, digest, rendered)
        suite.check(
            "4.16 企微失败：deliver_digest 不抛异常且记录错误",
            len(errors) == 1 and "企业微信" in errors[0],
            f"errors={errors}",
        )
        suite.check(
            "4.17 企微失败不影响邮件渠道（独立降级）",
            len(smtp.messages) == 1,
            f"email_messages={len(smtp.messages)}",
        )
        suite.check(
            "4.18 企微失败后日报已存档（内容不丢）",
            bool(rendered.files.get("markdown")) and Path(rendered.files["markdown"]).exists(),
            f"files={list(rendered.files)}",
        )

    # 图片被拒（errcode）时，文字是否继续发送？（观测项）
    with FakeWeCom("fail_image") as fail_image_wecom:
        push = dataclasses.replace(
            base_cfg.push,
            wecom=WeComConfig(enabled=True, webhook=fail_image_wecom.local_url),
        )
        cfg = dataclasses.replace(base_cfg, push=push)
        rendered = render_outputs(cfg, digest, with_image=True)
        errors = deliver_digest(cfg, digest, rendered)
        image_failed = any(
            r["payload"].get("msgtype") == "image" for r in fail_image_wecom.requests
        )
        markdown_sent = len(fail_image_wecom.markdown_requests)
        suite.check(
            "4.19 图片发送被拒 → 记录错误不崩溃",
            image_failed and len(errors) == 1,
            f"errors={errors}",
        )
        suite.check(
            "4.20 观测：图片失败后文字推送是否继续（不阻断）",
            markdown_sent == len(rendered.wecom_parts),
            f"图片请求=1 文字请求={markdown_sent}/{len(rendered.wecom_parts)}"
            f"（0 表示被图片失败阻断）",
        )

    after = real_state_snapshot()
    suite.check("4.21 真实库/存档未改动", before == after, f"db={after['db']}")

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
