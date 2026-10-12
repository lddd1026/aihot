"""M1 核心验收（黑盒 CLI + SQLite 观察）：验收项 1/2/3/5/6/7/8。

全部使用本地模拟源与临时目录，不访问真实网络、不触碰真实数据库。
"""

from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    PROJECT,
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
    ago,
    db_counts,
    db_query,
    rss_xml,
    run_cli,
    source,
    table_names,
    titles,
    write_config,
)

REAL_DB = PROJECT / "data" / "aihot.db"


def fresh_dir(name: str) -> Path:
    TEMPROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"aihot-m1-{name}-", dir=TEMPROOT))


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:  # noqa: C901 - 验收脚本，按场景线性展开
    suite = Suite("acceptance")
    server = LocalServer()
    real_before = db_counts(REAL_DB)
    try:
        _register_routes(server)
        _t1_failure_isolation(suite, server)
        _t2_idempotency(suite, server)
        _t3_window(suite, server)
        _t4_dry_run(suite, server)
        filter_db = _t5_source_filter(suite, server)
        _t6_store_correctness(suite, filter_db)
        _t7_exit_codes(suite, server)
        _t8_extra_observations(suite, server)
    finally:
        server.stop()
    real_after = db_counts(REAL_DB)
    suite.check(
        "guard:real-db-untouched",
        real_before == real_after,
        f"真实库快照前={real_before} 后={real_after}",
    )
    return suite.finish()


# --------------------------------------------------------------------------- 路由


FEED_G1 = rss_xml(
    [
        {"title": "g1-a", "link": "https://good.example/a", "date": ago(minutes=10)},
        {"title": "g1-b", "link": "https://good.example/b", "date": ago(minutes=30)},
    ]
)
FEED_G2 = rss_xml(
    [{"title": "g2-a", "link": "https://good.example/c", "date": ago(hours=2)}]
)


def _register_routes(server: LocalServer) -> None:
    server.routes["/good1"] = lambda query: Payload(200, FEED_G1)
    server.routes["/good2"] = lambda query: Payload(200, FEED_G2)
    server.routes["/bad500"] = lambda query: Payload(500, b"<html>server error</html>")
    server.routes["/empty"] = lambda query: Payload(200, rss_xml([]))

    def windowed(query: dict[str, list[str]]) -> Payload:
        entries = [
            {"title": "w-30m", "link": "https://w.example/30m", "date": ago(minutes=30)},
            {"title": "w-59m", "link": "https://w.example/59m", "date": ago(minutes=59)},
            {"title": "w-61m", "link": "https://w.example/61m", "date": ago(minutes=61)},
            {"title": "w-3h", "link": "https://w.example/3h", "date": ago(hours=3)},
            {"title": "w-25h", "link": "https://w.example/25h", "date": ago(hours=25)},
            {"title": "w-200h", "link": "https://w.example/200h", "date": ago(hours=200)},
            {"title": "w-nodate", "link": "https://w.example/nodate"},
            {"title": "w-baddate", "link": "https://w.example/baddate", "raw_date": "not a date"},
        ]
        return Payload(200, rss_xml(entries))

    server.routes["/windowed"] = windowed
    server.routes["/filter1"] = lambda query: Payload(
        200,
        rss_xml(
            [
                {"title": "shared", "link": "https://shared.example/article", "date": ago(hours=1)},
                {"title": "f1-only", "link": "https://s1.example/only", "date": ago(hours=1)},
            ]
        ),
    )
    server.routes["/filter2"] = lambda query: Payload(
        200,
        rss_xml(
            [
                {"title": "shared", "link": "https://shared.example/article", "date": ago(hours=1)},
                {"title": "f2-only", "link": "https://s2.example/only", "date": ago(hours=1)},
            ]
        ),
    )
    server.routes["/filter3"] = lambda query: Payload(
        200,
        rss_xml(
            [{"title": "f3-only", "link": "https://s3.example/only", "date": ago(hours=1)}]
        ),
    )


