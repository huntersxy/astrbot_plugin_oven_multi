"""Offline regressions for style retirement and on-demand Panel APIs."""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
plugin_module = importlib.import_module(f"{ROOT.name}.main")
balance_module = importlib.import_module(f"{ROOT.name}.features.balance_checker")


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """Build a real plugin with a local context and disabled external features.

    Args:
        tmp_path: Isolated plugin data directory.
        monkeypatch: Scoped runtime overrides.

    Returns:
        Plugin using real AstrBot hooks without starting a server.
    """
    monkeypatch.setattr(plugin_module.StarTools, "get_data_dir", lambda *_: tmp_path)
    context = SimpleNamespace(
        registered_web_apis=[
            (f"/{ROOT.name}/style_status", lambda: {}, ["GET"], "retired"),
            (f"/{ROOT.name}/style/manage", lambda: {}, ["POST"], "retired"),
        ]
    )
    context.register_web_api = lambda *args: context.registered_web_apis.append(args)
    config = {
        "style_learning": {"enabled": True, "enable_style_injection": True},
        "mention_parser": {"enabled": False},
        "file_reader": {"enabled": False},
        "jev": {"enabled": False},
    }
    return plugin_module.OvenMultiPlugin(context, config)


@pytest.mark.asyncio
async def test_retirement_preserves_native_prompt_and_legacy_files(plugin):
    legacy = plugin.data_dir / "universal.json"
    history = plugin.data_dir / "chat_history.json"
    legacy.write_text('{"old": "keep"}', encoding="utf-8")
    history.write_text('["keep"]', encoding="utf-8")
    original = "System prefix\n## Active Speakers\nOriginal host text"
    req = SimpleNamespace(
        system_prompt=original,
        contexts=[{"role": "user", "content": "old"}],
        extra_user_content_parts=[],
    )
    contexts = req.contexts.copy()
    event = SimpleNamespace(get_extra=lambda key, default=None: default)
    await plugin.initialize()
    await plugin.on_llm_request(event, req)
    await plugin.terminate()
    assert req.system_prompt == original
    assert req.contexts == contexts
    assert req.extra_user_content_parts == []
    assert legacy.read_text("utf-8") == '{"old": "keep"}'
    assert history.read_text("utf-8") == '["keep"]'
    assert not hasattr(plugin, "style")
    assert "style_learning" not in json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
    assert not list((ROOT / "features" / "learning_style").glob("*.py"))


@pytest.mark.asyncio
async def test_status_never_queries_balances_and_does_not_expose_keys(plugin):
    plugin.config["jev"] = {"enabled": True, "api_key": "synthetic-secret"}
    plugin.config["balance"] = {"config_content": "synthetic-secret"}
    plugin.balance_checker.query_all = AsyncMock(side_effect=AssertionError("Unexpected provider"))
    payload = await plugin._api_status()
    ids = [row["id"] for row in payload["data"]["features"]]
    assert len(ids) == 9
    assert "segment_reply" in ids and "bili" in ids and "active_reply" not in ids
    assert payload["data"]["balance_configured"] is True
    assert "synthetic-secret" not in json.dumps(payload)
    plugin.balance_checker.query_all.assert_not_called()
    # active_reply 墓碑已清理：schema 不再有该节，状态页也不再给迁移提示
    schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
    assert "active_reply" not in schema
    assert "主动回复已移除" not in "\n".join(payload["data"]["notices"])


@pytest.mark.asyncio
async def test_segment_reply_defaults_off_and_non_llm_gate(plugin):
    """分段回复默认关闭；schema 中存在且默认 process_non_llm=false。"""
    schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
    seg = schema["segment_reply"]
    assert seg["items"]["enabled"]["default"] is False
    assert seg["items"]["process_non_llm"]["default"] is False
    # 关闭时装饰钩子直接返回，不改动结果链
    import astrbot.api.message_components as components

    result = SimpleNamespace(chain=[components.Plain("很长的一段话" * 20)])
    event = SimpleNamespace(
        get_result=lambda: result,
        get_platform_name=lambda: "aiocqhttp",
        set_extra=lambda *a: None,
        get_extra=lambda *a, **k: None,
    )
    result.is_llm_result = lambda: True
    result.result_content_type = object()
    await plugin.segment_reply_decorate(event)
    assert len(result.chain) == 1


