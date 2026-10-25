"""Segmentation preserves native decorations and trailing sticker delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.message.components import At, Image, Plain, Reply

pytest_plugins = ["test_panel_retirement"]


@pytest.mark.asyncio
async def test_help_reply_with_quote_mention_and_sticker_is_segmented_in_order(plugin):
    plugin.config["segment_reply"] = {
        "enabled": True, "min_length": 1, "max_length": 50, "merge_short_tail": False
    }
    quote, mention, sticker = (
        Reply(id="source"),
        At(qq="123"),
        Image.fromURL("https://example.invalid/meme.png"),
    )
    result = SimpleNamespace(
        chain=[
            quote,
            mention,
            Plain("help 是问我能干什么吧\n\n我能帮你查群成员和群资料\n其他的直接说就行"),
            sticker,
        ],
        result_content_type=None,
        is_llm_result=lambda: True,
    )
    extra = {}
    incoming = SimpleNamespace(
        get_result=lambda: result,
        get_platform_name=lambda: "aiocqhttp",
        get_extra=lambda k, default=None: extra.get(k, default),
        set_extra=lambda k, v: extra.__setitem__(k, v),
        plain_result=lambda text: SimpleNamespace(chain=[Plain(text)]),
        send=AsyncMock(),
    )
    plugin.segment.delay = lambda *a: 0
    await plugin.segment_reply_decorate(incoming)
    assert result.chain[:2] == [quote, mention]
    assert result.chain[2].text == "help 是问我能干什么吧"
    assert sticker not in result.chain
    await plugin.send_pending_segments(incoming)
    deliveries = incoming.send.await_args_list
    assert len(deliveries) == 2
    assert deliveries[-1].args[0].chain[-1] is sticker
    assert all(not isinstance(c, (At, Reply)) for d in deliveries for c in d.args[0].chain)
    assert extra["oven_segment_pending"] is None and extra["oven_segment_suffix"] is None
    await plugin.terminate()