# --------------------------------------------------------------------------- 1 单源失败隔离


def _t1_failure_isolation(suite: Suite, server: LocalServer) -> None:
    workspace = fresh_dir("t1")
    cfg = write_config(
        workspace,
        [
            source("good1", server.url("/good1")),
            source("good2", server.url("/good2")),
            source("bad500", server.url("/bad500")),
        ],
    )
    result = run_cli(["fetch", "--config", str(cfg), "--limit", "5"])
    stdout = result.stdout
    items = db_counts(workspace / "data" / "aihot.db")
    runs = db_query(
        workspace / "data" / "aihot.db", "SELECT * FROM runs ORDER BY rowid DESC LIMIT 1"
    )
    status = runs[0]["status"] if runs else "<none>"
    stats = json.loads(runs[0]["stage_stats"]) if runs else {}

    ok = (
        result.rc == 0
        and "[OK ] good1" in stdout
        and "[OK ] good2" in stdout
        and "[ERR] bad500" in stdout
        and items["items"] == 3
        and status == "partial"
        and stats.get("sources_ok") == 2
        and stats.get("sources_failed") == 1
    )
    suite.check(
        "1-单源失败隔离",
        ok,
        f"rc={result.rc} 库内={items['items']} runs.status={status} "
        f"sources_ok/failed={stats.get('sources_ok')}/{stats.get('sources_failed')}",
        evidence=result.text,
    )


# --------------------------------------------------------------------------- 2 幂等


def _t2_idempotency(suite: Suite, server: LocalServer) -> None:
    workspace = fresh_dir("t2")
    cfg = write_config(
        workspace,
        [source("good1", server.url("/good1")), source("good2", server.url("/good2"))],
        window_hours=24,
    )
    db = workspace / "data" / "aihot.db"
    saved: list[int] = []
    totals: list[int] = []
    transcript: list[str] = []
    for index in range(3):
        result = run_cli(["fetch", "--config", str(cfg), "--limit", "0"])
        transcript.append(f"--- run {index + 1} (exit={result.rc}) ---\n{result.stdout}")
        match_saved = re.search(r"新增 (\d+) 条", result.stdout)
        match_total = re.search(r"库内共 (\d+) 条", result.stdout)
        saved.append(int(match_saved.group(1)) if match_saved else -1)
        totals.append(int(match_total.group(1)) if match_total else -1)
    counts = db_counts(db)
    monotonic = all(b >= a for a, b in zip(totals, totals[1:], strict=False))
    ok = (
        saved == [3, 0, 0]
        and totals == [3, 3, 3]
        and monotonic
        and counts["items"] == counts["unique_keys"] == 3
        and counts["runs"] == 3
    )
    suite.check(
        "2-连跑3次幂等",
        ok,
        f"新增序列={saved} 库内序列={totals} 唯一键={counts['unique_keys']} runs={counts['runs']}",
        evidence="\n".join(transcript),
    )


# --------------------------------------------------------------------------- 3 时间窗口


