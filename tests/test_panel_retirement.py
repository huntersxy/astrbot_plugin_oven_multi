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
    assert len(payload["data"]["features"]) == 8
    assert payload["data"]["balance_configured"] is True
    assert "synthetic-secret" not in json.dumps(payload)
    plugin.balance_checker.query_all.assert_not_called()


@pytest.mark.asyncio
async def test_only_native_read_routes_and_scoped_cleanup(plugin):
    routes = plugin.context.registered_web_apis
    assert [row[0].rsplit("/", 1)[-1] for row in routes] == ["status", "balance"]
    assert all(row[2] == ["GET"] for row in routes)
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
    assert metadata["version"] == plugin_module.PLUGIN_VERSION == "1.49.0"
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
