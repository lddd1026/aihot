"""socks5 代理复验：配置校验允许 socks5/socks5h，运行期必须干净失败（不 ImportError/堆栈）。

被测点：
- socks5 代理可用（socksio 已安装）时，HttpClient 构造不再抛 ImportError；
- 代理不可达 → check --net 给出 [ERR] 代理，fetch 干净失败，均无 traceback。
"""

from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
    ago,
    rss_xml,
    run_cli,
    source,
    write_config,
)


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    suite = Suite("socks5")
    server = LocalServer()
    server.routes["/good"] = lambda q: Payload(
        200, rss_xml([{"title": "ok", "link": "https://ok.example/1", "date": ago(hours=1)}])
    )
    server.routes["/llm/models"] = lambda q: Payload(401, b"{}")
    try:
        TEMPROOT.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="aihot-m1-socks-", dir=TEMPROOT))
        cfg = write_config(
            workspace, [source("good", server.url("/good"))], llm_base_url=server.url("/llm")
        )
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        port = closed_port()
        proxy_url = f"socks5://127.0.0.1:{port}"
        raw["proxy"] = {"enabled": True, "url": proxy_url}
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

        plain = run_cli(["check", "--config", str(cfg)], timeout=60)
        net = run_cli(["check", "--net", "--config", str(cfg)], timeout=120)
        fetch = run_cli(["fetch", "--config", str(cfg), "--dry-run"], timeout=120)
        combined = (
            f"proxy.url = {proxy_url}\n\n"
            f"=== check ===\n{plain.text}\n\n"
            f"=== check --net ===\n{net.text}\n\n"
            f"=== fetch --dry-run ===\n{fetch.text}\n"
        )
        suite.evidence("socks-probe-full.txt", combined)

        def no_import_error(result) -> bool:
            blob = result.stdout + result.stderr
            return "ImportError" not in blob and "Traceback" not in blob

        def clean(result) -> str:
            return "无" if no_import_error(result) else "有"

        suite.check(
            "socks5-check 配置校验 rc=0 且无导入错误",
            plain.rc == 0 and no_import_error(plain),
            f"rc={plain.rc}（期望 0），ImportError/Traceback={clean(plain)}",
            evidence=plain.text,
        )
        suite.check(
            "socks5-check --net 代理不可达=干净[ERR]",
            net.rc == 1 and "[ERR] 代理" in net.stdout and no_import_error(net),
            f"rc={net.rc}（期望 1），含[ERR]代理={'[ERR] 代理' in net.stdout}，"
            f"ImportError/Traceback={clean(net)}",
            evidence=net.text,
        )
        suite.check(
            "socks5-fetch 代理不可达=干净失败",
            fetch.rc == 1
            and no_import_error(fetch)
            and "socksio" not in (fetch.stdout + fetch.stderr),
            f"rc={fetch.rc}（期望 1），ImportError/Traceback={clean(fetch)}",
            evidence=fetch.text,
        )
        suite.check(
            "socks5-依赖可用（socksio 已安装）",
            _socksio_available(),
            "import socksio 成功（pyproject 已声明 socksio>=1.0,<2）",
        )
    finally:
        server.stop()
    return suite.finish()


def _socksio_available() -> bool:
    try:
        import socksio  # noqa: F401
    except ImportError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