@pytest.mark.asyncio
async def test_only_native_routes_and_scoped_cleanup(plugin):
    routes = plugin.context.registered_web_apis
    assert [row[0].replace(f"/{ROOT.name}/", "") for row in routes] == [
        "billion/dashboard",
        "status",
        "balance",
        "settings",
        "settings/save",
        "billion/status",
        "billion/wire",
        "billion/restore",
    ]
    assert [row[2] for row in routes] == [
        ["GET"], ["GET"], ["GET"], ["GET"], ["POST"], ["GET"], ["POST"], ["POST"],
    ]
    unrelated = ("/other", lambda: {}, ["GET"], "other plugin")
    routes.append(unrelated)
    await plugin.terminate()
    assert routes == [unrelated]


@pytest.mark.asyncio
async def test_coalesced_queries_cached_copies_and_config_invalidation():
    config = {"balance": {"config_content": "services: {}"}}
    checker = balance_module.BalanceChecker(config)
    checker._query_yaml = AsyncMock(return_value=[{"name": "A", "value": "1", "success": True}])
    rows = await asyncio.gather(*(checker.query_all() for _ in range(4)))
    assert checker._query_yaml.await_count == 1
    rows[0][0]["value"] = "modified"
    assert (await checker.query_all())[0]["value"] == "1"
    config["balance"]["config_content"] = "services: {A: {}}"
    await checker.query_all()
    assert checker._query_yaml.await_count == 2
    config["balance"]["config_content"] = ""
    assert await checker.query_all() == []
    assert checker.checked_at is None


@pytest.mark.asyncio
async def test_cache_expires_and_all_failures_can_retry(monkeypatch):
    checker = balance_module.BalanceChecker({"balance": {"config_content": "services: {}"}})
    now = [100.0]
    monkeypatch.setattr(balance_module.time, "monotonic", lambda: now[0])
    checker._query_yaml = AsyncMock(return_value=[{"name": "A", "value": "ok", "success": True}])
    await checker.query_all()
    now[0] += 31
    await checker.query_all()
    assert checker._query_yaml.await_count == 2
    checker._cache_key = None
    checker._query_yaml.return_value = [{"name": "A", "value": "failed", "success": False}]
    await checker.query_all()
    await checker.query_all()
    assert checker._query_yaml.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content", ["services: [not, a, mapping]", "[invalid-root]", "services: {a: [}"]
)
async def test_invalid_balance_configuration_fails_safely(content):
    checker = balance_module.BalanceChecker({"balance": {"config_content": content}})
    rows = await checker.query_all()
    assert rows and all(row["success"] is False for row in rows)
    assert checker.session is None


@pytest.mark.asyncio
async def test_malformed_service_and_exception_never_leak_credentials():
    checker = balance_module.BalanceChecker({"balance": {"config_content": "services: {}"}})
    row = await checker._handle_yaml_service("A", "not-a-mapping")
    assert row["success"] is False
    checker._query_yaml = AsyncMock(side_effect=RuntimeError("synthetic-secret in URL"))
    assert "synthetic-secret" not in json.dumps(await checker.query_all())


def test_versions_native_view_and_provider_templates_remain_consistent():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text("utf-8"))
    assert metadata["version"] == plugin_module.PLUGIN_VERSION == "1.56.0"
    assert metadata["views"] == [{"name": "status", "title": "状态总览"}]
    template = balance_module._BUILTIN_PARSERS["minimax"]["result_template"]
    checker = balance_module.BalanceChecker({})
    rendered = checker._render_template(
        template,
        {
            "model_remains": [
                {
                    "current_interval_total_count": 100,
                    "current_interval_usage_count": 30,
                    "current_weekly_total_count": 1000,
                    "current_weekly_usage_count": 250,
                }
            ]
        },
    )
    assert rendered == "MiniMax: 剩余 70/100 (70%), 本周 750/1000"
