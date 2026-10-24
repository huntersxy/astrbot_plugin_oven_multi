"""Real SDK requests: helper isolation, native conversation IDs and safe unload."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

pytest_plugins = ["test_panel_retirement"]
PASSTHROUGH_HEADER = "x-bili-passthrough"


class Manager:
    def __init__(self, provider):
        self.provider_insts = [provider]

    async def get_provider_by_id(self, provider_id):
        return self.provider_insts[0]

    async def get_using_provider_async(self, *args, **kwargs):
        return self.provider_insts[0]

    def get_using_provider(self, *args, **kwargs):
        return self.provider_insts[0]


def completion(text="OK"):
    return {
        "id": "chat-test",
        "object": "chat.completion",
        "created": 1,
        "model": "test",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
    }


def provider(base="http://127.0.0.1:8787/bili/https://upstream.invalid/v1"):
    return ProviderOpenAIOfficial(
        {
            "id": "main",
            "type": "openai_chat_completion",
            "api_base": base,
            "key": ["synthetic-key"],
            "model": "test",
        },
        {},
    )


@pytest.mark.asyncio
async def test_native_sdk_concurrency_and_stream_preserve_main_conversation(plugin):
    captured = []

    async def transport(request):
        captured.append((dict(request.headers), json.loads(request.content)))
        await asyncio.sleep(0)
        if captured[-1][1].get("stream"):
            chunks = [
                {
                    "id": "s",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "STREAM"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "s",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json=completion())

    prov = provider()
    await prov.client.close()
    prov.client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url=prov.provider_config["api_base"],
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    plugin.context.provider_manager = Manager(prov)
    config = json.dumps(prov.provider_config, sort_keys=True)
    await plugin.initialize()
    try:
        await asyncio.gather(
            prov.text_chat(prompt="PLANNER_JSON"),
            prov.text_chat(prompt="RECALL_JSON"),
            prov.text_chat(prompt="REAL_CHAT_A", conversation_id="dialog-a"),
            prov.text_chat(prompt="REAL_CHAT_B", conversation_id="dialog-b"),
        )
        for headers, body in captured:
            helper = body["messages"][-1]["content"] in ("PLANNER_JSON", "RECALL_JSON")
            assert (headers.get(PASSTHROUGH_HEADER) == "1") == helper
            assert bool(headers.get("x-astrbot-conversation-id")) != helper
            assert PASSTHROUGH_HEADER not in json.dumps(body)
        for cid in (None, "dialog-stream"):
            chunks = [
                chunk async for chunk in prov.text_chat_stream(prompt="STREAM", conversation_id=cid)
            ]
            assert chunks and chunks[-1].completion_text == "STREAM"
            assert (captured[-1][0].get(PASSTHROUGH_HEADER) == "1") == (cid is None)
        assert json.dumps(prov.provider_config, sort_keys=True) == config
        assert plugin.bili_requests.status()["auxiliary_passthrough"]
    finally:
        await plugin.terminate()
        await prov.client.close()
    assert "send" not in vars(prov.client._client)
    assert "get_provider_by_id" not in vars(plugin.context.provider_manager)


@pytest.mark.asyncio
async def test_resolver_adapts_reloaded_sdk_and_restoration_waits_for_runtime_switch(plugin):
    seen = []

    async def transport(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        old = SimpleNamespace(
            client=SimpleNamespace(base_url="https://direct.invalid/v1", _client=client)
        )
        manager = Manager(old)
        plugin.context.provider_manager = manager
        plugin.bili_requests.install()
        fresh = SimpleNamespace(
            client=SimpleNamespace(
                base_url="http://127.0.0.1:8787/bili/https://upstream.invalid/v1", _client=client
            )
        )
        manager.provider_insts = [fresh]
        assert await manager.get_provider_by_id("main") is fresh
        # Config restoration does not switch a live SDK until host reload.
        plugin.config["bili"] = {"enabled": False}
        await client.post(str(fresh.client.base_url) + "/chat/completions", json={})
        assert seen[-1][PASSTHROUGH_HEADER] == "1"
        for url in (
            "https://direct.invalid/v1/chat/completions",
            "http://127.0.0.1:9999/bili/upstream/chat/completions",
        ):
            await client.post(url, json={})
            assert PASSTHROUGH_HEADER not in seen[-1]
        original_send = client.send
        plugin.bili_requests.install()
        assert client.send is original_send
        await plugin.terminate()
        await client.post(str(fresh.client.base_url) + "/chat/completions", json={})
        assert PASSTHROUGH_HEADER not in seen[-1]


@pytest.mark.asyncio
async def test_unload_preserves_later_adapters(plugin):
    async with httpx.AsyncClient() as client:
        plugin.context.provider_manager = Manager(
            SimpleNamespace(
                client=SimpleNamespace(
                    base_url="http://127.0.0.1:8787/bili/https://upstream.invalid/v1",
                    _client=client,
                )
            )
        )
        plugin.bili_requests.install()
        ours = client.send

        async def later(*args, **kwargs):
            return await ours(*args, **kwargs)

        client.send = later
        await plugin.terminate()
        assert client.send is later
