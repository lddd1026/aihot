"""config 模块单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aihot.config import ConfigError, load_config, validate

VALID = """
app:
  timezone: Asia/Shanghai
  window_hours: 24
  top_n: 12
  log_level: INFO
  data_dir: data
  log_dir: logs

llm:
  base_url: https://api.deepseek.com
  model: deepseek-chat
  api_key: sk-test-1234567890
  batch_size: 15
  daily_cost_limit_cny: 1.0

proxy:
  enabled: false
  url: ""

push:
  wecom:
    enabled: true
    webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123456789
  email:
    enabled: false

sources:
  - id: qbitai
    name: 量子位
    type: rss
    url: https://www.qbitai.com/feed
    enabled: true
    weight: 1.0
  - id: hn
    name: Hacker News
    type: hackernews
    url: https://hn.algolia.com/api/v1/search_by_date
    enabled: false
    weight: 0.8
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, VALID))
    assert cfg.app.timezone == "Asia/Shanghai"
    assert cfg.app.top_n == 12
    assert cfg.app.window_hours == 24
    assert len(cfg.sources) == 2
    assert [source.id for source in cfg.enabled_sources] == ["qbitai"]
    assert cfg.push.wecom.enabled is True
    assert cfg.app.data_dir == (tmp_path / "data").resolve()
    assert cfg.warnings == ()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path / "nope.yaml")
    assert "配置文件不存在" in str(exc_info.value)


def test_invalid_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, "a: [1, 2\n"))
    assert "YAML 解析失败" in str(exc_info.value)


def test_top_level_not_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, "- a\n- b\n"))
    assert "顶层必须是映射" in str(exc_info.value)


def test_duplicate_source_id(tmp_path: Path) -> None:
    text = VALID.replace("id: hn", "id: qbitai")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("id 重复" in issue for issue in exc_info.value.issues)


def test_invalid_source_url(tmp_path: Path) -> None:
    text = VALID.replace("https://www.qbitai.com/feed", "www.qbitai.com/feed")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("url 必须是 http(s) 地址" in issue for issue in exc_info.value.issues)


def test_unknown_source_type(tmp_path: Path) -> None:
    text = VALID.replace("type: hackernews", "type: rss2")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("type 不支持" in issue for issue in exc_info.value.issues)


