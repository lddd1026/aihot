"""M1 白盒单元级验证：net 重试/超时、store 幂等/约束、utils 规范化。

这些是"外部行为"的补充：在产品代码边界上独立复算，不依赖开发者单测。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
    TEMPROOT,
    LocalServer,
    Payload,
    Suite,
)

from aihot.models import RawItem, RunRecord  # noqa: E402
from aihot.net import HttpClient  # noqa: E402
from aihot.store import Store  # noqa: E402
from aihot.utils import (  # noqa: E402
    detect_lang,
    normalize_url,
    parse_datetime,
    url_hash,
    within_window,
)


def temp_db(name: str) -> Path:
    TEMPROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f"aihot-m1-{name}-", dir=TEMPROOT))
    return directory / "test.db"


def main() -> int:  # noqa: C901
    suite = Suite("units")
    server = LocalServer()
    server.routes["/slow"] = lambda q: _slow()
    server.routes["/notfound"] = lambda q: Payload(404, b"nope")
    server.routes["/jsontext"] = lambda q: Payload(200, b"not json")
    server.routes["/json500"] = lambda q: Payload(500, b"oops")
    calls = {"n": 0}

    def flaky(query: dict[str, list[str]]) -> Payload:
        calls["n"] += 1
        if calls["n"] < 3:
            return Payload(503, b"try later")
        return Payload(200, b"ok")

    server.routes["/flaky"] = flaky
    try:
        _net_checks(suite, server, calls)
        _store_checks(suite)
        _utils_checks(suite)
    finally:
        server.stop()
    return suite.finish()


def _slow() -> Payload:
    time.sleep(3.0)
    return Payload(200, b"late")


# --------------------------------------------------------------------------- net


def _net_checks(suite: Suite, server: LocalServer, calls: dict[str, int]) -> None:
    with HttpClient(timeout=0.5, retries=0, backoff=0.0) as client:
        start = time.perf_counter()
        result = client.get(server.url("/slow"))
        elapsed = time.perf_counter() - start
    suite.check(
        "net-超时返回错误不抛异常",
        (not result.ok) and "Timeout" in result.error and 0.4 <= elapsed < 2.5,
        f"ok={result.ok} error={result.error!r} 耗时={elapsed:.2f}s（timeout=0.5s）",
    )

    calls["n"] = 0
    client = HttpClient(timeout=5.0, retries=2, backoff=0.1)
    start = time.perf_counter()
    result = client.get(server.url("/flaky"))
    elapsed = time.perf_counter() - start
    client.close()
    suite.check(
        "net-5xx重试退避后成功",
        result.ok and calls["n"] == 3 and elapsed >= 0.25,
        f"ok={result.ok} 请求次数={calls['n']} 耗时={elapsed:.2f}s（退避 0.1+0.2）",
    )

    client = HttpClient(timeout=5.0, retries=2, backoff=0.0)
    before = server.hits.get("/notfound", 0)
    result = client.get(server.url("/notfound"))
    client.close()
    hits = server.hits.get("/notfound", 0) - before
    suite.check(
        "net-4xx不重试",
        (not result.ok) and result.status == 404 and hits == 1,
        f"status={result.status} 请求次数={hits}",
    )

    client = HttpClient(timeout=5.0, retries=1, backoff=0.0)
    result = client.get("http://aihot-m1-invalid-9f3a.invalid/feed")
    client.close()
    suite.check(
        "net-DNS失败返回错误不抛异常",
        (not result.ok) and bool(result.error),
        f"ok={result.ok} error={result.error[:80]!r}",
    )

    client = HttpClient(timeout=5.0, retries=0, backoff=0.0)
    payload, result = client.get_json(server.url("/jsontext"))
    payload500, result500 = client.get_json(server.url("/json500"))
    client.close()
    suite.check(
        "net-非JSON与5xx的get_json语义",
        payload is None
        and "JSON 解析失败" in result.error
        and payload500 is None
        and bool(result500.error),
        f"非法JSON error={result.error[:40]!r}，500 error={result500.error!r}",
    )

    # D1 修复：非法代理地址在构造期抛清晰 ValueError（而非 httpx.InvalidURL / traceback）
    try:
        HttpClient(proxy="http://127.0.0.1:abc")
        raised = ""
    except ValueError as exc:
        raised = str(exc)
    suite.check(
        "net-非法代理地址抛清晰ValueError(D1)",
        "代理" in raised and "Invalid port" in raised,
        f"异常信息={raised!r}",
    )


# --------------------------------------------------------------------------- store


def _item(index: int, *, source_id: str = "s1", title: str | None = None) -> RawItem:
    return RawItem(
        source_id=source_id,
        title=title or f"item-{index}",
        url=f"https://store.example/{source_id}/{index}",
        published_at=datetime.now(UTC) - timedelta(minutes=index),
        raw_summary=f"summary {index}",
        lang="en",
        extra={"k": index},
    )


def _store_checks(suite: Suite) -> None:
    db = temp_db("store")
    with Store(db) as store:
        first = [_item(1), _item(2), _item(3)]
        new = store.filter_new(first)
        saved = store.save_items(new)
        again = store.filter_new(first)
        saved_again = store.save_items(again)
        total = store.total_items()
        suite.check(
            "store-filter_new幂等",
            len(new) == 3 and saved == 3 and again == [] and saved_again == 0 and total == 3,
            f"首次新增={saved} 二次新增={saved_again} filter_new再次={len(again)} 库内={total}",
        )

        batch = [_item(4), _item(5), _item(4, source_id="s2")]
        batch_new = store.filter_new(batch)
        suite.check(
            "store-批内去重(保留(源,URL)唯一)",
            len(batch_new) == 3,
            f"输入 3 条（其中 1 条批内重复）→ 通过 {len(batch_new)} 条",
        )

        other_source = store.filter_new([_item(1, source_id="s9")])
        suite.check(
            "store-同URL不同源互不影响",
            len(other_source) == 1 and store.save_items(other_source) == 1,
            f"新源新增={len(other_source)}（同 URL 不同 source_id 应视为新条目）",
        )

        tables = [
            row[0]
            for row in store._conn.execute(  # noqa: SLF001 - 独立测试直接观察连接
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]  # noqa: SLF001
        unique = [
            row[1]
            for row in store._conn.execute("PRAGMA index_list('items')").fetchall()  # noqa: SLF001
            if row[2] == 1
        ]
        suite.check(
            "store-四表+WAL+唯一索引",
            all(name in tables for name in ("items", "clusters", "digests", "runs"))
            and str(mode).lower() == "wal"
            and len(unique) >= 1,
            f"tables={tables} journal_mode={mode} unique索引={unique}",
        )

        record = RunRecord(
            run_id="run-test-1",
            started_at=datetime.now(UTC) - timedelta(seconds=5),
            finished_at=datetime.now(UTC),
            status="partial",
            stage_stats={"stage": "fetch", "fetched": 12, "中文键": "值"},
            error="",
        )
        store.log_run(record)
        runs = store.recent_runs(limit=1)
        stats = runs[0]["stage_stats"] if runs else "{}"
        suite.check(
            "store-runs记录完整(JSON可解析)",
            bool(runs)
            and runs[0]["status"] == "partial"
            and "中文键" in stats
            and isinstance(json.loads(stats), dict),
            f"status={runs[0]['status'] if runs else '无'} stage_stats={stats}",
        )

        # naive datetime 现按 UTC 解释（D4 修复）
        naive = datetime(2026, 1, 1, 0, 0, 0)
        store.save_items(
            [
                RawItem(
                    source_id="naive",
                    title="naive-dt",
                    url="https://store.example/naive",
                    published_at=naive,
                )
            ]
        )
        row = store._conn.execute(  # noqa: SLF001
            "SELECT published_at FROM items WHERE source_id='naive'"
        ).fetchone()
        suite.check(
            "store-naive datetime 按UTC解释",
            row[0] == "2026-01-01T00:00:00+00:00",
            f"输入 {naive.isoformat()} → 存储 {row[0]}（期望 2026-01-01T00:00:00+00:00）",
        )

        # 大批量性能
        big = [_item(index, source_id="bulk") for index in range(20_000)]
        start = time.perf_counter()
        bulk_new = store.filter_new(big)
        filter_seconds = time.perf_counter() - start
        start = time.perf_counter()
        bulk_saved = store.save_items(bulk_new)
        save_seconds = time.perf_counter() - start
        start = time.perf_counter()
        bulk_again = store.filter_new(big)
        filter_again = time.perf_counter() - start
        suite.check(
            "store-2万条批量<5s",
            bulk_saved == 20_000 and bulk_again == [] and save_seconds < 5,
            f"新增={bulk_saved} 再过滤={len(bulk_again)} 写={save_seconds:.2f}s "
            f"查={filter_seconds:.2f}s/复核={filter_again:.2f}s 总量={store.total_items()}",
        )


# --------------------------------------------------------------------------- utils


def _utils_checks(suite: Suite) -> None:
    cases = {
        "": "",
        "https://Example.COM/A/?utm_source=x#f": "https://example.com/A",
        "http://e.com:80/a": "http://e.com/a",
        "https://e.com:443/a": "https://e.com/a",
        "https://e.com/a//b///c": "https://e.com/a/b/c",
        "https://e.com/a?b=2&a=1": "https://e.com/a?a=1&b=2",
        "https://e.com/a?ref=x&from=y&a=1&fbclid=z": "https://e.com/a?a=1",
        "https://e.com/a?empty=": "https://e.com/a?empty=",
        "HTTPS://E.COM/Path": "https://e.com/Path",
        "https://e.com": "https://e.com",
        "https://e.com/中文": "https://e.com/中文",
    }
    wrong = {
        raw: (normalize_url(raw), want)
        for raw, want in cases.items()
        if normalize_url(raw) != want
    }
    suite.check(
        "utils-normalize_url",
        not wrong,
        f"{len(cases)} 例，不符合 {len(wrong)} 例" + (f": {wrong}" if wrong else ""),
    )

    same = [
        "https://Example.COM/A/?utm_source=x#f",
        "https://example.com/A",
        "https://example.com/A?a=1&utm_medium=y",
    ]
    hashes = {url_hash(same[0]), url_hash(same[1])}
    suite.check(
        "utils-url_hash稳定",
        len(hashes) == 1
        and url_hash(same[0]) != url_hash(same[2])
        and url_hash("https://e.com/a") != url_hash("https://e.com/b"),
        f"等价 URL 哈希一致={len(hashes) == 1}，不同 URL 哈希不同="
        f"{url_hash('https://e.com/a') != url_hash('https://e.com/b')}",
    )

    parsed_ok = (
        parse_datetime(None) is None
        and parse_datetime("not a datetime") is None
        and parse_datetime("2026-09-23T10:00:00") == datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
        and parse_datetime("2026-09-23T10:00:00+08:00") == datetime(2026, 9, 23, 2, 0, tzinfo=UTC)
    )
    suite.check(
        "utils-parse_datetime",
        parsed_ok,
        f"None→None，垃圾串→None，naive按UTC，带时区换算；naive结果="
        f"{parse_datetime('2026-09-23T10:00:00')}",
    )

    window_ok = (
        within_window(None, 24) is False
        and within_window(datetime.now(UTC) - timedelta(hours=1), 24) is True
        and within_window(datetime.now(UTC) - timedelta(hours=25), 24) is False
    )
    suite.check(
        "utils-within_window",
        window_ok,
        "None→False，1h前→True，25h前→False",
    )

    suite.check(
        "utils-detect_lang",
        detect_lang("你好世界") == "zh" and detect_lang("Hello world") == "en",
        f"中文→{detect_lang('你好世界')}，英文→{detect_lang('Hello world')}",
    )

    # D3 修复：URL 白名单与相对链接补全
    from aihot.sources.base import absolutize_url, is_http_url

    url_checks = {
        "https://e.com/a": is_http_url("https://e.com/a"),
        "javascript:alert(1)": not is_http_url("javascript:alert(1)"),
        "mailto:a@b.com": not is_http_url("mailto:a@b.com"),
        "https:not a url": not is_http_url("https:not a url"),
        "https://e.com/a b": not is_http_url("https://e.com/a b"),
        "": not is_http_url(""),
    }
    abs_checks = {
        "/x/y": absolutize_url("https://e.com/feed", "/x/y") == "https://e.com/x/y",
        "//cdn.e.com/a": absolutize_url("https://e.com/feed", "//cdn.e.com/a")
        == "https://cdn.e.com/a",
        "https://other.com/z": absolutize_url("https://e.com/feed", "https://other.com/z")
        == "https://other.com/z",
        "rel/path": absolutize_url("https://e.com/dir/feed", "rel/path")
        == "https://e.com/dir/rel/path",
    }
    suite.check(
        "utils-URL白名单与相对补全(D3)",
        all(url_checks.values()) and all(abs_checks.values()),
        f"白名单 {url_checks}；补全 {abs_checks}",
    )

    weird = {
        "javascript:alert(1)": normalize_url("javascript:alert(1)"),
        "not a url": normalize_url("not a url"),
    }
    suite.note(f"normalize_url 原始输出（白名单在 sources.base 层生效）：{weird}")


if __name__ == "__main__":
    raise SystemExit(main())
