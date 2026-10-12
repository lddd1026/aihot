"""M6 第 2 轮补充：健康检查的**最坏耗时**观察（P2-3 多探测点引入的成本）。

场景：代理端口 TCP 可连，但"节点挂起"（接受连接后对 CONNECT 不作任何响应）。
实测（timeout=1.0s、2 个探测点）：每个探测点约花 2 × timeout（连接/读取阶段各吃一次），
总耗时 ≈ 探测点数 × 2 × timeout；用 1.0s 测量可避免真机 2 × 5s × 2 的等待，
同时证明"超时按探测点生效、且整体有界"。

结论只作为 P3 观察（不影响放行）：默认 5s 超时下，节点挂起时健康检查最坏约 20s
（原单点探测约 10s）；这是预检/采集一次的固定开销，不会让运行失败。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m6r_health_latency.py
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import time
from pathlib import Path
from threading import Thread

os.environ.setdefault("AIHOT_M6R_EVIDENCE", "m6r/round2")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m6r_support as m6  # noqa: E402

import aihot.net as net  # noqa: E402


class HangingProxy:
    """接受 TCP 连接但从不响应（模拟"端口开着、节点挂起"）。"""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = int(self._sock.getsockname()[1])
        self.connections: list[socket.socket] = []
        self._stop = False
        Thread(target=self._loop, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> HangingProxy:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections.append(conn)

    def stop(self) -> None:
        self._stop = True
        for conn in self.connections:
            with contextlib.suppress(OSError):
                conn.close()
        self._sock.close()


def main() -> int:
    suite = m3.Suite("m6r_health_latency")
    before = m3.real_state_snapshot()
    m6.clean_proxy_env()

    probes = len(net.PROXY_HEALTH_PROBES)
    with HangingProxy() as proxy:
        started = time.perf_counter()
        healthy = net.proxy_healthy(proxy.url, timeout=1.0)
        elapsed = time.perf_counter() - started
        suite.evidence(
            "1-hanging-node-latency.txt",
            f"探测点数量={probes}\nhealthy={healthy}\ntimeout=1.0s\nelapsed={elapsed:.2f}s\n"
            f"理论最坏=探测点数 × timeout={probes * 1.0:.1f}s\n"
            f"接受的 TCP 连接数={len(proxy.connections)}",
        )
        suite.check(
            "R2-3.10 节点挂起：健康检查 False；耗时 ≥探测点数×timeout（都试过）且"
            " ≤探测点数×2×timeout+1.5（整体有界）",
            healthy is False and probes * 1.5 <= elapsed <= probes * 2.0 * 1.0 + 1.5,
            f"healthy={healthy}；elapsed={elapsed:.2f}s；探测点数={probes}；"
            f"单点实测≈{elapsed / probes:.2f}s（timeout=1.0s）",
        )
        worst = probes * 2 * net.PROXY_HEALTH_TIMEOUT
        single = 2 * net.PROXY_HEALTH_TIMEOUT
        suite.note(
            f"P3 观察：默认 PROXY_HEALTH_TIMEOUT={net.PROXY_HEALTH_TIMEOUT:.0f}s 时，节点挂起"
            f"（接受连接但不响应）会让健康检查最坏耗时 ≈{worst:.0f}s（实测单点≈2×timeout、"
            f"{probes} 个探测点；原单点探测 ≈{single:.0f}s）。属预检/采集一次的固定开销，"
            "不影响正确性；若在意可降低超时或减少探测点。"
        )

    m6.clean_proxy_env()
    suite.check(
        "R2-3.11 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
