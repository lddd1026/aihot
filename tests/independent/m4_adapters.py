"""M4 验收 6 独立验证：新适配器（arxiv / html / hf_papers）黑盒测试。

通过真实 HTTP（本地假服务）走适配器，不依赖 MonkeyPatch。
跑法: .venv\\Scripts\\python.exe tests\\independent\\m4_adapters.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from m4_support import FakeHTTP, Suite  # noqa: E402

from aihot.config import SourceConfig  # noqa: E402
from aihot.net import DEFAULT_USER_AGENT, HttpClient  # noqa: E402
from aihot.sources.arxiv import ArxivAdapter  # noqa: E402
from aihot.sources.base import SourceError  # noqa: E402
from aihot.sources.hf_papers import HfPapersAdapter  # noqa: E402
from aihot.sources.html import HtmlAdapter  # noqa: E402

SINCE = datetime(2026, 9, 24, 0, 0, tzinfo=UTC)


def source(kind: str, url: str, **extra: object) -> SourceConfig:
    return SourceConfig(
        id=f"test_{kind}",
        name=f"test {kind}",
        type=kind,
        url=url,
        enabled=True,
        weight=1.0,
        extra=dict(extra),
    )


def http() -> HttpClient:
    return HttpClient(retries=0, backoff=0.0)


# --------------------------------------------------------------------------- arXiv


def arxiv_feed(*, empty: bool = False) -> str:
    if empty:
        return '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    entries = []
    for index in range(1, 9):
        entries.append(
            f"""
  <entry>
    <id>http://arxiv.org/abs/2609.1000{index}v1</id>
    <title>Independent Paper {index}</title>
    <summary>Abstract {index} for independent verification.</summary>
    <published>2026-09-25T0{index}:00:00Z</published>
    <link href="http://arxiv.org/abs/2609.1000{index}v1" rel="alternate" type="text/html"/>
    <author><name>Author {index}A</name></author>
    <author><name>Author {index}B</name></author>
    <category term="cs.AI"/>
    <category term="cs.CL"/>
  </entry>"""
        )
    old = """
  <entry>
    <id>http://arxiv.org/abs/2609.09000v2</id>
    <title>Too Old Paper</title>
    <summary>Old.</summary>
    <published>2026-09-01T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2609.09000v2" rel="alternate" type="text/html"/>
    <category term="cs.AI"/>
  </entry>"""
    updated_only = """
  <entry>
    <id>http://arxiv.org/abs/2609.09999v1</id>
    <title>Updated Only Paper</title>
    <summary>Updated only.</summary>
    <updated>2026-09-24T12:00:00Z</updated>
    <link href="http://arxiv.org/abs/2609.09999v1" rel="alternate" type="text/html"/>
    <category term="cs.LG"/>
  </entry>"""
    boundary = """
  <entry>
    <id>http://arxiv.org/abs/2609.08888v1</id>
    <title>Boundary Paper</title>
    <summary>Boundary.</summary>
    <published>2026-09-24T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2609.08888v1" rel="alternate" type="text/html"/>
    <category term="cs.CV"/>
  </entry>"""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        + "".join(entries)
        + old
        + updated_only
        + boundary
        + "</feed>"
    )


def suite_arxiv(suite: Suite) -> None:
    with FakeHTTP() as fake:
        fake.add("/api/query", 200, arxiv_feed(), "application/atom+xml")
        adapter = ArxivAdapter(
            source("arxiv", fake.url("/api/query"), categories=["cs.AI", "cs.LG"], max_results=60),
            http(),
        )
        items = adapter.fetch(SINCE)
        request = fake.requests[0]
        query = request["query"]
        suite.check(
            "6.1 arXiv 分类参数拼接正确",
            query.get("search_query") == "cat:cs.AI OR cat:cs.LG",
            f"search_query={query.get('search_query')!r}",
        )
        suite.check(
            "6.2 arXiv 排序参数正确",
            query.get("sortBy") == "submittedDate" and query.get("sortOrder") == "descending",
            f"sortBy={query.get('sortBy')} sortOrder={query.get('sortOrder')}",
        )
        suite.check(
            "6.3 arXiv max_results 透传",
            query.get("max_results") == "60",
            f"max_results={query.get('max_results')}",
        )
        suite.check(
            "6.4 arXiv 时间窗口过滤（10 天前丢弃）",
            all("09000" not in item.url for item in items),
            f"共 {len(items)} 条，无 2609.09000",
        )
        boundary = [item for item in items if "08888" in item.url]
        suite.check(
            "6.5 arXiv 边界时刻（published==since）保留",
            len(boundary) == 1,
            f"boundary={len(boundary)}",
        )
        updated_only = [item for item in items if "09999" in item.url]
        suite.check(
            "6.6 arXiv 无 published 时回退 updated",
            len(updated_only) == 1
            and updated_only[0].published_at == datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
            f"published_at={updated_only[0].published_at if updated_only else None}",
        )
        first = next(item for item in items if "10001" in item.url)
        suite.check(
            "6.7 arXiv extra.kind=paper",
            first.extra.get("kind") == "paper",
            f"kind={first.extra.get('kind')!r}",
        )
        suite.check(
            "6.8 arXiv extra.authors/author_count",
            first.extra.get("authors") == ["Author 1A", "Author 1B"]
            and first.extra.get("author_count") == 2,
            f"authors={first.extra.get('authors')} count={first.extra.get('author_count')}",
        )
        suite.check(
            "6.9 arXiv extra.categories",
            first.extra.get("categories") == ["cs.AI", "cs.CL"],
            f"categories={first.extra.get('categories')}",
        )
        suite.check(
            "6.10 arXiv extra.arxiv_id",
            first.extra.get("arxiv_id") == "2609.10001v1",
            f"arxiv_id={first.extra.get('arxiv_id')!r}",
        )
        suite.check(
            "6.11 arXiv 摘要去除 HTML 并保留文本",
            "Abstract 1" in first.raw_summary,
            f"summary={first.raw_summary[:50]!r}",
        )
        suite.check("6.12 arXiv 语言识别 en", first.lang == "en", f"lang={first.lang}")
        suite.check(
            "6.13 arXiv 请求头使用浏览器 UA",
            "Chrome/" in request["headers"].get("User-Agent", ""),
            f"UA={request['headers'].get('User-Agent', '')[:40]}",
        )

    # 默认分类与上限裁剪
    with FakeHTTP() as fake:
        fake.add("/api/query", 200, arxiv_feed(empty=False), "application/atom+xml")
        adapter = ArxivAdapter(source("arxiv", fake.url("/api/query"), max_results=9999), http())
        adapter.fetch(SINCE)
        query = fake.requests[0]["query"]
        suite.check(
            "6.14 arXiv 默认分类 cs.AI/cs.CL/cs.LG",
            query.get("search_query") == "cat:cs.AI OR cat:cs.CL OR cat:cs.LG",
            f"search_query={query.get('search_query')!r}",
        )
        suite.check(
            "6.15 arXiv max_results 上限裁剪到 200",
            query.get("max_results") == "200",
            f"max_results={query.get('max_results')}",
        )

    with FakeHTTP() as fake:
        fake.add("/api/query", 200, arxiv_feed(empty=True), "application/atom+xml")
        adapter = ArxivAdapter(source("arxiv", fake.url("/api/query")), http())
        try:
            adapter.fetch(SINCE)
            ok, detail = False, "未抛 SourceError"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("6.16 arXiv 空 feed 抛 SourceError", ok, detail)

    with FakeHTTP() as fake:
        fake.add("/api/query", 500, "server error", "text/plain")
        adapter = ArxivAdapter(source("arxiv", fake.url("/api/query")), http())
        try:
            adapter.fetch(SINCE)
            ok, detail = False, "未抛 SourceError"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("6.17 arXiv HTTP 500 抛 SourceError", ok, detail)


# --------------------------------------------------------------------------- 通用 HTML

HTML_SAMPLE = """
<html><body><ul>
  <li class="item"><a href="/news/one"><span class="title">First Story</span></a>
      <time datetime="2026-09-25T00:30:00Z">Sep 25</time></li>
  <li class="item"><a href="/news/two">Second Story</a>
      <time>2026-09-25 02:00</time></li>
  <li class="item"><a href="/blog/three"><span class="title">Blog Post</span></a></li>
  <li class="item"><a href="/news/old"><span class="title">Too Old</span></a>
      <time datetime="2026-08-01T00:00:00Z">Aug 1</time></li>
  <li class="item"><span class="title">No anchor</span></li>
  <li class="item"><a href="/news/nolinkselector"><span class="title">No link match</span></a></li>
