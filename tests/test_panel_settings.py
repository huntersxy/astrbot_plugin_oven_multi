# Copyright (C) 2026 汐兮雨
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""离线测试：Panel 快捷设置（白名单校验、写回持久化、revision 防陈旧、API 处理器）。

不联网、不依赖真实 astrbot 运行时（打桩方式与 test_jev_reading 一致）。

运行方式：
    python -m pytest tests/test_panel_settings.py -q
    python tests/test_panel_settings.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, os.path.dirname(_PLUGIN_DIR))


def _stub(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    if "." in name:
        parent_name, child = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, module)
    return module


class _StubLogger:
    lines: list = []

    def _rec(self, *args, **kwargs):
        if args:
            _StubLogger.lines.append(str(args[0]))

    debug = info = warning = error = exception = _rec


class _StubFilter:
    """恒等装饰器：仅满足 main 模块导入时的 handler 注册语法。"""

    maxsize = 9223372036854775807
    EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="g", ALL="all")
    PermissionType = types.SimpleNamespace(ADMIN="admin", MEMBER="member", GROUP_ADMIN="gadmin")

    def __getattr__(self, name):
        return lambda *args, **kwargs: lambda fn: fn


class _StubStar:
    def __init__(self, context):
        self.context = context


class _StubPlain:
    def __init__(self, text: str = ""):
        self.text = text


def _ensure_astrbot() -> None:
    # 只有真实 astrbot（含 api.web）可用时才跳过打桩；
    # 其他测试文件先装的精简桩不含 main 所需模块，这里统一覆盖补齐。
    with contextlib.suppress(Exception):
        from astrbot.api.web import request  # noqa: F401

        return
    _stub("astrbot")
    _stub("astrbot.api", AstrBotConfig=dict, logger=_StubLogger())
    _stub(
        "astrbot.api.message_components",
        Plain=_StubPlain,
        At=type("At", (), {}),
        Image=type("Image", (), {}),
        Json=type("Json", (), {}),
        File=type("File", (), {}),
    )
    _stub("astrbot.api.event", AstrMessageEvent=object, filter=_StubFilter())
    _stub(
        "astrbot.api.star",
        Context=object,
        Star=_StubStar,
        StarTools=types.SimpleNamespace(get_data_dir=lambda name: os.devnull),
        register=lambda *args, **kwargs: lambda cls: cls,
    )
    _stub(
        "astrbot.api.web",
        request=types.SimpleNamespace(body=asyncio.sleep),  # 测试内按用例覆写 main.request
    )
    _stub(
        "astrbot.core.message.message_event_result",
        ResultContentType=types.SimpleNamespace(
            STREAMING_RESULT="SR",
            STREAMING_FINISH="SF",
            LLM_RESULT="LLM",
            GENERAL_RESULT="GENERAL",
        ),
        MessageChain=type("MessageChain", (), {"chain": []}),
    )
    _stub("astrbot.core.agent.message", TextPart=type("TextPart", (), {}))
    _stub("astrbot.api.platform", MessageType=types.SimpleNamespace(GROUP_MESSAGE="g"))
    _stub("astrbot.core.star.star", star_map={})


def _ensure_aiohttp() -> None:
    with contextlib.suppress(Exception):
        import aiohttp  # noqa: F401

        return

    class ClientError(Exception):
        pass

    class ClientSession:
        async def close(self):
            pass

    _stub(
        "aiohttp",
        ClientError=ClientError,
        ClientTimeout=lambda **kwargs: None,
        ClientSession=lambda *args, **kwargs: ClientSession(),
    )


_ensure_astrbot()
_ensure_aiohttp()

import astrbot_plugin_oven_multi.main as main_module  # noqa: E402
from astrbot_plugin_oven_multi.features import panel_settings  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_oven_multi"


class FakeAstrBotConfig(dict):
    """带 save_config 的宿主配置替身（面板先深合并进本 dict，再整体持久化）。"""

    def __init__(self, data=None):
        super().__init__(data or {})
        self.saved: list[dict] = []

    def save_config(self, replace_config=None, **kwargs):
        if replace_config:
            self.update(replace_config)
        self.saved.append(json.loads(json.dumps(dict(self))))


class FakeRequest:
    def __init__(self, raw: bytes):
        self._raw = raw

    async def body(self) -> bytes:
        return self._raw


