"""Live notification integration with real persisted state and no external messages."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_panel_retirement import ROOT
from yarl import URL

pytest_plugins = ["test_panel_retirement"]
dy = importlib.import_module(f"{ROOT.name}.features.douyin_live")


def test_legacy_import_preserves_states_and_does_not_resurrect_cookie(tmp_path):
    legacy = tmp_path / dy.LEGACY_PLUGIN
    legacy.mkdir()
    subscriptions = {
        "version": 1,
        "sessions": {
            "qq:GroupMessage:123": {"rooms": {
                "shared_room": {"web_rid": "shared_room", "name": "备注", "state": "live",
                                "updated_at": 123.5},
            }},
            "qq:GroupMessage:456": {"rooms": {
                "shared_room": {"web_rid": "shared_room", "name": "另一个群", "state": "offline",
                                "updated_at": 100.5},
            }},
        },
    }
    original = json.dumps(subscriptions, ensure_ascii=False).encode()
    (legacy / "subscriptions.json").write_bytes(original)
    (legacy / "cookies.json").write_text('{"cookie":"sessionid=AbCdEf"}', "utf-8")
    plugin = SimpleNamespace(data_dir=tmp_path / "oven_multi", config={}, context=SimpleNamespace())
    monitor = dy.DouyinLiveMonitor(plugin)
    assert monitor.store.session_count == 2
    assert len(monitor.store.all_pairs()) == 1
    sub = monitor.store.get("qq:GroupMessage:123", "shared_room")
    assert (sub.name, sub.state, sub.updated_at) == ("备注", "live", 123.5)
    monitor.cookies.clear()
    monitor.store.remove("qq:GroupMessage:456", "shared_room")
    monitor.store.save()
    reloaded = dy.DouyinLiveMonitor(plugin)
    assert reloaded.store.session_count == 1
    assert not reloaded.cookies.raw_present
    assert (legacy / "subscriptions.json").read_bytes() == original
    assert (legacy / "cookies.json").is_file()


@pytest.mark.asyncio
async def test_shared_room_is_polled_once_and_unknown_never_reports_offline(plugin, monkeypatch):
    monitor = plugin.douyin
    plugin.config["douyin"] = {"enabled": True, "notify_with_cover": False}
    plugin.context.send_message = AsyncMock()
    for umo in ("qq:GroupMessage:123", "qq:GroupMessage:456"):
        monitor.store.add(umo, "room123", state=dy.LiveState.LIVE)
    monitor.client.fetch_room = AsyncMock(return_value=dy.RoomInfo("room123", error="暂不可用"))
    monkeypatch.setattr(dy.random, "uniform", lambda *_: 0)
    await monitor.check_all(notify=False)
    assert monitor.client.fetch_room.await_count == 1
    assert all(monitor.store.get(umo, "room123").state == "live"
               for umo in monitor.store.all_pairs()["room123"])
    plugin.context.send_message.assert_not_awaited()
    monitor.client.fetch_room.return_value = dy.RoomInfo("room123", state=dy.LiveState.OFFLINE)
    await monitor.check_all()
    assert plugin.context.send_message.await_count == 2
    assert {call.args[0] for call in plugin.context.send_message.await_args_list} == {
        "qq:GroupMessage:123", "qq:GroupMessage:456",
    }
    await monitor.check_all()
    assert plugin.context.send_message.await_count == 2
    assert monitor.status()["last_check"] > 0
    await monitor.stop()


@pytest.mark.asyncio
async def test_cookie_command_preserves_case_and_closes_previous_http_session(plugin):
    monitor = plugin.douyin
    monitor.cookies.set_raw("sessionid=OldCaSe")
    old_session = await monitor.client._http()
    assert (
        old_session.cookie_jar.filter_cookies(URL(dy.DOUYIN_ORIGIN))["sessionid"].value
        == "OldCaSe"
    )
    monitor.client._ttwid = "old-anonymous-token"
    event = SimpleNamespace(
        message_str="/抖音Cookie sessionid=NeWcAsE; key=ABCxyz",
        is_admin=lambda: False,
        plain_result=lambda text: text,
    )
    denied = [reply async for reply in monitor.cmd_set_cookie(event)]
    assert "只有管理员" in denied[0]
    assert monitor.cookies.header == "sessionid=OldCaSe"
    event.is_admin = lambda: True
    accepted = [reply async for reply in monitor.cmd_set_cookie(event)]
    assert "已保存" in accepted[0]
    assert monitor.cookies.cookie_dict()["sessionid"] == "NeWcAsE"
    assert old_session.closed and monitor.client._session is None
    assert monitor.client._ttwid == ""
    assert "NeWcAsE" not in json.dumps(monitor.status())
    await monitor.stop()


@pytest.mark.asyncio
async def test_legacy_monitor_blocks_polling_and_settings_can_stop_builtin(plugin, monkeypatch):
    plugin.context.get_registered_star = lambda name: SimpleNamespace(activated=True)
    plugin.config["douyin"] = {"enabled": True}
    monitor = plugin.douyin
    checks = AsyncMock()
    monkeypatch.setattr(monitor, "check_all", checks)
    await monitor.configure()
    await asyncio.sleep(0)
    assert monitor.status()["legacy_active"] and not monitor.status()["running"]
    checks.assert_not_awaited()
    plugin.config["douyin"]["enabled"] = False
    await monitor.configure()
    assert monitor._task is None and monitor._stopping
