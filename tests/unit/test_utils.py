"""utils 模块单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aihot.utils import (
    clean_html,
    detect_lang,
    mask_url_credentials,
    normalize_url,
    parse_datetime,
    redact_text,
    url_hash,
    within_window,
)


class TestNormalizeUrl:
    def test_removes_tracking_params(self) -> None:
        assert normalize_url("https://example.com/a?utm_source=wx&id=1") == "https://example.com/a?id=1"

    def test_removes_fragment(self) -> None:
        assert normalize_url("https://example.com/a#section") == "https://example.com/a"

    def test_lowercases_host_keeps_path_case(self) -> None:
        assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_strips_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/a/") == "https://example.com/a"
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_sorts_query_params(self) -> None:
        assert normalize_url("https://e.com/x?b=2&a=1") == "https://e.com/x?a=1&b=2"

    def test_default_port_removed(self) -> None:
        assert normalize_url("https://example.com:443/a") == "https://example.com/a"

    def test_empty(self) -> None:
        assert normalize_url("") == ""
        assert normalize_url("   ") == ""


class TestUrlHash:
    def test_equivalent_urls_same_hash(self) -> None:
        assert url_hash("https://Example.com/post/?utm_source=x") == url_hash("https://example.com/post")

    def test_different_urls_different_hash(self) -> None:
        assert url_hash("https://example.com/a") != url_hash("https://example.com/b")


class TestWithinWindow:
    def test_recent_is_true(self) -> None:
        now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
        assert within_window(now - timedelta(hours=1), 24, now=now) is True

    def test_old_is_false(self) -> None:
        now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
        assert within_window(now - timedelta(hours=25), 24, now=now) is False

    def test_boundary_inclusive(self) -> None:
        now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
        assert within_window(now - timedelta(hours=24), 24, now=now) is True

    def test_none_is_false(self) -> None:
        assert within_window(None, 24) is False


class TestParseDatetime:
    def test_iso_with_timezone(self) -> None:
        parsed = parse_datetime("2026-09-23T08:00:00+08:00")
        assert parsed == datetime(2026, 9, 23, 0, 0, tzinfo=UTC)

    def test_rfc822(self) -> None:
        parsed = parse_datetime("Wed, 23 Sep 2026 09:57:23 +0000")
        assert parsed == datetime(2026, 9, 23, 9, 57, 23, tzinfo=UTC)

    def test_naive_treated_as_utc(self) -> None:
        parsed = parse_datetime("2026-09-23 08:00:00")
        assert parsed == datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    def test_garbage_returns_none(self) -> None:
        assert parse_datetime("not-a-date") is None
        assert parse_datetime(None) is None


class TestCleanHtml:
    def test_strips_tags(self) -> None:
        assert clean_html("<p>hello <b>world</b></p>") == "hello world"

    def test_drops_script_and_style(self) -> None:
        text = clean_html("<style>p{color:red}</style><script>alert(1)</script><p>正文</p>")
        assert text == "正文"

    def test_truncates(self) -> None:
        assert len(clean_html("x" * 5000, limit=100)) == 100


class TestRedactText:
    def test_known_secret_replaced(self) -> None:
        secret = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123456789"
        assert "abc123456789" not in redact_text(f"hook={secret}", [secret])

    def test_key_pattern_replaced(self) -> None:
        assert redact_text("https://x.com/api?key=abcdef123456") == "https://x.com/api?key=***"

    def test_sk_pattern_replaced(self) -> None:
        assert redact_text("token: sk-abcdefghijklmn") == "token: sk-***"


class TestMaskUrlCredentials:
    def test_hides_userinfo(self) -> None:
        assert mask_url_credentials("http://user:pass@127.0.0.1:7890") == "http://***@127.0.0.1:7890"

    def test_keeps_url_without_userinfo(self) -> None:
        assert mask_url_credentials("http://127.0.0.1:7890") == "http://127.0.0.1:7890"

    def test_empty(self) -> None:
        assert mask_url_credentials("") == ""

    def test_invalid_ipv6_does_not_crash(self) -> None:
        assert mask_url_credentials("http://[::1") == "***"


class TestDetectLang:
    def test_chinese(self) -> None:
        assert detect_lang("量子位发布新一代模型") == "zh"

    def test_english(self) -> None:
        assert detect_lang("OpenAI releases GPT-6") == "en"

    def test_mixed_counts_as_chinese(self) -> None:
        assert detect_lang("GPT-6 发布") == "zh"

    def test_empty_is_english(self) -> None:
        assert detect_lang("") == "en"