def make_plugin(config=None):
    context = types.SimpleNamespace(registered_web_apis=[])
    context.register_web_api = lambda *args: context.registered_web_apis.append(args)
    return main_module.OvenMultiPlugin(context, config if config is not None else {})


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 字段注册与取值
# ---------------------------------------------------------------------------


def test_describe_groups_and_defaults():
    manager = panel_settings.PanelSettingsManager()
    config = {"repetition": {"repeat_threshold": 3}}
    data = manager.describe(config)
    names = [group["name"] for group in data["groups"]]
    assert names == ["黑名单", "消息与回复", "分段回复", "Jev 判读", "文件读取", "上下文压缩"]
    all_fields = {field["key"]: field for group in data["groups"] for field in group["fields"]}
    assert "bracket_matching.enabled" in all_fields
    # 配置里有的值优先，缺失的回退 schema 默认值
    assert all_fields["repetition.repeat_threshold"]["value"] == 3
    assert all_fields["repetition.break_spell_probability"]["value"] == 0.3
    assert all_fields["segment_reply.process_non_llm"]["value"] is False
    assert all_fields["segment_reply.delay_type"]["type"] == "enum"
    assert "fixed" in all_fields["segment_reply.delay_type"]["options"]
    assert all_fields["segment_reply.random_delay_range"]["type"] == "pair"
    assert all_fields["repetition.repeat_threshold"]["max"] == 10  # schema slider 边界
    # 顶层与多层路径、列表字段与组指引
    assert all_fields["blacklist_groups"]["type"] == "list"
    assert all_fields["blacklist_groups"]["item_type"] == "str"
    assert all_fields["file_reader.rag.retrieve_top_k"]["value"] == 5
    assert all_fields["iam_thinking.thinking_emoji_ids"]["item_type"] == "int"
    assert all_fields["jev.base_url"]["max_chars"] == 200
    notes = {group["name"]: group.get("note") for group in data["groups"]}
    assert "API Key" in notes["Jev 判读"]
    assert "Provider" in notes["文件读取"]
    assert data["revision"]
    print("describe groups/defaults ok")


def test_describe_ignores_non_dict_sections():
    manager = panel_settings.PanelSettingsManager()
    data = manager.describe({"jev": "corrupted"})
    fields = {f["key"]: f for g in data["groups"] for f in g["fields"]}
    assert fields["jev.enabled"]["value"] is False  # 回退默认，不抛异常
    print("describe 容错非 dict 节 ok")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def test_validate_patch_types_and_bounds():
    ok = panel_settings.validate_patch(
        {
            "bracket_matching.enabled": True,
            "repetition.repeat_threshold": 4,
            "repetition.break_spell_probability": 0.5,
            "repetition.break_spell_text": "别念了！",
            "segment_reply.random_delay_range": [0.5, 2],
            "blacklist_groups": ["123", " 456 "],
            "iam_thinking.done_emoji_ids": ["74", 75],
            "file_reader.rag.retrieve_top_k": 8,
        }
    )
    assert ok == {
        "bracket_matching": {"enabled": True},
        "repetition": {
            "repeat_threshold": 4,
            "break_spell_probability": 0.5,
            "break_spell_text": "别念了！",
        },
        "segment_reply": {"random_delay_range": [0.5, 2]},
        "blacklist_groups": ["123", "456"],
        "iam_thinking": {"done_emoji_ids": [74, 75]},
        "file_reader": {"rag": {"retrieve_top_k": 8}},
    }
    # base_url 等长字符串字段按覆盖元数据放宽长度
    panel_settings.validate_patch({"jev.base_url": "https://" + "a" * 180})

    def rejects(values, keyword):
        try:
            panel_settings.validate_patch(values)
        except ValueError as exc:
            assert keyword in str(exc)
            return
        raise AssertionError(f"应拒绝: {values}")

    rejects({"no_such.key": True}, "不支持的字段")
    rejects({"repetition.repeat_threshold": True}, "整数")  # bool 不是合法 int
    rejects({"repetition.repeat_threshold": 1.5}, "整数")
    rejects({"repetition.repeat_threshold": 99}, "之间")
    rejects({"segment_reply.delay_type": "per_char"}, "有效选项")
    rejects({"segment_reply.random_delay_range": [1]}, "两个数字")
    rejects({"segment_reply.random_delay_range": [3, 1]}, "最小值")
    rejects({"segment_reply.random_delay_range": [0, 999]}, "之内")
    rejects({"repetition.break_spell_text": ""}, "不能为空")
    rejects({"repetition.break_spell_text": "x" * 51}, "长度")
    rejects(
        {"segment_reply.min_length": 80, "segment_reply.max_length": 50},
        "最小字数",
    )
    # 列表字段：类型、空项、元素类型
    rejects({"blacklist_groups": "not-a-list"}, "列表")
    rejects({"blacklist_groups": [""]}, "不能为空")
    rejects({"iam_thinking.thinking_emoji_ids": ["x"]}, "整数")
    rejects({"iam_thinking.thinking_emoji_ids": [True]}, "开关值")
    print("validate_patch 校验 ok")


