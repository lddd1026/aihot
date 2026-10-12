"""M1 对抗性测试：空源、坏 XML、超大源、乱字段、注入标题、编码、URL 规范化等。

全部使用本地模拟源，不访问真实网络、不触碰真实数据库。
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m1_support import (  # noqa: E402
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
    titles,
    write_config,
)

JSON_CT = "application/json; charset=utf-8"


def fresh_dir(name: str) -> Path:
    TEMPROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"aihot-m1-{name}-", dir=TEMPROOT))


def fetch_once(
    name: str, sources: list[dict], *, extra_args: list[str] | None = None, window: int = 24
):
    workspace = fresh_dir(name)
    cfg = write_config(workspace, sources, window_hours=window)
    result = run_cli(
        ["fetch", "--config", str(cfg), *(extra_args or []), "--limit", "0"]
    )
    db = workspace / "data" / "aihot.db"
    return result, db


def main() -> int:  # noqa: C901 - 对抗脚本，按用例线性展开
    suite = Suite("adversarial")
    server = LocalServer()
    try:
        _register(server)
        _empty_and_invalid(suite, server)
        _huge_feed(suite, server)
        _unreachable(suite, server)
        _hackernews(suite, server)
        _url_normalization(suite, server)
        _messy_feed(suite, server)
        _nasty_titles(suite, server)
        _duplicates(suite, server)
        _encoding(suite, server)
    finally:
        server.stop()
    return suite.finish()


def _register(server: LocalServer) -> None:
    server.routes["/good"] = lambda q: Payload(
        200,
        rss_xml([{"title": "good-item", "link": "https://ok.example/1", "date": ago(hours=1)}]),
    )
    server.routes["/empty"] = lambda q: Payload(200, rss_xml([]))
    server.routes["/empty-body"] = lambda q: Payload(200, b"")
    server.routes["/notxml"] = lambda q: Payload(200, b"<<<not xml at all>>>")
    server.routes["/html"] = lambda q: Payload(
        200, b"<!doctype html><html><body>hello</body></html>", "text/html; charset=utf-8"
    )
    server.routes["/drop"] = lambda q: Payload(200, None)
    server.routes["/hn-nohits"] = lambda q: Payload(200, b'{"nbHits": 0}', JSON_CT)
    server.routes["/hn-notdict"] = lambda q: Payload(200, b"[1, 2, 3]", JSON_CT)

    huge_entries = [
        {
            "title": f"huge-{index:04d}-" + "x" * 180,
            "link": f"https://huge.example/item/{index}",
            "date": ago(hours=1),
        }
        for index in range(4000)
    ]
    huge_body = rss_xml(huge_entries)
    server.routes["/huge"] = lambda q: Payload(200, huge_body)
    server.huge_bytes = len(huge_body)  # type: ignore[attr-defined]

    hn_hits = [
        {
            "objectID": "1",
            "title": "hn-complete",
            "url": "https://hn.example/1",
            "created_at": ago(hours=1).isoformat(),
            "points": 42,
            "num_comments": 7,
            "author": "alice",
            "story_text": "<p>hello <b>world</b></p>",
        },
        {"objectID": "2", "title": "", "url": "https://hn.example/2"},
        {"objectID": "3", "title": "hn-no-url-nor-objectid"},
        {
            "objectID": "4",
            "title": "hn-created-at-i",
            "url": "https://hn.example/4",
            "created_at_i": int(ago(hours=2).timestamp()),
            "points": "55",
            "num_comments": None,
        },
    ]
    server.routes["/hn-messy"] = lambda q: Payload(
        200, json.dumps({"hits": hn_hits}).encode(), JSON_CT
    )

    norm_entries = [
        {
            "title": "u1-utm",
            "link": "https://Example.COM/Article/?utm_source=weibo&b=2&a=1#section",
            "date": ago(hours=1),
        },
        {
            "title": "u2-canonical-dup",
            "link": "https://example.com/Article?a=1&b=2",
            "date": ago(hours=1),
        },
        {"title": "u3-slash", "link": "https://example.com/Path/", "date": ago(hours=1)},
        {"title": "u4-port443", "link": "https://example.com:443/Secure", "date": ago(hours=1)},
        {"title": "u5-port80", "link": "http://example.com:80/Plain", "date": ago(hours=1)},
        {"title": "u6-encoded", "link": "https://example.com/a%20b", "date": ago(hours=1)},
    ]
    server.routes["/urlnorm"] = lambda q: Payload(200, rss_xml(norm_entries))

    messy_entries = [
        {"title": "no-link-title", "date": ago(hours=1)},
        {"link": "https://messy.example/no-title", "date": ago(hours=1)},
        {"title": "   ", "link": "https://messy.example/blank", "date": ago(hours=1)},
        {"title": "no-date", "link": "https://messy.example/no-date"},
        {
            "title": "bad-date",
            "link": "https://messy.example/bad-date",
            "raw_date": "Tue, 32 Foo 2099 99:99:99 +0800",
        },
        {
            "title": "html-title",
            "link": "https://messy.example/html",
            "date": ago(hours=1),
            "summary": "<script>alert(1)</script>visible text",
        },
        {
            "title": "future",
            "link": "https://messy.example/future",
            "date": datetime.now(UTC).replace(microsecond=0),
        },
    ]
    server.routes["/messy"] = lambda q: Payload(200, rss_xml(messy_entries))

    nasty_entries = [
        {
            "title": "'); DROP TABLE items;--",
            "link": "https://nasty.example/sql",
            "date": ago(hours=1),
        },
        {
            "title": "🧠🔥 Emoji 标题 test",
            "link": "https://nasty.example/emoji",
            "date": ago(hours=1),
        },
        {"title": "A" * 100_000, "link": "https://nasty.example/long", "date": ago(hours=1)},
    ]
    server.routes["/nasty"] = lambda q: Payload(200, rss_xml(nasty_entries))

    server.routes["/dups"] = lambda q: Payload(
        200,
        rss_xml(
            [
                {"title": "dup-first", "link": "https://dups.example/x", "date": ago(hours=1)},
                {"title": "dup-second", "link": "https://dups.example/x/", "date": ago(hours=1)},
                {"title": "dup-third", "link": "https://DUPS.example/x#frag", "date": ago(hours=1)},
            ]
        ),
    )


# --------------------------------------------------------------------------- 用例


def _empty_and_invalid(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once(
        "adv-empty",
        [source("empty", server.url("/empty")), source("good", server.url("/good"))],
    )
    counts = db_counts(db)
    ok = result.rc == 0 and "[OK ] empty" in result.stdout and counts["items"] == 1
    suite.check(
        "adv-空feed",
        ok,
        f"rc={result.rc} 落库={counts['items']}（空源应计 0 条且不失败）",
        evidence=result.stdout,
    )

    result, db = fetch_once(
        "adv-invalid",
        [
            source("notxml", server.url("/notxml")),
            source("html", server.url("/html")),
            source("emptybody", server.url("/empty-body")),
            source("drop", server.url("/drop")),
            source("good", server.url("/good")),
        ],
    )
    counts = db_counts(db)
    marks = [line for line in result.stdout.splitlines() if "[ERR]" in line]
    ok = (
        result.rc == 0
        and counts["items"] == 1
        and len(marks) == 4
        and all(name in result.stdout for name in ("notxml", "html", "emptybody", "drop"))
    )
    suite.check(
        "adv-非法XML/HTML/空响应/断连",
        ok,
        f"rc={result.rc} 4 个坏源全部 [ERR] 且好源落库 {counts['items']} 条",
        evidence=result.stdout,
    )


def _huge_feed(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once("adv-huge", [source("huge", server.url("/huge"))])
    counts = db_counts(db)
    size = getattr(server, "huge_bytes", 0)
    ok = (
        result.rc == 0
        and counts["items"] == 4000
        and counts["unique_keys"] == 4000
        and result.seconds < 60
    )
    suite.check(
        "adv-超大feed(4000条)",
        ok,
        f"rc={result.rc} 落库={counts['items']} 唯一={counts['unique_keys']} "
        f"feed={size / 1024:.0f}KB 耗时={result.seconds:.1f}s",
        evidence=result.stdout[-2000:],
    )


def _unreachable(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once(
        "adv-unreachable",
        [
            source("bad-domain", "http://aihot-m1-nonexistent-9f3a.invalid/feed"),
            source("good", server.url("/good")),
        ],
    )
    counts = db_counts(db)
    ok = result.rc == 0 and counts["items"] == 1 and "[ERR] bad-domain" in result.stdout
    suite.check(
        "adv-不可达域名",
        ok,
        f"rc={result.rc} 坏源隔离，好源落库={counts['items']}",
        evidence=result.stdout,
    )


def _hackernews(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once(
        "adv-hn-nohits",
        [
            source("hn-nohits", server.url("/hn-nohits"), type_="hackernews"),
            source("hn-notdict", server.url("/hn-notdict"), type_="hackernews"),
            source("good", server.url("/good")),
        ],
    )
    ok = (
        result.rc == 0
        and "[ERR] hn-nohits" in result.stdout
        and "[ERR] hn-notdict" in result.stdout
        and db_counts(db)["items"] == 1
    )
    suite.check(
        "adv-HN缺少hits/非对象响应",
        ok,
        f"rc={result.rc} 两类坏响应均被隔离为失败",
        evidence=result.stdout,
    )

    result, db = fetch_once(
        "adv-hn-messy",
        [source("hn-messy", server.url("/hn-messy"), type_="hackernews")],
    )
    rows = db_query(db, "SELECT title, url, extra FROM items ORDER BY url")
    got_titles = [row["title"] for row in rows]
    extra = {}
    for row in rows:
        if row["title"] == "hn-complete":
            extra = json.loads(row["extra"])
    ok = (
        result.rc == 0
        and sorted(got_titles)
        == sorted(["hn-complete", "hn-created-at-i", "hn-no-url-nor-objectid"])
        and extra.get("points") == 42
        and extra.get("num_comments") == 7
        and extra.get("hn_id") == "1"
        and extra.get("author") == "alice"
        and extra.get("hn_url") == "https://news.ycombinator.com/item?id=1"
    )
    suite.check(
        "adv-HN字段缺失容错+extra热度信号",
        ok,
        f"rc={result.rc} 落库标题={got_titles} points={extra.get('points')} "
        f"comments={extra.get('num_comments')}",
        evidence=result.stdout,
    )


def _url_normalization(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once("adv-urlnorm", [source("urlnorm", server.url("/urlnorm"))])
    rows = db_query(db, "SELECT title, url FROM items ORDER BY title")
    urls = sorted(row["url"] for row in rows)
    expected = sorted(
        [
            "https://example.com/Article?a=1&b=2",
            "https://example.com/Path",
            "https://example.com/Secure",
            "http://example.com/Plain",
            "https://example.com/a%20b",
        ]
    )
    ok = result.rc == 0 and urls == expected and len(rows) == 5
    suite.check(
        "adv-URL规范化(utm/锚点/大小写/尾斜杠/默认端口/多参数排序)",
        ok,
        f"落库 {len(rows)} 条 URL={urls}",
        evidence=result.stdout,
    )


def _messy_feed(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once("adv-messy", [source("messy", server.url("/messy"))])
    rows = db_query(db, "SELECT title, url, raw_summary, published_at FROM items ORDER BY title")
    got = {row["title"] for row in rows}
    expected = {"no-date", "bad-date", "html-title", "future"}
    summary = next((row["raw_summary"] for row in rows if row["title"] == "html-title"), "")
    published = {row["title"]: row["published_at"] for row in rows}
    now = datetime.now(UTC)
    no_date_recent = False
    for title in ("no-date", "bad-date"):
        value = published.get(title)
        if value:
            delta = abs((now - datetime.fromisoformat(value)).total_seconds())
            no_date_recent = no_date_recent or delta < 300
    ok = (
        result.rc == 0
        and got == expected
        and summary == "visible text"
        and no_date_recent
    )
    suite.check(
        "adv-缺title/link/date+坏日期+HTML清洗",
        ok,
        f"rc={result.rc} 保留={sorted(got)} 丢弃=3条 无日期条目落库为当前时间={no_date_recent} "
        f"script清洗后摘要={summary!r}",
        evidence=result.stdout,
    )


def _nasty_titles(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once("adv-nasty", [source("nasty", server.url("/nasty"))])
    rows = db_query(db, "SELECT title FROM items ORDER BY length(title)")
    got = [row["title"] for row in rows]
    tables = db_query(db, "SELECT name FROM sqlite_master WHERE type='table' AND name='items'")
    ok = (
        result.rc == 0
        and len(got) == 3
        and "'); DROP TABLE items;--" in got
        and "🧠🔥 Emoji 标题 test" in got
        and "A" * 1000 in got
        and bool(tables)
    )
    suite.check(
        "adv-SQL注入式标题/emoji/超长标题",
        ok,
        f"rc={result.rc} 落库={len(got)}条 items表仍在={bool(tables)} "
        f"最长标题={max((len(t) for t in got), default=0)}字符"
        "（100k 原标题被 clean_html 截断为 1000）",
        evidence=result.stdout[-1500:],
    )


def _duplicates(suite: Suite, server: LocalServer) -> None:
    result, db = fetch_once("adv-dups", [source("dups", server.url("/dups"))])
    counts = db_counts(db)
    rows = db_query(db, "SELECT title, url FROM items")
    ok = (
        result.rc == 0
        and counts["items"] == 1
        and counts["unique_keys"] == 1
        and rows
        and rows[0]["url"] == "https://dups.example/x"
    )
    suite.check(
        "adv-同源内重复条目(尾斜杠/大小写/锚点)去重",
        ok,
        f"3 条重复链接落库={counts['items']} 条，保留 URL={rows[0]['url'] if rows else '无'}",
        evidence=result.stdout,
    )


def _encoding(suite: Suite, server: LocalServer) -> None:
    xml = (
        '<?xml version="1.0" encoding="GBK"?>\n'
        "<rss version=\"2.0\"><channel><title>gbk</title>"
        f"<item><title>中文标题测试</title><link>https://gbk.example/1</link>"
        f"<pubDate>{format_datetime(ago(hours=1))}</pubDate>"
        "</item></channel></rss>"
    )
    body = xml.encode("gbk")
    server.routes["/gbk"] = lambda q: Payload(200, body, "application/rss+xml; charset=gbk")
    result, db = fetch_once("adv-gbk", [source("gbk", server.url("/gbk"))])
    got = titles(db)
    ok = result.rc == 0 and got == ["中文标题测试"]
    suite.check(
        "adv-GBK编码源中文不乱码",
        ok,
        f"rc={result.rc} 落库标题={got}",
        evidence=result.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
