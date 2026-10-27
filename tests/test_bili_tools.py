"""Real SDK tools and HTTP requests over the native bili plugin contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema
import pytest
from aiohttp import web
from test_bili_requests import completion, provider

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.tool import ToolSet

pytest_plugins = ["test_panel_retirement"]


@pytest.mark.asyncio
async def test_native_context_recovery_is_registered_and_bound_to_its_conversation(plugin):
    seen, headers = [], []

    async def manifest(_):
        return web.json_response(
            {
                "protocolVersion": 1,
                "toolEndpoint": "/__bili/plugin/tool",
                "tools": {
                    "openai": [
                        {
                            "type": "function",
                            "function": {
                                "name": "decompress",
                                "description": "recover",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "blockId": {"type": "string"},
                                        "full": {"type": "boolean"},
                                        "toFile": {"type": "string"},
                                    },
                                    "required": ["blockId"],
                                },
                            },
                        }
                    ]
                },
            }
        )

    async def execute(request):
        seen.append(await request.json())
        return web.json_response({"ok": True, "content": "CURRENT_CONVERSATION_ONLY"})

    async def upstream(request):
        headers.append(dict(request.headers))
        return web.json_response(completion())

    app = web.Application()
    app.router.add_get("/__bili/plugin/manifest", manifest)
    app.router.add_post("/__bili/plugin/tool", execute)
    app.router.add_post("/bili/{tail:.*}", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    plugin.config["bili"] = {"host": "127.0.0.1", "port": port}
    prov = provider(base + "/bili/https://upstream.invalid/v1")
    plugin.context.get_current_chat_provider_id = AsyncMock(return_value="main")
    plugin.context.get_provider_by_id = lambda _: prov
    event = SimpleNamespace(unified_msg_origin="group-a")
    request = ProviderRequest()
    request.conversation = SimpleNamespace(cid="conversation-a")
    shared = ToolSet()
    request.func_tool = shared
    try:
        plugin.bili_requests.attach(prov)
        await plugin.bili_requests.native_tools.prepare(event, request)
        assert not shared.tools
        tool = request.func_tool.get_tool("decompress")
        assert tool is not None and tool.oven_multi_context_tool
        assert "toFile" not in tool.parameters["properties"]
        context = SimpleNamespace(context=SimpleNamespace(event=event))
        assert "CURRENT_CONVERSATION_ONLY" in await tool.call(context, blockId="b1")
        assert seen == [
            {
                "conversationId": "conversation-a",
                "tool": "decompress",
                "args": {"blockId": "b1"},
                "nativeCaller": True,
            }
        ]
        with pytest.raises(jsonschema.ValidationError):
            await tool.call(context, blockId="b1", toFile="outside.txt")
        wrong = SimpleNamespace(context=SimpleNamespace(event=SimpleNamespace()))
        assert "会话已改变" in await tool.call(wrong, blockId="b1")
        assert len(seen) == 1
        await prov.text_chat(
            prompt="MAIN", conversation_id="conversation-a", func_tool=request.func_tool
        )
        assert headers[-1]["x-bili-plugin"] == "astrbot"
        assert headers[-1]["x-bili-plugin-conversation"] == "conversation-a"
        await prov.text_chat(prompt="HELPER")
        assert headers[-1]["x-bili-passthrough"] == "1"
        assert "x-bili-plugin-conversation" not in headers[-1]
    finally:
        plugin.bili_requests.disconnect()
        await prov.client.close()
        await plugin.bili_proxy.stop()
        await runner.cleanup()