def _t3_window(suite: Suite, server: LocalServer) -> None:
    # 契约：--window-hours 合法范围 1~168（超范围 rc=2，见 8f）；168 应排除 200h 条目
    expected = {
        1: {"w-30m", "w-59m", "w-nodate", "w-baddate"},
        24: {"w-30m", "w-59m", "w-61m", "w-3h", "w-nodate", "w-baddate"},
        168: {
            "w-30m",
            "w-59m",
            "w-61m",
            "w-3h",
            "w-25h",
            "w-nodate",
            "w-baddate",
        },
    }
    transcript: list[str] = []
    all_ok = True
    details: list[str] = []
    for hours, want in expected.items():
        workspace = fresh_dir(f"t3-{hours}")
        cfg = write_config(workspace, [source("windowed", server.url("/windowed"))])
        result = run_cli(
            ["fetch", "--config", str(cfg), "--window-hours", str(hours), "--limit", "0"]
        )
        got = set(titles(workspace / "data" / "aihot.db"))
        good = result.rc == 0 and got == want
        all_ok = all_ok and good
        details.append(f"窗口{hours}h: {len(got)}条{'✓' if good else '✗'}")
        transcript.append(f"===== --window-hours {hours} =====\n{result.stdout}")
        if not good:
            transcript.append(f"缺={want - got} 多={got - want}")
    # 配置默认窗口回归：不传 --window-hours 时用 config 的 24
    workspace = fresh_dir("t3-default")
    cfg = write_config(workspace, [source("windowed", server.url("/windowed"))], window_hours=24)
    result = run_cli(["fetch", "--config", str(cfg), "--limit", "0"])
    default_ok = result.rc == 0 and set(titles(workspace / "data" / "aihot.db")) == expected[24]
    transcript.append(f"===== 默认窗口 24h =====\n{result.stdout}")
    suite.check(
        "3-时间窗口",
        all_ok and default_ok,
        f"{'；'.join(details)}；默认窗口{'✓' if default_ok else '✗'}",
        evidence="\n".join(transcript),
    )


# --------------------------------------------------------------------------- 6 --dry-run 不写库


def _t4_dry_run(suite: Suite, server: LocalServer) -> None:
    workspace = fresh_dir("t4")
    cfg = write_config(workspace, [source("good1", server.url("/good1"))])
    db = workspace / "data" / "aihot.db"
    before = db_counts(db)
    dry = run_cli(["fetch", "--config", str(cfg), "--dry-run", "--limit", "3"])
    after = db_counts(db)
    real = run_cli(["fetch", "--config", str(cfg), "--limit", "0"])
    final = db_counts(db)
    ok = (
        dry.rc == 0
        and "dry-run，不写库" in dry.stdout
        and before == {"exists": 0, "items": 0, "unique_keys": 0, "runs": 0}
        and after == {"exists": 0, "items": 0, "unique_keys": 0, "runs": 0}
        and real.rc == 0
        and final["items"] == 2
        and final["runs"] == 1
    )
    suite.check(
        "6-dry-run不写库",
        ok,
        f"dry-run 前={before['items']}/runs={before['runs']} "
        f"后={after['items']}/runs={after['runs']}；"
        f"随后真实运行落库={final['items']} runs={final['runs']}",
        evidence=f"--- dry-run ---\n{dry.stdout}\n--- 真实运行 ---\n{real.stdout}",
    )


# --------------------------------------------------------------------------- 7 --source 过滤


def _t5_source_filter(suite: Suite, server: LocalServer) -> Path:
    workspace = fresh_dir("t5")
    cfg = write_config(
        workspace,
        [
            source("s1", server.url("/filter1")),
            source("s2", server.url("/filter2")),
            source("s3", server.url("/filter3")),
        ],
    )
    filtered_db = workspace / "data" / "aihot.db"
    filtered = run_cli(
        ["fetch", "--config", str(cfg), "--source", "s1", "--source", "s3", "--limit", "0"]
    )
    rows = db_query(filtered_db, "SELECT source_id, title FROM items ORDER BY title")
    source_ids = sorted({row["source_id"] for row in rows})
    only_ok = filtered.rc == 0 and source_ids == ["s1", "s3"] and len(rows) == 3
    suite.check(
        "7a---source过滤",
        only_ok,
        f"rc={filtered.rc} 落库源={source_ids} 条数={len(rows)}",
        evidence=filtered.stdout,
    )

    ghost = run_cli(["fetch", "--config", str(cfg), "--source", "ghost-source"])
    suite.check(
        "7b---source未知ID",
        ghost.rc == 1 and "没有可用的信息源" in ghost.stdout,
        f"rc={ghost.rc}（期望 1）",
        evidence=ghost.stdout,
    )

    full_ws = fresh_dir("t5-full")
    full_cfg = write_config(
        full_ws,
        [
            source("s1", server.url("/filter1")),
            source("s2", server.url("/filter2")),
            source("s3", server.url("/filter3")),
        ],
    )
    full = run_cli(["fetch", "--config", str(full_cfg), "--limit", "0"])
    full_db = full_ws / "data" / "aihot.db"
    counts = db_counts(full_db)
    shared = db_query(
        full_db, "SELECT source_id FROM items WHERE url = 'https://shared.example/article'"
    )
    suite.check(
        "5c-同URL跨源不覆盖",
        full.rc == 0 and counts["items"] == 5 and counts["unique_keys"] == 5 and len(shared) == 2,
        f"全量落库={counts['items']}（唯一={counts['unique_keys']}）同一URL行数={len(shared)}",
        evidence=full.stdout,
    )
    return full_db