</ul></body></html>
"""

HTML_EXTRA = {
    "item_selector": "li.item",
    "link_selector": "a[href]",
    "link_pattern": "^/news/",
    "title_selector": "[class*='title']",
    "time_selector": "time",
    "time_attr": "datetime",
}


def suite_html(suite: Suite) -> None:
    with FakeHTTP() as fake:
        fake.add("/news", 200, HTML_SAMPLE)
        adapter = HtmlAdapter(source("html", fake.url("/news"), **HTML_EXTRA), http())
        items = adapter.fetch(SINCE)
        urls = [item.url for item in items]
        suite.check(
            "6.18 HTML link_pattern 过滤（/blog/ 被排除）",
            all("/news/" in url for url in urls)
            and len(items) == 3
            and all("/blog/" not in url for url in urls),
            f"urls={urls}",
        )
        suite.check(
            "6.19 HTML 相对链接补全为绝对地址",
            f"{fake.url('/news/one')}" == urls[0] or urls[0].endswith("/news/one"),
            f"url={urls[0]}",
        )
        first = next(item for item in items if item.url.endswith("/news/one"))
        suite.check(
            "6.20 HTML title_selector 命中",
            first.title == "First Story",
            f"title={first.title!r}",
        )
        second = next(item for item in items if item.url.endswith("/news/two"))
        suite.check(
            "6.21 HTML 标题回退到链接文本",
            second.title == "Second Story",
            f"title={second.title!r}",
        )
        suite.check(
            "6.22 HTML time_attr 解析 published_at",
            first.published_at == datetime(2026, 9, 25, 0, 30, tzinfo=UTC),
            f"published_at={first.published_at}",
        )
        suite.check(
            "6.23 HTML time 文本解析 published_at",
            second.published_at == datetime(2026, 9, 25, 2, 0, tzinfo=UTC),
            f"published_at={second.published_at}",
        )
        suite.check(
            "6.24 HTML 无链接条目被跳过",
            all("No anchor" not in item.title for item in items),
            f"titles={[item.title for item in items]}",
        )

    with FakeHTTP() as fake:
        fake.add("/news", 200, HTML_SAMPLE)
        adapter = HtmlAdapter(
            source("html", fake.url("/news"), item_selector="article.nope"), http()
        )
        try:
            adapter.fetch(SINCE)
            ok, detail = False, "未抛 SourceError"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("6.25 HTML item_selector 匹配为空抛 SourceError", ok, detail)

    with FakeHTTP() as fake:
        fake.add("/news", 404, "not found", "text/plain")
        adapter = HtmlAdapter(source("html", fake.url("/news"), **HTML_EXTRA), http())
        try:
            adapter.fetch(SINCE)
            ok, detail = False, "未抛 SourceError"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("6.26 HTML HTTP 404 抛 SourceError", ok, detail)

    # 观察项：link_selector 匹配为空时是否报错
    with FakeHTTP() as fake:
        fake.add("/news", 200, HTML_SAMPLE)
        extra = dict(HTML_EXTRA)
        extra["link_selector"] = "a.no-such-class"
        adapter = HtmlAdapter(source("html", fake.url("/news"), **extra), http())
        try:
            items = adapter.fetch(SINCE)
            suite.note(
                "观察：link_selector 匹配为空时返回 "
                f"{len(items)} 条且不报错（配置写错会静默变 0 条）"
            )
            suite.evidence("6-link-selector-empty.txt", f"items={len(items)}（未抛 SourceError）")
        except SourceError as exc:
            suite.note(f"link_selector 匹配为空会抛 SourceError: {exc}")

    # 无 time_selector 时的表现为观察项
    with FakeHTTP() as fake2:
        fake2.add("/news", 200, HTML_SAMPLE)
        extra = dict(HTML_EXTRA)
        extra.pop("time_selector")
        adapter = HtmlAdapter(source("html", fake2.url("/news"), **extra), http())
        items = adapter.fetch(datetime(2026, 1, 1, tzinfo=UTC))
        suite.check(
            "6.27 HTML 缺省 time 选择器仍能解析",
            all(item.published_at is not None for item in items) and len(items) >= 2,
            f"items={len(items)}",
        )


# --------------------------------------------------------------------------- HF Daily Papers


def suite_hf(suite: Suite) -> None:
    payload = [
        {
            "paper": {
                "id": "2609.77777",
                "title": "HF Featured Paper",
                "summary": "Featured abstract.",
                "upvotes": 42,
                "authors": [{"name": "Carol"}, {"name": "Dave"}],
                "publishedAt": "2026-09-01T00:00:00.000Z",
            },
            "publishedAt": "2026-09-25T01:00:00.000Z",
        },
        {"paper": {"id": "", "title": "Missing id"}},
        {"paper": {"id": "2609.66666", "title": ""}},
        "not-a-dict",
        {"paper": None},
        {
            "paper": {
                "id": "2609.55555",
                "title": "Upvotes as string",
                "summary": "S.",
                "upvotes": "17",
                "authors": None,
            },
            "publishedAt": "not-a-date",
        },
    ]
    with FakeHTTP() as fake:
        fake.add_json("/api/daily_papers", payload)
        adapter = HfPapersAdapter(
            source("hf_papers", fake.url("/api/daily_papers"), limit=30), http()
        )
        items = adapter.fetch(SINCE)
        suite.check(
            "6.28 HF 非字典/缺字段条目不崩溃且被跳过",
            len(items) == 2,
            f"items={len(items)} titles={[i.title for i in items]}",
        )
        first = next(item for item in items if "77777" in item.url)
        suite.check(
            "6.29 HF kind=paper 且 URL 正确",
            first.extra.get("kind") == "paper" and first.url.endswith("/papers/2609.77777"),
            f"kind={first.extra.get('kind')} url={first.url}",
        )
        suite.check(
            "6.30 HF upvotes 入 extra",
            first.extra.get("upvotes") == 42,
            f"upvotes={first.extra.get('upvotes')!r}",
        )
        suite.check(
            "6.31 HF 时间用入选日而非论文投稿日",
            first.published_at == datetime(2026, 9, 25, 1, 0, tzinfo=UTC)
            and first.extra.get("paper_published_at") == "2026-09-01T00:00:00.000Z",
            f"published_at={first.published_at} "
            f"paper_published_at={first.extra.get('paper_published_at')}",
        )
        second = next(item for item in items if "55555" in item.url)
        suite.check(
            "6.32 HF upvotes 字符串转 int",
            second.extra.get("upvotes") == 17,
            f"upvotes={second.extra.get('upvotes')!r}",
        )
        suite.check(
            "6.33 HF 入选日非法时回退当前时间",
            second.published_at is not None
            and second.published_at > datetime(2026, 9, 25, tzinfo=UTC),
            f"published_at={second.published_at}",
        )
        suite.check(
            "6.34 HF limit 参数透传（30）",
            fake.requests[0]["query"].get("limit") == "30",
            f"query={fake.requests[0]['query']}",
        )

    with FakeHTTP() as fake:
        fake.add_json("/api/daily_papers", {"unexpected": True})
        adapter = HfPapersAdapter(source("hf_papers", fake.url("/api/daily_papers")), http())
        try:
            adapter.fetch(SINCE)
            ok, detail = False, "未抛 SourceError"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("6.35 HF 非列表 payload 抛 SourceError", ok, detail)

    with FakeHTTP() as fake:
        fake.add_json("/api/daily_papers", [])
        adapter = HfPapersAdapter(source("hf_papers", fake.url("/api/daily_papers")), http())
        suite.check("6.36 HF 空列表返回空（不报错）", adapter.fetch(SINCE) == [], "items=[]")

    with FakeHTTP() as fake:
        fake.add_json("/api/daily_papers", [])
        adapter = HfPapersAdapter(
            source("hf_papers", fake.url("/api/daily_papers"), limit=999), http()
        )
        adapter.fetch(SINCE)
        suite.check(
            "6.37 HF limit 上限裁剪到 100",
            fake.requests[0]["query"].get("limit") == "100",
            f"query={fake.requests[0]['query']}",
        )

    with FakeHTTP() as fake:
        fake.add("/api/daily_papers", 429, "too many", "text/plain")
        adapter = HfPapersAdapter(source("hf_papers", fake.url("/api/daily_papers")), http())
        try:
            adapter.fetch(SINCE)
            ok, detail = False, "未抛 SourceError"
        except SourceError as exc:
            ok, detail = True, f"SourceError: {exc}"
        suite.check("6.38 HF HTTP 429 抛 SourceError（不再无限重试）", ok, detail)
        suite.check(
            "6.39 HF 适配器请求头 UA 为浏览器 UA",
            "Chrome/" in fake.requests[0]["headers"].get("User-Agent", DEFAULT_USER_AGENT),
            f"UA={fake.requests[0]['headers'].get('User-Agent', DEFAULT_USER_AGENT)[:40]}",
        )


def main() -> int:
    suite = Suite("m4_adapters")
    suite_arxiv(suite)
    suite_html(suite)
    suite_hf(suite)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
