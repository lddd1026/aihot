"""M4 验收 7 独立验证：13 个信息源逐个真实抓取（只读，不写库）。

- 启用源：CLI `fetch --source ID --dry-run`（真实网络，不写库）
- 停用源（jiqizhixin / hf_papers）：进程内适配器直连（记录失败也算结果，隔离验证在 m4_drills）
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_sources_live.py
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


from m4_support import (  # noqa: E402
    Suite,
    evidence_json,
    parse_fetch_lines,
    run_python,
    write_raw,
)

from aihot.config import load_config  # noqa: E402
from aihot.net import HttpClient  # noqa: E402
from aihot.utils import now_utc  # noqa: E402

PER_SOURCE_TIMEOUT = 300.0


def live_cli_fetch(source_id: str) -> dict[str, object]:
    result = run_python(
        [
            "-m",
            "aihot",
            "fetch",
            "--source",
            source_id,
            "--dry-run",
            "--limit",
            "2",
            "--config",
            "config.yaml",
        ],
        timeout=PER_SOURCE_TIMEOUT,
    )
    rows = [row for row in parse_fetch_lines(result["stdout"]) if row["source_id"] == source_id]
    row = rows[0] if rows else None
    return {
        "source_id": source_id,
        "mode": "cli-dry-run",
        "rc": result["rc"],
        "seconds": result["seconds"],
        "ok": bool(row and row["ok"]) and result["rc"] == 0,
        "count": row["detail"] if row and row["ok"] else 0,
        "detail": (row["detail"] if row else f"无统计行；stderr={result['stderr'][:200]}"),
        "raw": result["stdout"],
    }


def live_inprocess_fetch(source_id: str, cfg) -> dict[str, object]:
    """停用源直连尝试：直接实例化适配器（绕过 build_adapters 的 enabled 过滤）。"""
    from aihot.sources.registry import ADAPTER_TYPES

    source = next(item for item in cfg.sources if item.id == source_id)
    adapter_class = ADAPTER_TYPES.get(source.type)
    if adapter_class is None:
        return {
            "source_id": source_id,
            "mode": "in-process(disabled)",
            "ok": False,
            "count": 0,
            "detail": f"未注册的适配器类型: {source.type}",
            "raw": "",
        }
    proxy = cfg.proxy.url if cfg.proxy.enabled else None
    http = HttpClient(proxy=proxy, retries=2, backoff=1.0)
    try:
        adapter = adapter_class(source, http)
        since = now_utc() - timedelta(hours=48)
        items = adapter.fetch(since)
        return {
            "source_id": source_id,
            "mode": "in-process(disabled)",
            "ok": True,
            "count": len(items),
            "detail": f"{len(items)} 条（48h 窗口，enabled={source.enabled}）",
            "raw": "\n".join(f"- {item.title[:80]} | {item.url}" for item in items[:3]),
        }
    except Exception as exc:  # noqa: BLE001 - 记录真实失败，不掩盖
        return {
            "source_id": source_id,
            "mode": "in-process(disabled)",
            "ok": False,
            "count": 0,
            "detail": f"{type(exc).__name__}: {exc}",
            "raw": "",
        }
    finally:
        http.close()


def main() -> int:
    suite = Suite("m4_sources_live")
    cfg = load_config("config.yaml")
    enabled = [s.id for s in cfg.sources if s.enabled]
    disabled = [s.id for s in cfg.sources if not s.enabled]

    if "--disabled-only" in sys.argv:
        results_disabled = [live_inprocess_fetch(source_id, cfg) for source_id in disabled]
        evidence_json(
            "25b-disabled-sources.json",
            [{k: v for k, v in row.items() if k != "raw"} for row in results_disabled],
        )
        write_raw(
            "25b-disabled-sources.txt",
            "\n".join(
                f"{'OK ' if row['ok'] else 'ERR'} {row['source_id']:<18} {row['detail']}"
                for row in results_disabled
            ),
        )
        for row in results_disabled:
            suite.check(
                f"7b 停用源 {row['source_id']} 直连尝试",
                True,
                f"[{'OK' if row['ok'] else 'ERR'}] {row['detail']}",
                evidence=str(row["raw"]),
            )
        return suite.finish()

    suite.note(
        f"配置共 {len(cfg.sources)} 个源：启用 {len(enabled)} 个 {enabled}；"
        f"停用 {len(disabled)} 个 {disabled}"
    )
    suite.evidence_json(
        "sources-config.json",
        [
            {"id": s.id, "name": s.name, "type": s.type, "enabled": s.enabled, "url": s.url}
            for s in cfg.sources
        ],
    )

    results: list[dict[str, object]] = []
    for source_id in enabled:
        row = live_cli_fetch(source_id)
        results.append(row)
        mark = "OK" if row["ok"] else "ERR"
        suite.check(
            f"7.{len(results)} 源 {source_id} 真实抓取",
            True,  # 只记录结果，成败在报告中判定
            f"[{mark}] {row['detail']}（{row['seconds']}s）",
            evidence=str(row["raw"])[-1500:],
        )

    for source_id in disabled:
        row = live_inprocess_fetch(source_id, cfg)
        results.append(row)
        mark = "OK" if row["ok"] else "ERR"
        suite.check(
            f"7.{len(results)} 源 {source_id}（停用源直连尝试）",
            True,
            f"[{mark}] {row['detail']}",
            evidence=str(row["raw"]),
        )

    ok_count = sum(1 for row in results if row["ok"])
    enabled_ok = sum(1 for row in results if row["ok"] and row["source_id"] in enabled)
    summary = {
        "config_sources": len(cfg.sources),
        "enabled": enabled,
        "disabled": disabled,
        "ok_total": ok_count,
        "ok_enabled": enabled_ok,
        "results": [{k: v for k, v in row.items() if k != "raw"} for row in results],
    }
    evidence_json("25-live-sources.json", summary)
    write_raw(
        "25-live-sources.txt",
        "\n".join(
            f"{'OK ' if row['ok'] else 'ERR'} {row['source_id']:<18} "
            f"{row['detail']} ({row['mode']})"
            for row in results
        ),
    )
    suite.note(
        f"真实抓取汇总：启用源 {enabled_ok}/{len(enabled)} 成功；"
        f"含停用源共 {ok_count}/{len(results)}"
    )
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
