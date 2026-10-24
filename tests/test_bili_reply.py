"""Compression-only receipts never become a chat answer or replay user tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from astrbot.core.provider.entities import LLMResponse, TokenUsage

pytest_plugins = ["test_panel_retirement"]
ACK = (
    "[Compressed m00001–m00002 → 1 block(s), ~2000 tokens saved.]"
    '\n· b1 full summary 70ch · head "Earlier question"'
    "\nFingerprints show head/tail only — every summary is stored in full."
    "\nNo compressible ranges remain — continue the task without compressing."
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["maintenance", "mixed", "tool", "bounded", "auxiliary"])
async def test_receipt_continuation_is_bounded_and_never_replays_tools(plugin, mode):
    async with httpx.AsyncClient() as client:
        response = LLMResponse(
            "assistant", completion_text=("ANSWER\n" if mode == "mixed" else "") + ACK
        )
        response.usage = TokenUsage(input_other=10, output=2)
        if mode == "tool":
            response.tools_call_name = ["real_user_tool"]
        original = AsyncMock(
            side_effect=[
                response,
                LLMResponse("assistant", completion_text=ACK if mode == "bounded" else "ANSWER"),
            ]
        )
        prov = SimpleNamespace(
            client=SimpleNamespace(
                base_url="http://127.0.0.1:8787/bili/https://upstream.invalid/v1", _client=client
            ),
            text_chat=original,
        )
        plugin.bili_requests.attach(prov)
        result = await prov.text_chat(conversation_id=None if mode == "auxiliary" else "dialog-a")
        assert original.await_count == (2 if mode in ("maintenance", "bounded") else 1)
        if mode == "auxiliary":
            assert result is response and result.completion_text == ACK
        elif mode == "tool":
            assert result.tools_call_name == ["real_user_tool"]
            assert result.completion_text == ""
        elif mode == "bounded":
            assert not result.completion_text
            assert result.usage.total == 12
        else:
            assert result.completion_text == "ANSWER"
            assert result.usage.total == 12
        assert response.completion_text.endswith(ACK)
        await plugin.terminate()
        assert prov.text_chat is original


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_stream_filters_partial_receipts_without_buffering_plain_answers(plugin, mixed):
    calls = []

    async def source(**kwargs):
        calls.append(kwargs)
        text = ("ANSWER\n" if mixed else "") + ACK if len(calls) == 1 else "ANSWER"
        for index in range(0, len(text), 3):
            yield LLMResponse("assistant", is_chunk=True, completion_text=text[index : index + 3])
        yield LLMResponse("assistant", completion_text=text)

    async with httpx.AsyncClient() as client:
        prov = SimpleNamespace(
            client=SimpleNamespace(
                base_url="http://127.0.0.1:8787/bili/https://upstream.invalid/v1", _client=client
            ),
            text_chat_stream=source,
        )
        plugin.bili_requests.attach(prov)
        chunks = [response async for response in prov.text_chat_stream(conversation_id="dialog-a")]
        assert len(calls) == (1 if mixed else 2)
        assert "".join(r.completion_text for r in chunks if r.is_chunk).strip() == "ANSWER"
        assert chunks[-1].completion_text == "ANSWER"
        await plugin.terminate()
        assert prov.text_chat_stream is source