# ---------------------------------------------------------------------------
# 保存链路
# ---------------------------------------------------------------------------


def test_save_roundtrip_and_revision():
    manager = panel_settings.PanelSettingsManager()
    config = FakeAstrBotConfig()
    data = manager.describe(config)
    revision = data["revision"]

    new_revision = run(
        manager.save(
            config,
            {"segment_reply.max_length": 80, "jev.enabled": True},
            revision,
        )
    )
    assert new_revision != revision
    assert config["segment_reply"]["max_length"] == 80
    assert config["jev"]["enabled"] is True
    assert config.saved and config.saved[-1]["segment_reply"]["max_length"] == 80

    # 保存后 describe 反映新值；旧 revision 已失效
    data = manager.describe(config)
    fields = {f["key"]: f for g in data["groups"] for f in g["fields"]}
    assert fields["segment_reply.max_length"]["value"] == 80
    try:
        run(manager.save(config, {"segment_reply.max_length": 60}, revision))
    except panel_settings.StaleSettingsError as exc:
        assert "刷新" in str(exc)
    else:
        raise AssertionError("旧 revision 应被拒绝")
    print("save 往返与 revision ok")


def test_save_detects_dashboard_side_change():
    manager = panel_settings.PanelSettingsManager()
    config = FakeAstrBotConfig()
    data = manager.describe(config)  # 记录快照
    # 模拟用户在 AstrBot 插件配置页改了同一字段（不经过面板）
    config.setdefault("repetition", {})["repeat_threshold"] = 9
    try:
        run(
            manager.save(
                config,
                {"segment_reply.max_length": 80},
                data["revision"],
            )
        )
    except panel_settings.StaleSettingsError as exc:
        assert "插件配置页" in str(exc)
    else:
        raise AssertionError("宿主侧改动应触发陈旧拒绝")
    assert "segment_reply" not in config  # 拒绝后未写入

    # 刷新设置页（重新 describe）后快照重置，外部改动未经过面板时 revision
    # 仍有效，保存应恢复成功——否则陈旧状态永远无法解除
    refreshed = manager.describe(config)
    revision = run(
        manager.save(config, {"segment_reply.max_length": 80}, refreshed["revision"])
    )
    assert config["segment_reply"]["max_length"] == 80
    assert revision != data["revision"]
    print("宿主侧改动防陈旧 ok")


def test_apply_values_rolls_back_on_save_failure():
    class ExplodingConfig(dict):
        def save_config(self, replace_config=None, **kwargs):
            if replace_config:
                self.update(replace_config)
            raise OSError("disk full")

    config = ExplodingConfig({"repetition": {"enabled": True}})
    try:
        panel_settings.apply_values(
            config, {"repetition": {"enabled": False}, "jev": {"enabled": True}}
        )
    except OSError:
        pass
    else:
        raise AssertionError("持久化失败应上抛")
    assert config["repetition"]["enabled"] is True  # 已存在的节回滚
    assert "jev" not in config  # 原本不存在的节不残留
    print("写盘失败回滚 ok")