# --------------------------------------------------------------------------- 5 落库正确性


def _t6_store_correctness(suite: Suite, db: Path) -> None:
    tables = table_names(db)
    suite.check(
        "5a-四张表",
        all(name in tables for name in ("items", "clusters", "digests", "runs")),
        f"tables={tables}",
    )
    rows = db_query(db, "SELECT * FROM items ORDER BY id")
    problems: list[str] = []
    for row in rows:
        for field in ("title", "url", "published_at", "fetched_at", "first_seen_at"):
            if not row[field]:
                problems.append(f"id={row['id']} 缺字段 {field}")
        if not str(row["url"]).startswith("http"):
            problems.append(f"id={row['id']} url 非 http: {row['url']!r}")
        try:
            extra = json.loads(row["extra"])
            if not isinstance(extra, dict):
                problems.append(f"id={row['id']} extra 非对象")
        except json.JSONDecodeError as exc:
            problems.append(f"id={row['id']} extra 非法 JSON: {exc}")
    suite.check(
        "5b-字段完整+extra合法JSON",
        not problems,
        f"检查 {len(rows)} 行，问题 {len(problems)} 个" + (f": {problems[:3]}" if problems else ""),
    )
    import hashlib

    hash_problems = [
        row["id"]
        for row in rows
        if row["url_hash"] != hashlib.sha1(row["url"].encode("utf-8")).hexdigest()
    ]
    suite.check(
        "5c-url_hash与URL一致",
        not hash_problems,
        f"不一致行: {hash_problems or '无'}",
    )
    indexes = db_query(db, "PRAGMA index_list('items')")
    unique_names = [row["name"] for row in indexes if row["unique"] == 1]
    unique_cols = []
    for name in unique_names:
        columns = db_query(db, f"PRAGMA index_info('{name}')")
        unique_cols.append(tuple(row["name"] for row in columns))
    has_unique = ("source_id", "url_hash") in unique_cols
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO items (url_hash, source_id, title, url, fetched_at, first_seen_at) "
            "VALUES ('dup-hash-test', 's1', 't', 'u', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO items (url_hash, source_id, title, url, fetched_at, first_seen_at) "
            "VALUES ('dup-hash-test', 's1', 't2', 'u2', 'now', 'now')"
        )
        conn.commit()
        duplicate_rejected = False
    except sqlite3.IntegrityError:
        duplicate_rejected = True
    finally:
        conn.rollback()
        conn.close()
    suite.check(
        "5d-UNIQUE(source_id,url_hash)生效",
        has_unique and duplicate_rejected,
        f"唯一索引列={unique_cols} 重复插入被拒={duplicate_rejected}",
    )


# --------------------------------------------------------------------------- 8 退出码语义


