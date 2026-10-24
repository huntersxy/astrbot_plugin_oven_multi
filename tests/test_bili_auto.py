"""Real SDK model switches take the proxy route on their first native request."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from test_bili_requests import completion, provider

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner

pytest_plugins = ["test_panel_retirement"]


@pytest.fixture
async def automatic(plugin):
    seen = []

    async def send(request):
        seen.append((str(request.url), dict(request.headers)))
        return httpx.Response(200, json=completion())

    instances = []
    for pid in ["first", "second", "fallback", "selector"]:
        p = provider("https://upstream.invalid/v1")
        await p.client.close()
        p.provider_config["id"] = pid
        p.client = AsyncOpenAI(
            api_key="synthetic-key",
            base_url="https://upstream.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(send)),
        )
        instances.append(p)
    host = {
        "provider": [deepcopy(p.provider_config) for p in instances],
        "agent_runner": {
            "config": {
                "model": {"provider_id": "first", "fallback_provider_ids": []},
                "compression": {"max_turns": 50, "overflow_strategy": "llm_compress"},
            }
        },
    }
    manager = SimpleNamespace(provider_insts=instances, selected="first")

    async def using(*args):
        return next(p for p in instances if p.provider_config["id"] == manager.selected)

    async def by_id(pid):
        return next(p for p in manager.provider_insts if p.provider_config["id"] == pid)

    manager.get_using_provider_async = using
    manager.get_provider_by_id = by_id
    plugin.context.provider_manager = manager
    plugin.context.get_config = lambda umo=None: host
    plugin.config["bili"] = {"enabled": True, "auto_follow": True, "provider_ids": ["first"]}
    plugin.bili_proxy.state = "external_ok"
    plugin.bili_proxy.session_header_state = "ok"
    plugin.bili_proxy.probe_health = AsyncMock(return_value=(True, ""))
    plugin.bili_proxy.verify_session_header = AsyncMock()
    plugin.bili_requests.install()
    event = SimpleNamespace(unified_msg_origin="room", get_extra=lambda *args: None)
    yield plugin, manager, host, instances, seen, event
    await plugin.terminate()
    for p in {id(p): p for p in [*instances, *manager.provider_insts]}.values():
        if hasattr(p, "client"):
            await p.client.close()


async def test_model_switch_follows_current_and_fallback_on_first_request(automatic):
    plugin, manager, host, instances, seen, event = automatic
    original = deepcopy(host)
    await plugin.bili_auto.follow(event)
    first_record = plugin.bili_wire.load_record()
    manager.selected = "second"
    host["agent_runner"]["config"]["model"].update(
        provider_id="second", fallback_provider_ids=["fallback"]
    )
    await asyncio.gather(*(plugin.bili_auto.follow(event) for _ in range(4)))
    record = plugin.bili_wire.load_record()
    assert set(record["providers"]) == {"first", "second", "fallback"}
    assert record["providers"]["first"] == first_record["providers"]["first"]
    assert record["compression"] == first_record["compression"]
    assert "/bili/" not in str(instances[3].client.base_url)
    for p in instances[1:3]:
        await p.text_chat(prompt="MAIN", conversation_id="same-native-dialog")
        assert seen[-1][0].startswith("http://127.0.0.1:8787/bili/")
        assert seen[-1][1]["x-astrbot-conversation-id"] == "same-native-dialog"
        assert "x-bili-passthrough" not in seen[-1][1]
        assert p.provider_config["max_context_tokens"] == 10000000
        await p.text_chat(prompt="HELPER")
        assert seen[-1][1]["x-bili-passthrough"] == "1"
    assert plugin.bili_wire.restore()["ok"]
    assert host["provider"] == original["provider"]
    assert (
        host["agent_runner"]["config"]["compression"]
        == original["agent_runner"]["config"]["compression"]
    )
    assert host["agent_runner"]["config"]["model"]["provider_id"] == "second"


@pytest.mark.parametrize("stop", ["disabled", "manual", "unhealthy", "header", "closing"])
async def test_auto_follow_respects_stop_conditions(automatic, stop):
    plugin, manager, host, instances, seen, event = automatic
    if stop == "disabled":
        plugin.config["bili"]["enabled"] = False
    elif stop == "manual":
        plugin.config["bili"]["auto_follow"] = False
    elif stop == "unhealthy":
        plugin.bili_proxy.state = "degraded"
    elif stop == "header":
        plugin.bili_proxy.session_header_state = "mismatch"
    else:
        plugin.closing = True
    before = deepcopy(host)
    await plugin.bili_auto.follow(event)
    assert host == before
    assert not plugin.bili_wire.record_exists()
    assert all("/bili/" not in str(p.client.base_url) for p in instances)


async def test_reloaded_provider_is_rebound_before_its_first_call(automatic):
    plugin, manager, host, instances, seen, event = automatic
    await plugin.bili_auto.follow(event)
    p = instances[0]
    await p.client.close()
    p.client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://upstream.invalid/v1",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=completion()))
        ),
    )
    assert await manager.get_provider_by_id("first") is p
    assert "/bili/" in str(p.client.base_url)
    await p.text_chat(prompt="MAIN_AFTER_RELOAD", conversation_id="unchanged-dialog")


async def test_explicit_session_selection_overrides_global_default(automatic):
    plugin, manager, host, instances, seen, event = automatic
    event.get_extra = lambda key: "second" if key == "selected_provider" else None
    await plugin.bili_auto.follow(event)
    assert set(plugin.bili_wire.load_record()["providers"]) == {"second"}
    assert "/bili/" not in str(instances[0].client.base_url)


async def test_native_hook_prepares_new_sdk_before_runner_reset(automatic):
    plugin, manager, host, instances, seen, event = automatic
    manager.selected = "second"
    request = ProviderRequest(
        prompt="当前消息",
        system_prompt="固定人格",
        contexts=[
            {"role": "user", "content": "历史消息"},
        ],
    )
    before = deepcopy(request)
    await plugin.on_llm_request(event, request)
    assert request == before
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=instances[1],
        request=request,
        run_context=SimpleNamespace(),
        tool_executor=SimpleNamespace(),
        agent_hooks=SimpleNamespace(),
    )
    assert runner.request_context_manager_config.max_context_tokens == 10000000
    await instances[1].text_chat(prompt=request.prompt, conversation_id="native-cid")
    assert "/bili/" in seen[-1][0]
    assert seen[-1][1]["x-astrbot-conversation-id"] == "native-cid"


async def test_startup_reuses_record_and_synchronizes_sdk_without_reload(automatic):
    plugin, manager, host, instances, seen, event = automatic
    assert plugin.bili_wire.wire(["first"], "127.0.0.1", 8787)["ok"]
    assert "/bili/" not in str(instances[0].client.base_url)
    plugin.bili_proxy.ensure_started = AsyncMock(
        return_value={
            "state": "external_ok",
            "detail": "",
            "healthy": True,
            "session_header_state": "ok",
        }
    )
    await plugin._bili_startup()
    assert "/bili/" in str(instances[0].client.base_url)
    assert not plugin._bili_doctor()


@pytest.mark.parametrize(
    "condition", ["corrupt", "unsupported", "late_disable", "paused", "probe_fail", "late_header"]
)
async def test_invalid_or_revoked_takeover_keeps_native_route(automatic, condition):
    plugin, manager, host, instances, seen, event = automatic
    if condition == "corrupt":
        plugin.bili_wire.record_path.write_text('{"invalid":true}')
    elif condition == "unsupported":
        manager.provider_insts = [SimpleNamespace(provider_config={"id": "first"})]
    elif condition == "late_disable":

        async def disable(umo):
            plugin.config["bili"]["enabled"] = False
            return "first"

        plugin._using_chat_provider_id = disable
    elif condition == "paused":
        plugin.bili_auto.paused = True
    elif condition == "probe_fail":
        plugin.bili_proxy.probe_health.return_value = (False, "offline")
    else:

        async def mismatch():
            plugin.bili_proxy.session_header_state = "mismatch"

        plugin.bili_proxy.verify_session_header.side_effect = mismatch
    before = deepcopy(host)
    await plugin.bili_auto.follow(event)
    assert host == before
    assert all("/bili/" not in str(p.client.base_url) for p in instances)


async def test_new_provider_instance_is_adapted_by_native_resolver(automatic):
    plugin, manager, host, instances, seen, event = automatic
    await plugin.bili_auto.follow(event)
    await instances[0].client.close()
    fresh = provider("https://upstream.invalid/v1")
    await fresh.client.close()
    fresh.provider_config["id"] = "first"
    calls = []

    async def send(r):
        calls.append(r)
        return httpx.Response(200, json=completion())

    fresh.client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://upstream.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(send)),
    )
    manager.provider_insts[0] = fresh
    assert await manager.get_provider_by_id("first") is fresh
    await fresh.text_chat(prompt="NEW_HELPER")
    assert "/bili/" in str(calls[-1].url)
    assert calls[-1].headers["x-bili-passthrough"] == "1"