def test_apply_values_deep_merge_preserves_native_fields():
    """回归：面板保存不得整节覆盖，同节未提交的密钥/选择器/列表自定义值必须保留。"""
    config = FakeAstrBotConfig(
        {
            "jev": {"enabled": True, "api_key": "sk-keep"},
            "file_reader": {
                "enabled": True,
                "rag": {"enabled": True, "embedding_provider_id": "openai/embed"},
            },
            "segment_reply": {"enabled": True, "exclude_keywords": ["自定义菜单"]},
        }
    )
    nested = panel_settings.validate_patch(
        {
            "jev.enabled": False,
            "file_reader.rag.retrieve_top_k": 9,
            "segment_reply.min_length": 15,
        }
    )
    panel_settings.apply_values(config, nested)
    assert config["jev"]["api_key"] == "sk-keep"
    assert config["jev"]["enabled"] is False
    assert config["file_reader"]["rag"]["embedding_provider_id"] == "openai/embed"
    assert config["file_reader"]["rag"]["retrieve_top_k"] == 9
    assert config["segment_reply"]["exclude_keywords"] == ["自定义菜单"]
    assert config.saved[-1]["jev"]["api_key"] == "sk-keep"
    print("深合并保留原生字段 ok")


def test_panel_covers_all_non_native_schema_leaves():
    """防回归：schema 叶子除原生保留字段外必须全部由面板接管，且两边不重复。"""
    panel_paths = {path for _, paths in panel_settings.GROUP_PATHS for path in paths}
    leaves = panel_settings.schema_leaf_paths()
    assert panel_paths <= leaves  # 面板路径都在 schema 中真实存在
    assert not (panel_paths & panel_settings.NATIVE_ONLY_PATHS)
    assert leaves - panel_paths == set(panel_settings.NATIVE_ONLY_PATHS)
    print(
        f"schema 叶子全覆盖 ok（叶子 {len(leaves)}，面板 {len(panel_paths)}，原生 "
        f"{len(panel_settings.NATIVE_ONLY_PATHS)}）"
    )


# ---------------------------------------------------------------------------
# main.py API 处理器
# ---------------------------------------------------------------------------


def test_api_settings_routes_registered():
    plugin = make_plugin()
    routes = [
        (row[0].replace(f"/{PLUGIN_NAME}/", ""), row[2])
        for row in plugin.context.registered_web_apis
    ]
    assert routes == [
        ("billion/dashboard", ["GET"]),
        ("status", ["GET"]),
        ("balance", ["GET"]),
        ("settings", ["GET"]),
        ("settings/save", ["POST"]),
        ("billion/status", ["GET"]),
        ("billion/wire", ["POST"]),
        ("billion/restore", ["POST"]),
    ]
    print("API 路由注册 ok")


def test_api_settings_get_and_save_roundtrip():
    plugin = make_plugin(FakeAstrBotConfig())
    payload = run(plugin._api_settings())
    assert payload["success"] is True
    revision = payload["data"]["revision"]
    assert payload["data"]["groups"]

    main_module.request = FakeRequest(
        json.dumps(
            {
                "revision": revision,
                "values": {
                    "segment_reply.enabled": True,
                    "segment_reply.max_length": 90,
                },
            }
        ).encode("utf-8")
    )
    result = run(plugin._api_settings_save())
    assert result["success"] is True
    assert result["data"]["revision"] != revision
    assert plugin.config["segment_reply"]["enabled"] is True
    # 修改立即反映到状态接口的功能行
    payload = run(plugin._api_status())
    seg = next(row for row in payload["data"]["features"] if row["id"] == "segment_reply")
    assert seg["enabled"] is True
    print("API 设置读取/保存往返 ok")


def test_api_settings_save_rejects_bad_input():
    plugin = make_plugin(FakeAstrBotConfig())
    cases = [
        (b"not-json", "JSON"),
        (json.dumps({"values": "flat"}).encode(), "设置"),
        (json.dumps({"values": {"x.y": 1}}).encode(), "不支持的字段"),
        (json.dumps({"values": {"repetition.repeat_threshold": 0}}).encode(), "之间"),
    ]
    for raw, keyword in cases:
        main_module.request = FakeRequest(raw)
        result = run(plugin._api_settings_save())
        assert result["success"] is False, raw
        assert keyword in result["message"], raw
    # 陈旧表单带 stale 标记
    run(plugin._api_settings())
    main_module.request = FakeRequest(
        json.dumps({"revision": "outdated", "values": {}}).encode("utf-8")
    )
    result = run(plugin._api_settings_save())
    assert result["success"] is False and result["stale"] is True
    print("API 非法输入拒绝 ok")