def _t7_exit_codes(suite: Suite, server: LocalServer) -> None:
    # 全部源失败 -> 1
    workspace = fresh_dir("t7-allfail")
    port = closed_port()
    cfg = write_config(
        workspace,
        [
            source("bad500", server.url("/bad500")),
            source("refused", f"http://127.0.0.1:{port}/feed"),
        ],
    )
    allfail = run_cli(["fetch", "--config", str(cfg)])
    runs = db_query(
        workspace / "data" / "aihot.db", "SELECT * FROM runs ORDER BY rowid DESC LIMIT 1"
    )
    suite.check(
        "8a-全部源失败=1",
        allfail.rc == 1 and runs[0]["status"] == "failed",
        f"rc={allfail.rc} runs.status={runs[0]['status'] if runs else '<none>'}",
        evidence=allfail.stdout,
    )
    # 配置错误 -> 3
    missing = run_cli(["fetch", "--config", str(fresh_dir("t7-cfg") / "nope.yaml")])
    bad_ws = fresh_dir("t7-badyaml")
    bad_cfg = bad_ws / "config.yaml"
    bad_cfg.write_text("app: [oops\n", encoding="utf-8")
    bad_yaml = run_cli(["fetch", "--config", str(bad_cfg)])
    suite.check(
        "8b-配置错误=3",
        missing.rc == 3 and bad_yaml.rc == 3,
        f"缺失配置 rc={missing.rc}，坏 YAML rc={bad_yaml.rc}（期望均 3）",
        evidence=f"--- missing ---\n{missing.stdout}\n--- bad yaml ---\n{bad_yaml.stdout}",
    )
    # 用法错误 -> 2
    bogus = run_cli(["fetch", "--bogus-flag"])
    badval = run_cli(["fetch", "--window-hours", "abc"])
    unknown = run_cli(["frobnicate"])
    suite.check(
        "8c-用法错误=2",
        bogus.rc == 2 and badval.rc == 2 and unknown.rc == 2,
        f"未知参数 rc={bogus.rc}，非法取值 rc={badval.rc}，未知命令 rc={unknown.rc}（期望均 2）",
        evidence=f"--- 未知参数 ---\n{bogus.stderr}\n--- 未知命令 ---\n{unknown.stderr}",
    )
    # 无启用源 -> 1
    ws_off = fresh_dir("t7-noenabled")
    cfg_off = write_config(
        ws_off, [source("off1", server.url("/good1"), enabled=False)]
    )
    no_enabled = run_cli(["fetch", "--config", str(cfg_off)])
    suite.check(
        "8d-无启用源=1",
        no_enabled.rc == 1 and "没有可用的信息源" in no_enabled.stdout,
        f"rc={no_enabled.rc}（期望 1）",
        evidence=no_enabled.stdout,
    )


# --------------------------------------------------------------------------- 补充观察


def _t8_extra_observations(suite: Suite, server: LocalServer) -> None:
    # dry-run + 全部失败
    workspace = fresh_dir("t8-dryfail")
    cfg = write_config(workspace, [source("bad500", server.url("/bad500"))])
    result = run_cli(["fetch", "--config", str(cfg), "--dry-run"])
    suite.check(
        "8e-dry-run全部失败=1",
        result.rc == 1,
        f"rc={result.rc}（期望 1）",
        evidence=result.stdout,
    )
    # 非法窗口参数 -> 用法错误 rc=2（修复后契约：1~168 整数）
    results = {}
    for value in ("0", "-5", "169", "abc"):
        ws = fresh_dir(f"t8-window-{value.strip('-')}")
        cfg_w = write_config(ws, [source("windowed", server.url("/windowed"))])
        run = run_cli(["fetch", "--config", str(cfg_w), "--window-hours", value, "--limit", "0"])
        results[value] = run.rc
    suite.check(
        "8f-非法--window-hours=2",
        all(rc == 2 for rc in results.values()),
        f"0/-5/169/abc 的 rc={results}（期望均 2）",
        evidence="\n".join(f"--window-hours {k} → rc={v}" for k, v in results.items()),
    )
    # 非法 --limit -> rc=2
    ws_limit = fresh_dir("t8-limit")
    cfg_limit = write_config(ws_limit, [source("windowed", server.url("/windowed"))])
    negative = run_cli(["fetch", "--config", str(cfg_limit), "--limit", "-1"])
    suite.check(
        "8g-非法--limit=2",
        negative.rc == 2,
        f"--limit -1 → rc={negative.rc}（期望 2）",
        evidence=negative.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
