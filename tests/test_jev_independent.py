"""Independent Jev switches, exclusive reply ownership and native direct priority."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import AstrBotMessage, Group, MessageMember, MessageType, PlatformMetadata
from astrbot.core.star.session_llm_manager import SessionServiceManager

pytest_plugins = ["test_panel_retirement"]


def incoming(text="Please explain this idea.", mid="1"):
    msg = AstrBotMessage()
    msg.type = MessageType.GROUP_MESSAGE
    msg.self_id = "bot"
    msg.sender = MessageMember("12345678", "Alice")
    msg.group = Group("room", "Group")
    msg.message_id = mid
    msg.message = []
    msg.message_str = text
    return AstrMessageEvent(text, msg, PlatformMetadata("aiocqhttp", "test", "qq"), "room")


@pytest.fixture
def active(plugin, monkeypatch):
    plugin.config["jev"] = {
        "enabled": True,
        "active_reply_enabled": True,
        "inject_on_mention": False,
        "active_interval_sec": 5,
        "api_key": "synthetic",
    }
    plugin.context.get_config = lambda *_: {"provider_settings": {"enable": True}}
    plugin.ovenheart.owned = AsyncMock(return_value=False)
    monkeypatch.setattr(
        SessionServiceManager, "should_process_llm_request", AsyncMock(return_value=True)
    )
    plugin.jev_active.client.evaluate = AsyncMock(
        return_value={"status": "ready", "answers": {"should_reply": {"noul": 0.9}}}
    )
    return plugin


@pytest.mark.asyncio
async def test_standalone_active_reply_works_with_emotion_disabled(active):
    event = incoming()
    active.jev_scenes.record_history(event, active.config)
    assert await active.jev_active.consider(event)
    assert event.is_at_or_wake_command
    assert set(active.jev_active.client.evaluate.await_args.args[2]) == {"should_reply"}
    assert active.jev_active.client.evaluate.await_args.args[2]["should_reply"][
        "instructions"
    ].isascii()
    assert active.jev_active.client.evaluate.await_args.args[1].count(event.get_message_str()) == 1
    assert not await active.jev_scenes.mention_reading(incoming(), active.config)


@pytest.mark.asyncio
async def test_ovenheart_owns_reply_even_if_jev_active_enabled(active):
    active.ovenheart.owned.return_value = True
    assert not await active.jev_active.consider(incoming())
    active.jev_active.client.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_wakes_never_wait_for_active_judgment(active):
    ready = asyncio.Event()

    async def slow(*args):
        ready.set()
        await asyncio.Event().wait()

    active.jev_active.client.evaluate.side_effect = slow
    previous = incoming(mid="old")
    task = asyncio.create_task(active.jev_active.consider(previous))
    await ready.wait()
    direct = incoming(mid="direct")
    direct.is_at_or_wake_command = True
    active.jev_active.notice(direct)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert previous.is_stopped() and not direct.is_stopped()


@pytest.mark.asyncio
async def test_failed_judgment_stays_silent_and_context_change_cancels(active):
    active.jev_active.client.evaluate.return_value = {"status": "error", "http_status": 503}
    event = incoming()
    assert not await active.jev_active.consider(event) and not event.is_at_or_wake_command


@pytest.mark.asyncio
async def test_native_credentials_are_not_exposed_as_panel_fields(active):
    active.config["jev"].update(base_url="http://example.invalid", model="jev-latest")
    form = await active._api_settings()
    fields = {f["key"] for g in form["data"]["groups"] for f in g["fields"]}
    assert not {"jev.api_key", "jev.base_url", "jev.model"} & fields
    assert {"jev.active_reply_enabled", "jev.inject_on_mention"} <= fields


@pytest.mark.asyncio
async def test_intent_emotion_adapter_shares_requests_and_uses_cache(active):
    calls = []

    async def handle(request):
        data = await request.json()
        calls.append(data)
        assert set(data["questions"]) == {"intent", "emotion"}
        await asyncio.sleep(0.01)
        return web.json_response(
            {
                "answers": {
                    "intent": {"type": "choice", "choice": "asking_help", "confidence": 0.9},
                    "emotion": {"type": "choice", "choice": "neutral", "confidence": 0.8},
                },
                "usage": {"input_tokens": 20, "output_tokens": 5},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/systemone", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    judge = type(active.jev_scenes.jev)()
    cfg = {
        "api_key": "synthetic",
        "base_url": f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1",
        "timeout_sec": 2,
        "retries": 0,
    }
    try:
        results = await asyncio.gather(
            *(judge.judge(cfg, "Please help", dimensions=("intent", "emotion")) for _ in range(4))
        )
        assert all(r["ok"] and r["intent"] == "提问求助" for r in results)
        assert len(calls) == 1
        assert (await judge.judge(cfg, "Please help", dimensions=("intent", "emotion")))[
            "cache_hit"
        ]
        assert judge.transport.metrics["input_tokens"] == 20
    finally:
        await judge.close()
        await runner.cleanup()