# ---------------------------------------------------------------------------
# 上下文压缩（billion-context）API 与面板联动
# ---------------------------------------------------------------------------


class FakeHostConfig(FakeAstrBotConfig):
    """带 provider/agent_runner 的宿主配置替身。"""


def _bili_plugin():
    plugin = make_plugin(FakeAstrBotConfig({"bili": {"enabled": True}}))
    host = FakeHostConfig(
        {
            "provider": [
                {
                    "id": "main",
                    "type": "openai_chat_completion",
                    "api_base": "https://api.example.com/v1",
                    "key": "sk-x",
                },
            ],
            "agent_runner": {
                "config": {"model": {"provider_id": "main"}, "compression": {"max_turns": 50}}
            },
        }
    )
    plugin._host_config = lambda: host  # 同一实例：接管与恢复须读到同一份配置
    plugin._chat_provider_view = lambda: {"main": "https://api.example.com/v1"}
    plugin.bili_proxy.state = "spawned"  # healthy 只读 property 由状态推导
    plugin.bili_proxy.session_header_state = "ok"  # 会话标识已确认（否则面板插入警示）

    async def probe():
        return plugin.bili_proxy.healthy, ""

    async def verify():
        return None

    plugin.bili_proxy.probe_health = probe
    plugin.bili_proxy.verify_session_header = verify
    # 数据目录是 devnull 桩，接管记录改放真实临时目录
    plugin.bili_wire.record_path = Path(tempfile.mkdtemp()) / "bili_wire_record.json"
    return plugin


def test_api_billion_status_and_wire_flow():
    plugin = _bili_plugin()
    payload = run(plugin._api_billion_status())
    assert payload["success"] is True
    assert payload["data"]["enabled"] is True
    assert payload["data"]["wire"] is None and payload["data"]["doctor"] == []

    # 未确认 → 返回预览，不做修改
    main_module.request = FakeRequest(json.dumps({"confirm": False}).encode())
    result = run(plugin._api_billion_wire())
    assert result["success"] is True
    assert any(
        row["field"] == "api_base"
        and row["new"] == "http://127.0.0.1:8787/bili/https://api.example.com/v1"
        for row in result["data"]["preview"]["changes"]
    )

    # 确认 → 接管成功，记录生成，状态与巡检可见
    main_module.request = FakeRequest(json.dumps({"confirm": True}).encode())
    result = run(plugin._api_billion_wire())
    assert result["success"] is True, result
    summary = run(plugin._api_billion_status())["data"]["wire"]
    assert summary["providers"] == ["main"]

    # 恢复需确认；确认后记录删除
    main_module.request = FakeRequest(json.dumps({}).encode())
    result = run(plugin._api_billion_restore())
    assert result["success"] is True and "confirm" in result["data"]["message"]
    main_module.request = FakeRequest(json.dumps({"confirm": True}).encode())
    result = run(plugin._api_billion_restore())
    assert result["success"] is True
    assert run(plugin._api_billion_status())["data"]["wire"] is None
    print("billion API 接管/恢复往返 ok")


def test_api_billion_wire_rejects_unhealthy():
    plugin = _bili_plugin()
    plugin.bili_proxy.state = "spawn_failed"
    main_module.request = FakeRequest(json.dumps({"confirm": True}).encode())
    result = run(plugin._api_billion_wire())
    assert result["success"] is False and "健康检查" in result["message"]
    print("健康不通过拒绝接管 ok")


def test_panel_save_bili_triggers_reevaluate():
    async def scenario():
        plugin = make_plugin(FakeAstrBotConfig())
        calls = []
        plugin.bili_proxy.stop = async_marker(calls, "stop")
        payload = await plugin._api_settings()
        main_module.request = FakeRequest(
            json.dumps(
                {"revision": payload["data"]["revision"], "values": {"bili.enabled": False}}
            ).encode("utf-8")
        )
        result = await plugin._api_settings_save()
        assert result["success"] is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # 停用不立即停代理：运行中客户端要等重载才切回原地址，代理保留到卸载
        assert calls == []

    run(scenario())
    print("面板保存 bili 触发重评估（停用不停代理）ok")


def async_marker(calls: list, name: str):
    async def stub():
        calls.append(name)

    return stub


if __name__ == "__main__":
    fns = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\n全部通过：{len(fns)} 项")
