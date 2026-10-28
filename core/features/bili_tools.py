"""Native session-bound tools over billion-context's plugin protocol."""

from __future__ import annotations

import copy
import json
import time

import aiohttp
import jsonschema

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool, ToolSet

NAMES = {"compress", "decompress", "search_context", "acp_status", "acp_cache"}


class BiliContextTool(FunctionTool):
    oven_multi_context_tool = True

    async def call(self, context, **kwargs):
        jsonschema.validate(kwargs, self.parameters)
        if self.bridge.plugin.closing or context.context.event is not self.event:
            return '{"ok":false,"error":"上下文工具的会话已改变。"}'
        session = await self.bridge.plugin.bili_proxy._http()
        try:
            async with session.post(
                self.base + "/__bili/plugin/tool",
                json={
                    "conversationId": self.cid,
                    "tool": self.name,
                    "args": kwargs,
                    "nativeCaller": True,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                result = await response.json()
            return json.dumps(result, ensure_ascii=False)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error": "压缩上下文工具暂不可用，请保留当前任务继续处理。",
                    "cause": type(exc).__name__,
                },
                ensure_ascii=False,
            )


class BiliNativeTools:
    def __init__(self, bridge):
        self.bridge = bridge
        self.bound = {}
        self.schemas = []
        self.base = ""
        self.expires = 0

    async def prepare(self, event, request):
        if request.conversation is None or not request.conversation.cid:
            return
        provider_id = await self.bridge.plugin.context.get_current_chat_provider_id(
            event.unified_msg_origin
        )
        provider = self.bridge.plugin.context.get_provider_by_id(provider_id)
        if not self.bridge._route(getattr(getattr(provider, "client", None), "base_url", "")):
            return
        base = self.bridge.plugin.bili_proxy.url.rstrip("/")
        if base != self.base or time.monotonic() >= self.expires:
            try:
                session = await self.bridge.plugin.bili_proxy._http()
                async with session.get(
                    base + "/__bili/plugin/manifest", timeout=aiohttp.ClientTimeout(total=3)
                ) as response:
                    response.raise_for_status()
                    manifest = await response.json()
                if (
                    manifest.get("protocolVersion") != 1
                    or manifest.get("toolEndpoint") != "/__bili/plugin/tool"
                ):
                    raise ValueError("不支持的上下文工具协议")
                schemas = []
                for entry in manifest["tools"]["openai"]:
                    schema = entry["function"]
                    if schema["name"] in NAMES:
                        schemas.append(copy.deepcopy(schema))
                if not any(schema["name"] == "decompress" for schema in schemas):
                    raise ValueError("代理未提供恢复工具")
                self.schemas, self.base = schemas, base
                self.expires = time.monotonic() + 60
            except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                logger.warning("[烤箱-压缩] 原生上下文工具未就绪 (%s)", type(exc).__name__)
                return
        tools = ToolSet(
            [
                tool
                for tool in (request.func_tool.tools if request.func_tool is not None else [])
                if not getattr(tool, "oven_multi_context_tool", False)
            ]
        )
        for schema in self.schemas:
            parameters = copy.deepcopy(schema["parameters"])
            if schema["name"] == "decompress":
                # Context recovery is scoped text access, not a file-writing tool.
                parameters["properties"] = {
                    k: v for k, v in parameters["properties"].items() if k in {"blockId", "full"}
                }
            parameters["additionalProperties"] = False
            tool = BiliContextTool(
                name=schema["name"],
                description="当前会话的压缩历史工具，仅在需要管理上下文时使用。\n"
                + schema["description"],
                parameters=parameters,
            )
            if tool.name == "decompress":
                tool.description = (
                    "恢复当前会话已压缩区块的文字原文。"
                    "blockId 来自 search_context 或 acp_status 的真实结果；"
                    "full=true 恢复完整原文，不支持写文件。不存在的区块不猜测。"
                )
            tool.bridge, tool.event = self.bridge, event
            tool.cid, tool.base = str(request.conversation.cid), base
            tools.add_tool(tool)
        request.func_tool = tools
        cid = str(request.conversation.cid)
        self.bound.pop(cid, None)
        self.bound[cid] = True
        if len(self.bound) > 128:
            self.bound.pop(next(iter(self.bound)))
