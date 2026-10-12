"""M5 声明 1 + 2 独立验收（第 2 轮复验版）：代理不可用不拦截 + 预检直连（忽略环境代理）。

背景变化：第 1 轮用「环境 HTTPS_PROXY → 本地假企微 MITM」截获 qyapi 探测；主控 P1 修复后
企微/LLM 探测显式 `trust_env=False`，该截获方式**已被淘汰**（再拦截会直连真实域名，违反
本轮"只连 localhost"约束）。因此本脚本改为：
- A：配置代理 = 坏端口；环境 HTTPS_PROXY / ALL_PROXY 也 = 坏端口；LLM 指向本地假 LLM
  （wecom 在配置里关闭，避免直连真实域名）。期望 `check --quick` rc=0、代理 [WARN] 降级、
  LLM [OK ]、无 [ERR] —— 同时证明**环境代理被忽略**（否则 LLM 会经死代理失败）。
- B（反证）：假 LLM 关闭后 rc=1、LLM [ERR]（不是"永远成功"）。
企微探测的直连验证见 `m5r_round2_proxy.py`（用本地 webhook 的 Config 副本，进程内调用）。

跑法: .venv\\Scripts\\python.exe tests\\independent\\m5r_preflight.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_support as m3  # noqa: E402
import m5r_support as m5  # noqa: E402

BAD_PROXY = f"http://127.0.0.1:{m5.BAD_PORT}"
PROBE_LINE = re.compile(r"^\s*(\[(?:OK |WARN|ERR)\])\s+([^:]+):\s(.*)$")


def probe_marks(output: str) -> dict[str, str]:
    """解析预检段落：{"代理": "[WARN]", "LLM API": "[OK ]", ...}。"""
    marks: dict[str, str] = {}
    for line in output.splitlines():
        found = PROBE_LINE.match(line)
        if found and found.group(2).strip() not in marks:
            marks[found.group(2).strip()] = found.group(1)
    return marks


def probe_detail(output: str, name: str) -> str:
    for line in output.splitlines():
        found = PROBE_LINE.match(line)
        if found and found.group(2).strip() == name:
            return line.strip()
    return "（未找到）"


def dead_env_proxies() -> dict[str, str]:
    """由子进程继承的坏端口环境代理（用于证明它们被忽略）。"""
    bad = f"http://127.0.0.1:{m5.BAD_PORT}"
    return {"HTTPS_PROXY": bad, "HTTP_PROXY": bad, "ALL_PROXY": bad}


def main() -> int:
    suite = m3.Suite("m5r_preflight")
    m5.ensure_certs()
    before = m3.real_state_snapshot()
    ws = m3.workspace("m5r-preflight")
    cfg = ws / "config.yaml"

    # ---------- A：配置代理 + 环境代理全部坏端口；LLM 本地假服务 ----------
    with m5.m4.FakeLLMProbe() as llm:
        raw = m5.build_raw(
            ws,
            proxy_url=BAD_PROXY,
            llm_base_url=llm.base_url,
            wecom_enabled=False,  # 避免直连真实域名（企微探测另在 round2 脚本进程内验证）
            source_url="http://127.0.0.1:1/feed.xml",
        )
        m5.m4.dump_config(cfg, raw)
        env = m5.clean_proxy_env(dead_env_proxies())
        result = m3.run_cli(["check", "--quick", "--config", str(cfg)], env_extra=env, timeout=120)
        suite.evidence("1-check-quick-proxy-down.txt", result.text)
        out = result.stdout
        marks = probe_marks(out)
        suite.check(
            "1.1 代理指向坏端口时 check --quick 退出码=0（不拦截运行）",
            result.rc == 0,
            f"rc={result.rc}",
            evidence=result.text,
        )
        suite.check(
            "1.2 代理项是 [WARN]（可降级），提示含「降级」",
            marks.get("代理") == "[WARN]" and "降级" in probe_detail(out, "代理"),
            probe_detail(out, "代理"),
        )
        suite.check(
            "1.3 输出明确说明可降级失败不阻断",
            "可降级失败" in out,
            next((line.strip() for line in out.splitlines() if "可降级失败" in line), "（缺失）"),
        )
        suite.check(
            "1.5 LLM API = [OK ]（环境代理为坏端口时仍直连成功）",
            marks.get("LLM API") == "[OK ]",
            probe_detail(out, "LLM API"),
        )
        suite.check(
            "1.5b LLM 探测确实到达本地假 LLM（/models 命中）",
            llm.models_probes >= 1,
            f"models_probes={llm.models_probes}",
        )
        suite.check(
            "1.5c 无任何 [ERR]（环境代理未被继承）",
            "[ERR]" not in out,
            "无 ERR 行" if "[ERR]" not in out else probe_detail(out, "LLM API"),
        )

    # ---------- B：假 LLM 关闭 → 必须失败（反证） ----------
    env = m5.clean_proxy_env(dead_env_proxies())
    result = m3.run_cli(["check", "--quick", "--config", str(cfg)], env_extra=env, timeout=120)
    suite.evidence("2-check-quick-llm-down.txt", result.text)
    out = result.stdout
    marks = probe_marks(out)
    suite.check(
        "2.3 假服务关闭后 check --quick 退出码=1（不是假装成功）",
        result.rc == 1,
        f"rc={result.rc}",
        evidence=result.text,
    )
    suite.check(
        "2.5 假服务关闭后 LLM 项 [ERR]",
        marks.get("LLM API") == "[ERR]",
        probe_detail(out, "LLM API"),
    )

    suite.check(
        "1.6 真实库/存档未被本套件改动",
        before == m3.real_state_snapshot(),
        "真实库快照一致",
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
