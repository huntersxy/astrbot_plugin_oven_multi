"""Coexistence yields @ ownership while retaining standalone settings and Jev."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_panel_retirement import plugin  # noqa: F401

from astrbot.api.platform import MessageType
from astrbot.api.provider import ProviderRequest
from astrbot.core.message.components import At, Plain


@pytest.fixture
def linked(plugin):  # noqa: F811
    core = SimpleNamespace(
        ready=True,
        closing=False,
        oven_multi_protocol=1,
        settings={"enabled": True, "apply_enabled": True},
        owns_group_reply=AsyncMock(return_value=True),
        resolve_scope=AsyncMock(return_value=("scope", "persona", "label")),
        conversation=SimpleNamespace(valid=AsyncMock(return_value=True)),
    )
    plugin.context.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=core)
    plugin.config.update(
        mention_parser={"enabled": True},
        bracket_matching={"enabled": False},
        repetition={"enabled": False},
        jev={"enabled": True, "inject_on_mention": True, "api_key": "synthetic-key"},
    )
    plugin.speakers.record = Mock()
    plugin.speakers.build_speakers_prompt = Mock(return_value="MEMBERS")
    plugin.jev_scenes.mention_reading = AsyncMock(return_value="LEGACY_READING")
    return core


def message():
    extra = {}
    chain = [Plain('<mention id="12345"/> hello')]
    result = SimpleNamespace(chain=chain)
    return SimpleNamespace(
        unified_msg_origin="test:GroupMessage:room",
        is_at_or_wake_command=True,
        message_obj=SimpleNamespace(
            message_str="hello",
            group_id="room",
            sender=SimpleNamespace(nickname="Alice", user_id="12345"),
        ),
        get_sender_id=lambda: "12345",
        get_self_id=lambda: "54321",
        get_result=lambda: result,
        get_extra=lambda key, default=None: extra.get(key, default),
        set_extra=lambda key, value: extra.__setitem__(key, value),
        get_message_type=lambda: MessageType.GROUP_MESSAGE,
    )


@pytest.mark.asyncio
async def test_core_suppresses_all_mention_paths_and_legacy_judgment(plugin, linked):  # noqa: F811
    event = message()
    original = event.get_result().chain[:]
    req = ProviderRequest(prompt="hello")
    await plugin.on_early_group_message(event)
    await plugin.on_group_message(event)
    await plugin.on_llm_request(event, req)
    await plugin.parse_mention_tags(event)
    assert event.get_result().chain == original and not req.extra_user_content_parts
    plugin.speakers.record.assert_not_called()
    plugin.speakers.build_speakers_prompt.assert_not_called()
    plugin.jev_scenes.mention_reading.assert_not_awaited()
    assert plugin.config["mention_parser"]["enabled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", ["absent", "closing", "global", "session"])
async def test_disabled_core_restores_standalone_mentions_and_jev(plugin, linked, disabled):  # noqa: F811
    if disabled == "absent":
        plugin.context.get_registered_star = lambda name: None
    elif disabled == "closing":
        linked.closing = True
    elif disabled == "global":
        linked.settings["enabled"] = False
    else:
        linked.owns_group_reply.return_value = False
    event = message()
    req = ProviderRequest(prompt="hello")
    await plugin.on_group_message(event)
    await plugin.on_llm_request(event, req)
    await plugin.parse_mention_tags(event)
    plugin.speakers.record.assert_called_once()
    plugin.jev_scenes.mention_reading.assert_awaited_once()
    assert len(req.extra_user_content_parts) == 2
    assert any(isinstance(item, At) for item in event.get_result().chain)


@pytest.mark.asyncio
async def test_panel_reports_effective_switch_without_overwriting_saved_value(plugin, linked):  # noqa: F811
    before = json.dumps(plugin.config, sort_keys=True)
    data = (await plugin._api_settings())["data"]
    field = next(
        f for g in data["groups"] for f in g["fields"] if f["key"] == "mention_parser.enabled"
    )
    assert field["readonly"] and field["effective_value"] is False and field["value"] is True
    assert json.dumps(plugin.config, sort_keys=True) == before
    await plugin.panel_settings.save(plugin.config, {"jev.enable_advice": False}, data["revision"])
    assert plugin.config["jev"]["api_key"] == "synthetic-key"
    assert plugin.config["mention_parser"]["enabled"] is True
    linked.closing = True
    field = next(
        f
        for g in (await plugin._api_settings())["data"]["groups"]
        for f in g["fields"]
        if f["key"] == "mention_parser.enabled"
    )
    assert not field.get("readonly") and field["value"] is True


def payload():
    question = {"source": "q", "sender_id": "12345", "text": "汐兮雨是谁", "time": 1}
    own = {
        "source": "s",
        "sender_id": "54321",
        "self": True,
        "text": "",
        "time": 2,
        "media": [{"status": "ready", "text": "A_BLUE_CAT"}],
        "image_refs": ["PRIVATE_IMAGE_PATH"],
    }
    target = {
        "source": "t",
        "sender_id": "12345",
        "text": "@bot(54321)",
        "time": 4,
        "reply_to": {"sender_id": "54321", "text": "我发了图片"},
    }
    return {
        "version": 1,
        "scope": "scope",
        "epoch": "epoch",
        "target": target,
        "messages": [
            question,
            own,
            {"source": "p", "sender_id": "98765", "text": "OTHER_CHAT", "time": 3},
            target,
        ],
        "bare_mention": True,
        "bot_sender_id": "54321",
    }


@pytest.mark.asyncio
async def test_service_uses_target_thread_quote_and_own_media_without_ids_or_raw_reply_decisions(
    plugin, linked  # noqa: F811
):
    plugin.jev_scenes.jev.judge = AsyncMock(
        return_value={
            "ok": True,
            "intent": "提问求助",
            "intent_confidence": 0.8,
            "should_reply": "回复",
            "api_key": "LEAK",
            "risk": "低风险",
        }
    )
    output = await plugin.analyze_for_ovenheart(message(), payload())
    assert output["status"] == "ready" and output["fields"]["intent"] == "提问求助"
    state = plugin.jev_scenes.jev.judge.await_args.args[1]
    assert all(
        term in state for term in ["汐兮雨是谁", "A_BLUE_CAT", "quoted:", "standalone mention"]
    )
    assert all(term not in state for term in ["12345", "54321", "98765", "PRIVATE_IMAGE_PATH"])
    assert "LEAK" not in json.dumps(output) and "should_reply" not in output["fields"]
    assert plugin.jev_scenes.jev.judge.await_args.kwargs == {"with_decision": False, "dimensions": ("intent", "emotion")}
    assert plugin.jev_scenes.jev.judge.await_args.args[0]["retries"] == 0
    assert plugin.config["jev"]["api_key"] == "synthetic-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["disabled", "blacklist", "scope", "epoch", "session"])
async def test_no_provider_call_outside_valid_core_scope(plugin, linked, denied):  # noqa: F811
    data = payload()
    if denied == "disabled":
        plugin.config["jev"]["enabled"] = False
    elif denied == "blacklist":
        plugin._blocked = lambda event: True
    elif denied == "scope":
        data["scope"] = "elsewhere"
    elif denied == "epoch":
        linked.conversation.valid.return_value = False
    else:
        linked.owns_group_reply.return_value = False
    plugin.jev_scenes.jev.judge = AsyncMock()
    assert (await plugin.analyze_for_ovenheart(message(), data))["status"] != "ready"
    plugin.jev_scenes.jev.judge.assert_not_awaited()