def test_wecom_enabled_without_webhook(tmp_path: Path) -> None:
    text = VALID.replace(
        "    webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123456789\n",
        "",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("未配置 webhook" in issue for issue in exc_info.value.issues)


def test_email_enabled_missing_credentials(tmp_path: Path) -> None:
    text = VALID.replace("  email:\n    enabled: false", "  email:\n    enabled: true")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    issues = " ".join(exc_info.value.issues)
    assert "smtp_host" in issues
    assert "password" in issues
    assert "to_addrs" in issues


def test_invalid_window_hours(tmp_path: Path) -> None:
    text = VALID.replace("window_hours: 24", "window_hours: 0")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("window_hours" in issue for issue in exc_info.value.issues)


def test_boolean_is_not_valid_int(tmp_path: Path) -> None:
    text = VALID.replace("top_n: 12", "top_n: true")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("top_n" in issue for issue in exc_info.value.issues)


def test_invalid_timezone(tmp_path: Path) -> None:
    text = VALID.replace("Asia/Shanghai", "Mars/Olympus", 1)
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("timezone" in issue for issue in exc_info.value.issues)


@pytest.mark.parametrize("bad_clock", ["17:60", "25:00", "1730", "abc", "17-30"])
def test_invalid_deliver_by_rejected(tmp_path: Path, bad_clock: str) -> None:
    text = VALID.replace(
        "  timezone: Asia/Shanghai",
        f'  timezone: Asia/Shanghai\n  deliver_by: "{bad_clock}"',
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("deliver_by" in issue for issue in exc_info.value.issues)


def test_valid_deliver_by_accepted(tmp_path: Path) -> None:
    text = VALID.replace(
        "  timezone: Asia/Shanghai", '  timezone: Asia/Shanghai\n  deliver_by: "17:30"'
    )
    cfg = load_config(write_config(tmp_path, text))
    assert cfg.app.deliver_by == "17:30"


def test_proxy_enabled_without_url(tmp_path: Path) -> None:
    text = VALID.replace("proxy:\n  enabled: false", "proxy:\n  enabled: true")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("proxy.url 为空" in issue for issue in exc_info.value.issues)


def test_llm_key_missing_is_warning(tmp_path: Path) -> None:
    text = VALID.replace("  api_key: sk-test-1234567890\n", "")
    cfg = load_config(write_config(tmp_path, text))
    assert any("api_key 未配置" in warning for warning in cfg.warnings)


def test_secret_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = VALID.replace(
        "    webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123456789",
        "    webhook: ''\n    webhook_env: TEST_WECOM_HOOK",
    )
    monkeypatch.setenv(
        "TEST_WECOM_HOOK",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fromenv12345",
    )
    cfg = load_config(write_config(tmp_path, text))
    assert cfg.push.wecom.webhook.endswith("key=fromenv12345")


def test_secrets_collected(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, VALID))
    secrets = cfg.secrets()
    assert any("abc123456789" in secret for secret in secrets)
    assert any("sk-test-1234567890" in secret for secret in secrets)


# ---------- 坏 YAML 不得回显原文（P0 回归） ----------


def test_yaml_error_does_not_leak_content(tmp_path: Path) -> None:
    secret = "SECRETKEY0123456789abcdef"
    text = (
        "push:\n"
        "  wecom:\n"
        "    webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
        f"{secret}: oops\n"
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    message = " ".join(exc_info.value.issues)
    assert "YAML 解析失败" in message
    assert secret not in message
    assert "已隐藏原文" in message
    assert "行" in message


def test_yaml_error_unclosed_bracket_has_position(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, "sources: [1, 2\n"))
    message = " ".join(exc_info.value.issues)
    assert "YAML 解析失败" in message
    assert "行" in message


# ---------- 严格类型校验（P2 回归） ----------


def _raw_valid() -> dict:
    return yaml.safe_load(VALID)


def test_string_bool_rejected_for_source() -> None:
    raw = _raw_valid()
    raw["sources"][0]["enabled"] = "false"
    errors, _ = validate(raw)
    assert any("布尔值" in issue and "enabled" in issue for issue in errors)


def test_string_bool_rejected_for_proxy() -> None:
    raw = _raw_valid()
    raw["proxy"]["enabled"] = "true"
    errors, _ = validate(raw)
    assert any("proxy.enabled" in issue and "布尔值" in issue for issue in errors)


def test_smtp_port_type_error_even_when_email_disabled() -> None:
    raw = _raw_valid()
    raw["push"]["email"] = {"enabled": False, "smtp_port": "abc"}
    errors, _ = validate(raw)
    assert any("smtp_port" in issue for issue in errors)


def test_extra_must_be_mapping() -> None:
    raw = _raw_valid()
    raw["sources"][0]["extra"] = "not-a-dict"
    errors, _ = validate(raw)
    assert any("extra 必须是映射" in issue for issue in errors)


def test_data_dir_must_be_string() -> None:
    raw = _raw_valid()
    raw["app"]["data_dir"] = 123
    errors, _ = validate(raw)
    assert any("data_dir" in issue for issue in errors)


def test_to_addrs_must_be_string_list() -> None:
    raw = _raw_valid()
    raw["push"]["email"] = {"enabled": False, "to_addrs": "a@b.com"}
    errors, _ = validate(raw)
    assert any("to_addrs 必须是邮箱字符串列表" in issue for issue in errors)


def test_data_dir_pointing_to_file_is_config_error(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("i am a file", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, VALID))
    assert any("指向已存在的文件" in issue for issue in exc_info.value.issues)


def test_llm_section_missing_required_fields(tmp_path: Path) -> None:
    text = VALID.replace("  base_url: https://api.deepseek.com\n", "")
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert any("base_url" in issue for issue in exc_info.value.issues)


# ---------- proxy.url 严格校验（P1 回归） ----------


def _with_proxy(url: str) -> str:
    return VALID.replace(
        "proxy:\n  enabled: false\n  url: \"\"",
        f'proxy:\n  enabled: true\n  url: "{url}"',
    )


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:abc",
        "socks5://127.0.0.1:abc",
        "http://[::1",
        "http://:7890",
        "http://127.0.0.1:99999",
        "ftp://127.0.0.1:7890",
        "127.0.0.1:7890",
    ],
)
def test_invalid_proxy_url_is_config_error(tmp_path: Path, bad_url: str) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, _with_proxy(bad_url)))
    assert any("proxy.url" in issue for issue in exc_info.value.issues)


