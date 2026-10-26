"""Plugin lifecycle and real local HTTP contracts; never call an LLM provider."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from test_panel_retirement import plugin_module

pytest_plugins = ["test_panel_retirement"]


@pytest.mark.asyncio
async def test_install_api_queues_once_and_honors_managed_mode(plugin):
    import asyncio

    plugin.config["bili"] = {"enabled": False, "auto_install": False}
    assert not (await plugin._api_billion_install())["success"]
    plugin.config["bili"].update(enabled=True, mode="external")
    assert not (await plugin._api_billion_install())["success"]
    plugin.config["bili"]["mode"] = "managed"
    finish = asyncio.Event()
    calls = []

    async def startup(*, install=False):
        calls.append(install)
        await finish.wait()

    plugin._bili_startup = startup
    assert (await plugin._api_billion_install())["success"]
    task = plugin._bili_task
    assert (await plugin._api_billion_install())["success"]
    assert plugin._bili_task is task
    assert (await plugin._api_billion_status())["data"]["busy"]
    await asyncio.sleep(0)
    assert calls == [True]
    assert plugin.config["bili"]["auto_install"] is False
    finish.set()
    await task
    assert not (await plugin._api_billion_status())["data"]["busy"]


@pytest.mark.asyncio
async def test_unrelated_full_form_save_does_not_cancel_installation(plugin, monkeypatch):
    import asyncio

    finish = asyncio.Event()
    plugin._bili_task = asyncio.create_task(finish.wait())
    task = plugin._bili_task
    try:
        form = (await plugin._api_settings())["data"]
        values = {f["key"]: f["value"] for g in form["groups"] for f in g["fields"]}
        assert values["bili.auto_install"] is True
        values["blacklist_groups"] = ["1001"]
        # Normalize the existing persisted form as AstrBot does at load time.
        plugin.config["bili"] = {
            key.split(".", 1)[1]: value for key, value in values.items()
            if key.startswith("bili.")
        }
        monkeypatch.setattr(
            plugin_module, "request",
            SimpleNamespace(body=AsyncMock(return_value=json.dumps({
                "revision": form["revision"], "values": values,
            }).encode())),
        )
        result = await plugin._api_settings_save()
        assert result["success"], result
        assert plugin._bili_task is task and not task.done()
    finally:
        finish.set()
        await task


@pytest.mark.asyncio
async def test_full_panel_save_with_native_request_and_disk_config(plugin, tmp_path, monkeypatch):
    from starlette.requests import Request

    from astrbot.api.web import PluginRequest, bind_request_context, request
    from astrbot.core.config.astrbot_config import AstrBotConfig

    path = tmp_path / "plugin.json"
    schema = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text("utf-8"))
    cfg = AstrBotConfig(str(path), schema=schema)
    cfg["jev"]["api_key"] = "keep-native-key"
    cfg["file_reader"]["rag"]["embedding_provider_id"] = "keep-embedding"
    cfg.save_config()
    plugin.config = cfg
    plugin._bili_revaluate = AsyncMock()
    monkeypatch.setattr(plugin_module, "request", request)
    form = (await plugin._api_settings())["data"]
    values = {f["key"]: f["value"] for g in form["groups"] for f in g["fields"]}
    assert len(values) == 86
    values.update(blacklist_groups=["1001", "1002"], blacklist_users=["2001"])
    values["bili.auto_install"] = False
    body = json.dumps({"revision": form["revision"], "values": values}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    raw_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/settings/save",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )
    with bind_request_context(PluginRequest(raw_request)):
        result = await plugin._api_settings_save()
    assert result["success"], result
    await plugin._bili_task
    reloaded = AstrBotConfig(str(path), schema=schema)
    assert reloaded["blacklist_groups"] == ["1001", "1002"]
    assert reloaded["blacklist_users"] == ["2001"]
    assert reloaded["jev"]["api_key"] == "keep-native-key"
    assert reloaded["file_reader"]["rag"]["embedding_provider_id"] == "keep-embedding"
    assert plugin.panel_settings.revision != form["revision"]


@pytest.mark.asyncio
async def test_native_config_write_failure_restores_lists_and_revision(
    plugin, tmp_path, monkeypatch
):
    from astrbot.core.config.astrbot_config import AstrBotConfig

    path = tmp_path / "plugin.json"
    cfg = AstrBotConfig(
        str(path),
        default_config={"blacklist_groups": ["old"], "jev": {"enabled": False, "api_key": "keep"}},
    )
    plugin.config = cfg
    form = plugin.panel_settings.describe(cfg)
    before = json.loads(json.dumps(dict(cfg)))
    disk_before = path.read_bytes()

    def rejected(*args, **kwargs):
        raise PermissionError("read only")

    monkeypatch.setattr(AstrBotConfig, "_write_config_snapshot", rejected)
    monkeypatch.setattr(
        plugin_module,
        "request",
        SimpleNamespace(
            body=AsyncMock(
                return_value=json.dumps(
                    {
                        "revision": form["revision"],
                        "values": {
                            "jev.enabled": True,
                            "blacklist_groups": ["new"],
                            "blacklist_users": ["added"],
                        },
                    }
                ).encode()
            )
        ),
    )
    result = await plugin._api_settings_save()
    assert not result["success"] and "配置目录" in result["message"]
    assert dict(cfg) == before
    assert path.read_bytes() == disk_before
    assert plugin.panel_settings.revision == form["revision"]


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


def test_status_reports_current_model_outside_existing_wire_scope(plugin):
    host = {
        "provider": [
            {"id": pid, "api_base": "https://example.invalid/v1"}
            for pid in ("original", "new-current")
        ],
        "agent_runner": {"config": {"model": {"provider_id": "new-current"}}},
    }
    plugin.context.get_config = lambda: host
    plugin.context.provider_manager = SimpleNamespace(
        provider_insts=[SimpleNamespace(provider_config=entry) for entry in host["provider"]]
    )
    assert plugin.bili_wire.wire(["original"], "127.0.0.1", 8787)["ok"]
    notices = plugin._bili_doctor()
    assert any("当前对话模型未接管（new-current）" in notice for notice in notices)
    assert plugin.bili_wire.record_summary()["providers"] == ["original"]
    assert host["provider"][1]["api_base"] == "https://example.invalid/v1"
