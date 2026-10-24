"""Plugin lifecycle and real local HTTP contracts; never call an LLM provider."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from test_panel_retirement import plugin_module

pytest_plugins = ["test_panel_retirement"]


@pytest.mark.asyncio
async def test_http_health_and_upstream_raw_config(plugin):
    valid = {"ok": True}

    async def health(_):
        return web.json_response(valid)

    async def config(_):
        return web.json_response(
            {"path": "test.json", "raw": json.dumps({"sessionHeader": "x-astrbot-conversation-id"})}
        )

    app = web.Application()
    app.router.add_get("/__bili/health", health)
    app.router.add_get("/__bili/config", config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    plugin.config["bili"] = {"host": "127.0.0.1", "port": port, "mode": "external"}
    try:
        status = await plugin.bili_proxy.ensure_started()
        assert status["healthy"] and status["session_header_state"] == "ok"
        valid["ok"] = False
        assert not (await plugin.bili_proxy.probe_health())[0]
    finally:
        await plugin.bili_proxy.stop()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_manual_api_and_command_cannot_bypass_session_validation(plugin, monkeypatch):
    monkeypatch.setattr(
        plugin_module, "request", SimpleNamespace(body=AsyncMock(return_value=b'{"confirm":true}'))
    )
    plugin.bili_proxy.probe_health = AsyncMock(return_value=(True, ""))
    plugin.bili_proxy.verify_session_header = AsyncMock()
    plugin.bili_proxy.session_header_state = "mismatch"
    plugin.bili_wire.wire = lambda *args, **kwargs: pytest.fail("Must not wire")
    result = await plugin._api_billion_wire()
    assert not result["success"]
    event = SimpleNamespace(message_str="烤箱压缩接管 确认", plain_result=lambda text: text)
    messages = [message async for message in plugin.bili_wire_cmd(event)]
    assert len(messages) == 1 and "拒绝接管" in messages[0]


@pytest.mark.asyncio
async def test_unload_restores_config_without_stopping_live_sdk_dependency(plugin):
    class Process:
        returncode = None

        def terminate(self):
            pytest.fail("A live SDK still depends on this process")

    host = {
        "provider": [{"id": "main", "api_base": "https://example.invalid/v1"}],
        "agent_runner": {"config": {"compression": {"max_turns": 20}}},
    }
    plugin.context.get_config = lambda: host
    assert plugin.bili_wire.wire(["main"], "127.0.0.1", 8787)["ok"]
    plugin.bili_proxy._proc = Process()
    plugin.context.provider_manager = SimpleNamespace(
        provider_insts=[
            SimpleNamespace(
                provider_config={"id": "main", "api_base": "https://example.invalid/v1"},
                client=SimpleNamespace(base_url=host["provider"][0]["api_base"] + "/"),
            )
        ]
    )
    await plugin.terminate()
    assert host["provider"][0]["api_base"] == "https://example.invalid/v1"
    assert not plugin.bili_wire.record_exists()
    assert plugin.bili_proxy._proc.returncode is None


def test_auto_detection_does_not_guess_first_of_multiple_providers(plugin):
    plugin.context.get_config = lambda: {"agent_runner": {"config": {"model": {}}}}
    plugin.context.provider_manager = SimpleNamespace(
        provider_insts=[
            SimpleNamespace(provider_config={"id": pid, "api_base": "https://example.invalid/v1"})
            for pid in ("one", "two")
        ]
    )
    assert plugin._sync_using_provider_id() is None
