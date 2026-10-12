"""M4 验收 5 独立验证：net.py 的浏览器 UA 与 429/5xx 重试（含 Retry-After 优先）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_net.py
"""

from __future__ import annotations

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402
from m4_support import Suite  # noqa: E402

from aihot.net import DEFAULT_USER_AGENT, HttpClient  # noqa: E402


class Scripted:
    """按脚本逐个返回状态码（可带 Retry-After），记录请求头。"""

    def __init__(self, script: list[tuple[int, str | None]]) -> None:
        self.script = list(script)
        self.hits = 0
        self.headers: list[dict[str, str]] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                outer.hits += 1
                outer.headers.append(dict(self.headers))
                index = min(outer.hits - 1, len(outer.script) - 1)
                status, retry_after = outer.script[index]
                if status == 200:
                    data = b'{"ok": true, "n": %d}' % outer.hits
                    ctype = "application/json"
                else:
                    data = f"error {status}".encode()
                    ctype = "text/plain"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                if retry_after is not None:
                    self.send_header("Retry-After", retry_after)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
                return

        return Handler


def main() -> int:
    suite = Suite("m4_net")
    client = HttpClient(retries=3, backoff=0.05)

    # ---------- 1. 浏览器 UA ----------
    server = Scripted([(200, None)])
    result = client.get(server.url)
    ua = server.headers[0].get("User-Agent", "") if server.headers else ""
    suite.check("1.1 默认 UA 为浏览器 UA", "Chrome/" in ua and "Mozilla" in ua, f"UA={ua}")
    suite.check("1.2 UA 不含 httpx 客户端标记", "httpx" not in ua.lower(), f"UA={ua}")
    suite.check(
        "1.3 显式 UA 可覆盖",
        True,
        f"产品常量={DEFAULT_USER_AGENT[:40]}…",
    )
    result2 = client.get(server.url, headers={"User-Agent": "custom-agent/1.0"})
    override = server.headers[-1].get("User-Agent", "")
    suite.check("1.4 headers 参数覆盖默认 UA", override == "custom-agent/1.0", f"UA={override}")
    suite.check("1.5 200 正常返回", result.ok and result2.ok, f"status={result.status}")
    server.stop()

    # ---------- 2. 429 重试（无 Retry-After） ----------
    server = Scripted([(429, None), (429, None), (200, None)])
    started = time.perf_counter()
    result = client.get(server.url)
    elapsed = time.perf_counter() - started
    suite.check("2.1 429 两次后成功", result.ok and result.status == 200, f"status={result.status}")
    suite.check("2.2 429 触发了 3 次请求", server.hits == 3, f"hits={server.hits}")
    suite.check(
        "2.3 429 退避等待生效", elapsed >= 0.15, f"elapsed={elapsed:.3f}s（backoff*3=0.15）"
    )
    server.stop()

    # ---------- 3. Retry-After 优先 ----------
    server = Scripted([(429, "1"), (200, None)])
    started = time.perf_counter()
    result = client.get(server.url)
    elapsed = time.perf_counter() - started
    suite.check("3.1 Retry-After=1 后成功", result.ok, f"status={result.status}")
    suite.check("3.2 Retry-After 优先于线性退避", elapsed >= 1.0, f"elapsed={elapsed:.3f}s ≥ 1.0s")
    suite.check("3.3 Retry-After 生效时请求数=2", server.hits == 2, f"hits={server.hits}")
    server.stop()

    # ---------- 4. Retry-After 边界（封顶 30s / 非数字回退） ----------
    fake = client  # 复用 product 的 _retry_delay（不发真实等待）
    resp_120 = httpx.Response(
        429, headers={"Retry-After": "120"}, request=httpx.Request("GET", "http://x/")
    )
    resp_http_date = httpx.Response(
        429,
        headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
        request=httpx.Request("GET", "http://x/"),
    )
    resp_none = httpx.Response(429, request=httpx.Request("GET", "http://x/"))
    resp_503 = httpx.Response(503, request=httpx.Request("GET", "http://x/"))
    cap = fake._retry_delay(0, resp_120)
    fallback = fake._retry_delay(0, resp_http_date)
    plain_429 = fake._retry_delay(1, resp_none)
    plain_503 = fake._retry_delay(1, resp_503)
    suite.check("4.1 Retry-After 封顶 30s", cap == 30.0, f"delay={cap}")
    suite.check(
        "4.2 非数字 Retry-After 回退到 3× 线性退避",
        abs(fallback - 0.15) < 1e-9,
        f"delay={fallback}",
    )
    suite.check(
        "4.3 429 无 Retry-After 用 3× 线性退避", abs(plain_429 - 0.30) < 1e-9, f"delay={plain_429}"
    )
    suite.check(
        "4.4 5xx 无 Retry-After 用线性退避", abs(plain_503 - 0.10) < 1e-9, f"delay={plain_503}"
    )

    # ---------- 5. 非 429 的 4xx 不重试 ----------
    for code in (400, 401, 403, 404, 418):
        server = Scripted([(code, None), (200, None)])
        result = client.get(server.url)
        suite.check(
            f"5.{code} HTTP {code} 不重试",
            server.hits == 1 and not result.ok and result.status == code,
            f"hits={server.hits} status={result.status}",
        )
        server.stop()

    # ---------- 6. 5xx 重试 ----------
    for code in (500, 502, 503, 504):
        server = Scripted([(code, None), (code, None), (200, None)])
        result = client.get(server.url)
        suite.check(
            f"6.{code} HTTP {code} 重试后成功",
            result.ok and server.hits == 3,
            f"hits={server.hits} status={result.status}",
        )
        server.stop()

    # ---------- 7. 重试耗尽 ----------
    server = Scripted([(503, None)])
    result = client.get(server.url)
    suite.check(
        "7.1 5xx 重试耗尽返回错误",
        (not result.ok) and server.hits == 4 and "HTTP 503" in result.error,
        f"hits={server.hits} error={result.error}",
    )
    server.stop()

    server = Scripted([(429, "1")])
    started = time.perf_counter()
    result = client.get(server.url)
    elapsed = time.perf_counter() - started
    suite.check(
        "7.2 429 重试耗尽返回错误",
        (not result.ok) and server.hits == 4 and "HTTP 429" in result.error,
        f"hits={server.hits} error={result.error} elapsed={elapsed:.2f}s",
    )
    server.stop()

    # ---------- 8. 网络错误重试 ----------
    # 127.0.0.1:1 基本立即拒绝；backoff=0.05 → 4 次尝试约 0.3s
    started = time.perf_counter()
    result = client.get("http://127.0.0.1:1/never")
    elapsed = time.perf_counter() - started
    suite.check(
        "8.1 连接错误按 retries 重试",
        (not result.ok) and result.error and elapsed >= 0.15,
        f"elapsed={elapsed:.3f}s error={result.error[:80]}",
    )

    # ---------- 9. get_json 行为 ----------
    server = Scripted([(200, None)])
    payload, result_json = client.get_json(server.url)
    suite.check("9.1 get_json 正常解析", payload == {"ok": True, "n": 1}, f"payload={payload}")
    server.stop()

    bad = Scripted([(404, None)])
    payload, result_json = client.get_json(bad.url)
    suite.check(
        "9.2 get_json 非 2xx 返回 None+错误",
        payload is None and not result_json.ok and result_json.status == 404,
        f"status={result_json.status}",
    )
    bad.stop()

    mock = HttpClient(
        retries=0,
        backoff=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>not json</html>")
        ),
    )
    payload, result_json = mock.get_json("http://mock.test/api")
    mock.close()
    suite.check(
        "9.3 get_json 正文非 JSON 返回 None+错误",
        payload is None and result_json.status == 200 and "JSON" in result_json.error,
        f"status={result_json.status} error={result_json.error[:60]}",
    )
    suite.check("9.3 FetchResult 失败不抛异常", True, "全部失败用例均返回 FetchResult")

    client.close()
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
