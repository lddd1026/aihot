"""M4 修复回归测试：企微 key 语义、编排层异常告警、dry-run 回落、探测参数。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from aihot import checks, runner
from aihot.config import Config, load_config
from aihot.models import NewsItem
from aihot.runner import EnrichOutcome, FetchOutcome

CONFIG_TEMPLATE = """
app:
  timezone: Asia/Shanghai
  data_dir: "{data_dir}"
  log_dir: "{log_dir}"
  top_n: 3
llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
proxy:
  enabled: false
push:
  wecom:
    enabled: true
    webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake-key-123456"
  email:
    enabled: false
sources:
  - id: s1
    type: rss
    url: https://example.com/feed
    enabled: true
    weight: 1.0
"""


def load_cfg(tmp_path: Path) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_TEMPLATE.format(
            data_dir=(tmp_path / "data").as_posix(),
            log_dir=(tmp_path / "logs").as_posix(),
        ),
        encoding="utf-8",
    )
    return load_config(path)


def make_news(index: int = 1) -> NewsItem:
    return NewsItem(
        source_id="s1",
        title=f"Title {index}",
        url=f"https://example.com/{index}",
        published_at=datetime.now(UTC),
        cluster_id=index,
        category="模型发布",
        importance=80,
        cn_title=f"中文标题 {index}",
        cn_summary="摘要",
        extra={"mention_count": 1, "source_ids": ["s1"]},
    )


# ---------- P1-1：企微 key 语义 ----------


class TestWeComProbe:
    def test_invalid_key_detected(self, tmp_path: Path, monkeypatch) -> None:
        cfg = load_cfg(tmp_path)

        def fake_get(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={"errcode": 93000, "errmsg": "invalid webhook url"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(checks.httpx, "get", fake_get)
        result = checks.probe_wecom(cfg)
        assert result is not None
        assert result.ok is False
        assert "失效" in result.detail

    def test_get_with_valid_key_passes(self, tmp_path: Path, monkeypatch) -> None:
        """有效 key + GET 会返回 93017（请求体非法），这属于正常。"""
        cfg = load_cfg(tmp_path)

        def fake_get(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={"errcode": 93017, "errmsg": "invalid json request"},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(checks.httpx, "get", fake_get)
        result = checks.probe_wecom(cfg)
        assert result is not None
        assert result.ok is True

    def test_non_json_response_still_reachable(self, tmp_path: Path, monkeypatch) -> None:
        cfg = load_cfg(tmp_path)

        def fake_get(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(200, text="<html>ok</html>", request=httpx.Request("GET", url))

        monkeypatch.setattr(checks.httpx, "get", fake_get)
        result = checks.probe_wecom(cfg)
        assert result is not None
        assert result.ok is True

    def test_json_array_response_does_not_crash(self, tmp_path: Path, monkeypatch) -> None:
        """P2 回归：响应是 JSON 数组时不应抛 AttributeError。"""
        cfg = load_cfg(tmp_path)

        def fake_get(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3], request=httpx.Request("GET", url))

        monkeypatch.setattr(checks.httpx, "get", fake_get)
        result = checks.probe_wecom(cfg)
        assert result is not None
        assert result.ok is True


# ---------- P1-2：编排层异常必须告警 ----------


class TestRunAllFailures:
    def _stub_stages(self, monkeypatch) -> list[tuple[str, list[str]]]:
        alerts: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(
            runner, "fetch_stage", lambda *args, **kwargs: FetchOutcome(stats=[])
        )
        monkeypatch.setattr(
            runner,
            "enrich_stage",
            lambda *args, **kwargs: EnrichOutcome(
                pending=1, kept=1, clusters=1, news_items=[make_news()], summary="综述"
            ),
        )
        monkeypatch.setattr(
            runner,
            "_alert",
            lambda cfg, title, details: alerts.append((title, list(details))),
        )
        return alerts

    def test_storage_error_alerts_and_marks_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = load_cfg(tmp_path)
        alerts = self._stub_stages(monkeypatch)
        # 让 digests 路径是文件 → 写盘失败（模拟文件被占用）
        digest_dir = cfg.app.data_dir / "digests"
        digest_dir.parent.mkdir(parents=True, exist_ok=True)
        digest_dir.write_text("i am a file", encoding="utf-8")

        outcome = runner.run_all(cfg, dry_run=True)

        assert outcome.status == "failed"
        assert alerts, "渲染/存档失败必须触发告警"
        assert outcome.errors

    def test_render_error_alerts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = load_cfg(tmp_path)
        alerts = self._stub_stages(monkeypatch)

        def boom(*args: object, **kwargs: object):
            raise runner.RenderError("模拟长图渲染失败")

        monkeypatch.setattr(runner, "render_outputs", boom)
        outcome = runner.run_all(cfg, dry_run=True)
        assert outcome.status == "failed"
        assert any("渲染" in title for title, _ in alerts)


# ---------- P2：dry-run 无新条目时回落到库 ----------


def test_build_digest_falls_back_to_db_when_dry_run_has_no_items(tmp_path: Path) -> None:
    from aihot.presentation.compose import local_date
    from aihot.store import Store

    cfg = load_cfg(tmp_path)
    # 库里放一条已加工条目
    news = make_news()
    with Store(cfg.app.data_dir / "aihot.db") as store:
        from aihot.models import RawItem

        raw = RawItem(
            source_id=news.source_id,
            title=news.title,
            url=news.url,
            published_at=news.published_at,
        )
        store.save_items([raw])
        store.mark_enriched(
            [
                (
                    raw.source_id,
                    raw.url_hash,
                    news.cluster_id,
                    news.category,
                    news.importance,
                    news.cn_title,
                    news.cn_summary,
                    "{}",
                )
            ]
        )

    outcome = EnrichOutcome(pending=0, news_items=[], persisted=False)
    today = local_date(datetime.now(UTC), cfg.app.timezone)
    digest = runner.build_digest(cfg, outcome, digest_date=today)
    assert [item.cn_title for item in digest.top_items] == ["中文标题 1"]


# ---------- P2：源探测参数 ----------


def test_probe_params_for_arxiv() -> None:
    from aihot.config import SourceConfig

    arxiv = SourceConfig(
        id="arxiv",
        name="arXiv",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        enabled=True,
        weight=1.0,
    )
    rss = SourceConfig(
        id="rss",
        name="RSS",
        type="rss",
        url="https://example.com/feed",
        enabled=True,
        weight=1.0,
    )
    assert checks._probe_params(arxiv) == {  # noqa: SLF001
        "search_query": "cat:cs.AI",
        "max_results": "1",
    }
    assert checks._probe_params(rss) is None  # noqa: SLF001


def test_wecom_part_cap_is_visible(tmp_path: Path) -> None:
    from aihot.presentation import render_text

    parts = [f"第 {index} 片" for index in range(1, 20)]
    capped = render_text._cap_parts(parts)  # noqa: SLF001
    assert len(capped) == render_text.MAX_WECOM_PARTS
    assert "省略" in capped[-1]


def test_digest_helpers_are_date_independent() -> None:
    from aihot.presentation.compose import date_display, issue_number

    assert date_display(date(2026, 9, 25)) == "2026年9月25日 星期五"
    assert issue_number(date(2026, 9, 25)) == 3
