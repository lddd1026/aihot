"""M3 测试脚手架自检：验证假 LLM / 假 RSS / 假企微 / 假 SMTP 可用（含 TLS 与 CONNECT 代理）。

用法: .venv\\Scripts\\python.exe tests\\independent\\m3_infra_probe.py
"""

from __future__ import annotations

import contextlib
import json
import os
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from m3_support import (  # noqa: E402
    CA_PEM,
    FakeLLM,
    FakeRSS,
    FakeSMTP,
    FakeWeCom,
    Suite,
    default_llm_generator,
    parse_email,
    rfc822,
)


def main() -> int:
    suite = Suite("m3_infra")
    # 1) 假 LLM
    with FakeLLM() as llm:
        llm.generator = default_llm_generator
        reply = httpx.post(
            f"{llm.base_url}/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "资深 AI 行业编辑"},
                    {"role": "user", "content": "[1] 来源：x | 标题：y"},
                ]
            },
            timeout=10,
        )
        payload = json.loads(reply.json()["choices"][0]["message"]["content"])
        suite.check("fake-llm", payload["items"][0]["index"] == 1, f"requests={llm.request_count}")

    # 2) 假 RSS
    with FakeRSS(
        [
            {
                "title": "Hello AI",
                "link": "https://example.com/1",
                "description": "d",
                "pubDate": rfc822(30),
            }
        ]
    ) as rss:
        feed = httpx.get(rss.url, timeout=10)
        suite.check(
            "fake-rss",
            feed.status_code == 200 and "<item>" in feed.text,
            f"bytes={len(feed.content)} hits={rss.hits}",
        )

    # 3) 假企微（HTTP 直连）
    with FakeWeCom("ok") as wecom:
        resp = httpx.post(
            wecom.local_url, json={"msgtype": "text", "text": {"content": "x"}}, timeout=10
        )
        suite.check(
            "fake-wecom-http",
            resp.json().get("errcode") == 0 and len(wecom.requests) == 1,
            f"mode={wecom.mode}",
        )

    # 4) 假企微（HTTPS + 自签 CA）
    with FakeWeCom("ok", tls=True) as wecom_tls:
        os.environ["SSL_CERT_FILE"] = str(CA_PEM)
        url = wecom_tls.url.replace("qyapi.weixin.qq.com", "127.0.0.1").replace(
            "https://", f"https://127.0.0.1:{wecom_tls.port}/"
        )
        # 保持官方前缀校验可用的形式：用 localhost 访问（SAN 含 127.0.0.1）
        url = f"https://127.0.0.1:{wecom_tls.port}/cgi-bin/webhook/send?key=fake"
        resp = httpx.post(url, json={"msgtype": "text", "text": {"content": "x"}}, timeout=10)
        suite.check(
            "fake-wecom-https",
            resp.json().get("errcode") == 0 and wecom_tls.requests[0]["tls"],
            f"tls={wecom_tls.requests[0]['tls']}",
        )
        os.environ.pop("SSL_CERT_FILE", None)

    # 5) 假企微（CONNECT 代理 + MITM：模拟 CLI 级 e2e 路径）
    with FakeWeCom("ok", proxy=True) as proxy:
        os.environ["SSL_CERT_FILE"] = str(CA_PEM)
        os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{proxy.port}"
        client = httpx.Client(proxy=None, timeout=10)  # 与产品 WeComNotifier 一致
        try:
            resp = client.post(
                proxy.url, json={"msgtype": "markdown", "markdown": {"content": "hi"}}
            )
            ok = resp.json().get("errcode") == 0 and proxy.requests and proxy.requests[0]["tls"]
        finally:
            client.close()
            os.environ.pop("SSL_CERT_FILE", None)
            os.environ.pop("HTTPS_PROXY", None)
        suite.check("fake-wecom-connect-proxy", bool(ok), f"requests={len(proxy.requests)}")

    # 6) 假 SMTP（STARTTLS + AUTH LOGIN）
    with FakeSMTP() as smtp:
        msg = EmailMessage()
        msg["Subject"] = "主题测试"
        msg["From"] = "sender@example.com"
        msg["To"] = "receiver@example.com"
        msg.set_content("plain")
        msg.add_alternative("<p>中文 HTML</p>", subtype="html")
        client = smtplib.SMTP("127.0.0.1", smtp.port, timeout=10)
        client.starttls()
        client.login("sender@example.com", "fake-auth-code")
        client.send_message(msg)
        client.quit()
        parsed = parse_email(smtp.messages[0]["data"]) if smtp.messages else None
        ok = parsed is not None and parsed["Subject"] == "主题测试" and smtp.auth_attempts[0]["tls"]
        suite.check(
            "fake-smtp-starttls",
            bool(ok),
            f"messages={len(smtp.messages)} "
            f"tls={smtp.auth_attempts[0]['tls'] if smtp.auth_attempts else None}",
        )

    # 7) 假 SMTP（SSL 465）
    with FakeSMTP(use_ssl=True) as smtp_ssl:
        client = smtplib.SMTP_SSL("127.0.0.1", smtp_ssl.port, timeout=10)
        client.login("sender@example.com", "fake-auth-code")
        client.send_message(msg)
        client.quit()
        suite.check(
            "fake-smtp-ssl", len(smtp_ssl.messages) == 1, f"messages={len(smtp_ssl.messages)}"
        )

    # 8) 假 SMTP（拒绝认证）
    with FakeSMTP(reject_auth=True) as smtp_reject:
        client = smtplib.SMTP("127.0.0.1", smtp_reject.port, timeout=10)
        client.starttls()
        try:
            client.login("sender@example.com", "fake-auth-code")
            rejected = False
        except smtplib.SMTPAuthenticationError:
            rejected = True
        finally:
            with contextlib.suppress(smtplib.SMTPException):
                client.quit()
        suite.check("fake-smtp-reject-auth", rejected, "SMTPAuthenticationError raised")

    # 9) 性能：假服务启动/停止无泄漏
    start = time.perf_counter()
    for _ in range(3):
        with FakeWeCom("ok") as w:
            httpx.get(w.local_url, timeout=5)
    suite.check(
        "fake-service-churn", time.perf_counter() - start < 5, f"{time.perf_counter() - start:.2f}s"
    )

    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