@pytest.mark.parametrize(
    "good_url",
    ["http://127.0.0.1:7890", "socks5://127.0.0.1:1080", "https://proxy.example.com:443"],
)
def test_valid_proxy_url_accepted(tmp_path: Path, good_url: str) -> None:
    cfg = load_config(write_config(tmp_path, _with_proxy(good_url)))
    assert cfg.proxy.enabled is True
    assert cfg.proxy.url == good_url


# ---------- pipeline 配置（M2） ----------


def test_pipeline_defaults(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, VALID))
    assert cfg.app.deliver_by == "08:00"
    assert cfg.pipeline.prefilter.gate_sources == ("hackernews",)
    assert cfg.pipeline.dedup.title_similarity == 88.0
    assert cfg.pipeline.dedup.max_hours_apart == 72
    assert cfg.pipeline.dedup.llm_refine is False
    assert cfg.pipeline.enrich.max_items == 200
    assert cfg.pipeline.enrich.time_budget_seconds == 600
    assert cfg.pipeline.prefilter.use_builtin_blacklist is True
    assert cfg.pipeline.prefilter.use_builtin_exclude is True
    assert cfg.pipeline.prefilter.exclude_keywords == ()
    assert cfg.pipeline.rank.interest_bonus["模型发布"] == 10.0
    assert cfg.pipeline.rank.interest_bonus["行业动态"] == -20.0
    assert cfg.source_weights["qbitai"] == 1.0


def test_pipeline_custom_values(tmp_path: Path) -> None:
    text = VALID + """
pipeline:
  prefilter:
    gate_sources: [hackernews, infoq_cn]
    extra_ai_keywords: ["\\\\bfoo\\\\b"]
  dedup:
    title_similarity: 60
    max_hours_apart: 12
    llm_refine: false
  enrich:
    max_items: 50
    price_input_per_million: 1.0
    price_output_per_million: 4.0
  rank:
    interest_bonus:
      模型发布: 10
"""
    cfg = load_config(write_config(tmp_path, text))
    assert cfg.pipeline.prefilter.gate_sources == ("hackernews", "infoq_cn")
    assert cfg.pipeline.dedup.title_similarity == 60.0
    assert cfg.pipeline.dedup.llm_refine is False
    assert cfg.pipeline.enrich.max_items == 50
    assert cfg.pipeline.rank.interest_bonus == {"模型发布": 10.0}


@pytest.mark.parametrize(
    "snippet",
    [
        "  dedup:\n    title_similarity: 200",
        "  dedup:\n    max_hours_apart: 0",
        "  dedup:\n    llm_refine: \"yes\"",
        "  enrich:\n    max_items: 0",
        "  enrich:\n    price_input_per_million: -1",
        "  rank:\n    interest_bonus:\n      模型发布: 很多",
        "  prefilter:\n    gate_sources: hackernews",
    ],
)
def test_pipeline_invalid_values_rejected(tmp_path: Path, snippet: str) -> None:
    text = VALID + "\npipeline:\n" + snippet + "\n"
    with pytest.raises(ConfigError) as exc_info:
        load_config(write_config(tmp_path, text))
    assert exc_info.value.issues
