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
"""离线冒烟：Jev 判读核心 + 判读上下文记录 + 被@判读 + @保留（不联网、不依赖 AstrBot 运行时）。

主动回复已于 1.50.0 移除（由烤箱之心接棒），相关触发测试随之删除；
本文件只覆盖保留能力：Jev 判读库、上下文记录、被@/唤醒判读注入、@保留与原生补记。

运行方式：
    python -m pytest tests/test_jev_reading.py -q
    python tests/test_jev_reading.py
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import types

# ---------------------------------------------------------------------------
# 运行时打桩：未安装 astrbot / aiohttp 时注入最小 stub（装了则直接用真模块）
# ---------------------------------------------------------------------------

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, os.path.dirname(_PLUGIN_DIR))  # 使插件包可按名导入


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
    """记录日志行，供断言「注入块全文」等输出。"""

    lines: list = []

    def _rec(self, *args, **kwargs):
        if args:
            _StubLogger.lines.append(str(args[0]))

    def debug(self, *args, **kwargs):
        self._rec(*args, **kwargs)

    def info(self, *args, **kwargs):
        self._rec(*args, **kwargs)

    def warning(self, *args, **kwargs):
        self._rec(*args, **kwargs)

    def error(self, *args, **kwargs):
        self._rec(*args, **kwargs)


class _StubPlain:
    def __init__(self, text: str = ""):
        self.text = text


class _StubAt:
    def __init__(self, qq, name: str = ""):
        self.qq = qq
        self.name = name


class _StubImage:
    pass


class _StubJson:
    pass


def _ensure_astrbot() -> None:
    with contextlib.suppress(Exception):
        import astrbot.api.message_components  # noqa: F401

        return
    _stub("astrbot")
    _stub("astrbot.api", logger=_StubLogger())
    _stub(
        "astrbot.api.message_components",
        Plain=_StubPlain,
        At=_StubAt,
        Image=_StubImage,
        Json=_StubJson,
    )
    _stub("astrbot.api.event", AstrMessageEvent=object, filter=object)
    _stub("astrbot.api.provider", Provider=object)


def _ensure_aiohttp() -> None:
    with contextlib.suppress(Exception):
        import aiohttp  # noqa: F401

        return

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None, **kwargs):
            self.total = total

    class ClientSession:
        def __init__(self, *args, **kwargs):
            self.closed = False

        async def close(self):
            self.closed = True

        def post(self, *args, **kwargs):
            raise AssertionError("测试中不允许发起真实网络请求")

    _stub(
        "aiohttp",
        ClientError=ClientError,
        ClientTimeout=ClientTimeout,
        ClientSession=ClientSession,
    )


_ensure_astrbot()
_ensure_aiohttp()

Plain = sys.modules["astrbot.api.message_components"].Plain
At = sys.modules["astrbot.api.message_components"].At

from astrbot_plugin_oven_multi.features.jev_judge import (  # noqa: E402
    INJECT_FOOTER,
    INJECT_HEADER,
    MENTION_INJECT_HEADER,
    build_questions,
    build_state,
    decide_reply,
    format_block,
    parse_answers,
)
from astrbot_plugin_oven_multi.features.jev_scenes import JevScenes  # noqa: E402
from astrbot_plugin_oven_multi.features.mention_preserve import (  # noqa: E402
    restore_bot_mention,
    should_backfill_native,
)

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

ANSWERS = {
    # choice 线上返回英文 key，parse 映射回中文展示
    "addressed": {"choice": "to_bot", "confidence": 0.9},
    "intent": {"choice": "asking_help", "confidence": 0.85},
    "emotion": {"choice": "neutral", "confidence": 0.8},
    "attitude": {"choice": "friendly", "confidence": 0.78},
    "expectancy": {"score": 2.0, "confidence": 0.72},  # score: 0=随口一说 … 2=期待即时回复
    "risk": {"score": 0.0, "confidence": 0.95},  # score: 0=安全 1=关注 2=高危
    "should_reply": {"noul": 0.88},  # noul: 主动回复概率（判读库通用能力，主动场景已移除）
}


class FakeSender:
    def __init__(self, nickname: str = "测试"):
        self.nickname = nickname
        self.user_id = "1"


class FakeMessageObj:
    def __init__(self, text: str, chain: list | None = None):
        self.message = chain if chain is not None else [Plain(text)]
        self.sender = FakeSender()
        self.group_id = "1001"
        self.message_str = text


class FakeEvent:
    def __init__(
        self,
        text: str,
        origin: str = "group_1",
        chain: list | None = None,
        platform: str = "aiocqhttp",
    ):
        self.is_at_or_wake_command = False
        self.unified_msg_origin = origin
        self.message_obj = FakeMessageObj(text, chain)
        self._text = text
        self.extras: dict = {}
        self._platform = platform

    def get_group_id(self):
        return "1001"

    def get_sender_id(self):
        return "1"

    def get_self_id(self):
        return "999"

    def get_message_str(self):
        return self.message_obj.message_str

    def get_platform_name(self):
        return self._platform

    def get_platform_id(self):
        return f"{self._platform}-main"

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)


def make_config(**jev_over):
    jev = {"enabled": True, "api_key": "test-key", **jev_over}
    return {"jev": jev}


def make_reading(answers=None, error=None, calls=None) -> JevScenes:
    """构造 JevScenes 并把 JevJudge.judge 换成假实现（记录调用、不联网）。"""
    scenes = JevScenes()

    async def fake_judge(cfg, state, with_decision=False):
        if calls is not None:
            calls.append({"state": state, "with_decision": with_decision, "cfg": cfg})
        if error is not None:
            return {"ok": False, "error": error}
        return parse_answers(answers if answers is not None else ANSWERS, with_decision)

    scenes.jev.judge = fake_judge
    return scenes


def run(coro):
    return asyncio.run(coro)


def _answers_with(**overrides):
    merged = {**ANSWERS, **overrides}
    return merged


# ---------------------------------------------------------------------------
# 判读核心
# ---------------------------------------------------------------------------


def test_parse_and_decide():
    result = parse_answers(ANSWERS, with_decision=True)
    assert result["ok"] is True
    assert result["addressed"] == "对机器人说"
    # score 维度：等级标签 + 序号
    assert result["expectancy"] == "期待即时回复"
    assert result["expectancy_level"] == 2
    assert result["risk"] == "安全"
    assert result["risk_level"] == 0
    # noul 判定：概率即“该回复”的把握
    assert result["should_reply"] == "回复"
    assert abs(result["should_reply_probability"] - 0.88) < 1e-9
    assert 0.0 < result["confidence"] <= 1.0

    assert decide_reply(result, 0.6) is True
    assert decide_reply(result, 0.9) is False  # 0.88 < 0.9 阈值

    low = parse_answers(_answers_with(**{"should_reply": {"noul": 0.3}}), True)
    assert low["should_reply"] == "不回复"
    assert decide_reply(low, 0.6) is False

    skip = parse_answers(_answers_with(**{"should_reply": {"noul": 0.1}}), True)
    assert decide_reply(skip, 0.6) is False

    # noul 缺失 → 判定无效
    missing = parse_answers(_answers_with(**{"should_reply": {}}), True)
    assert missing["ok"] is False
    assert decide_reply(missing, 0.6) is False

    # score 光谱位置四舍五入到最近等级并夹紧范围
    mid = parse_answers(_answers_with(**{"risk": {"score": 1.6, "confidence": 0.9}}))
    assert mid["risk"] == "高危" and mid["risk_level"] == 2
    edge = parse_answers(_answers_with(**{"risk": {"score": -1.0, "confidence": 0.9}}))
    assert edge["risk"] == "安全" and edge["risk_level"] == 0
    # score 节点缺失 → 该维无效（0.0 是合法分数，不能当缺失）
    no_score = parse_answers(_answers_with(**{"risk": {}}))
    assert no_score["risk"] == "" and no_score["risk_level"] == 0  # 回退 RISK_LEVEL 映射

    assert parse_answers({}, with_decision=True)["ok"] is False
    print("parse/decide ok")


def test_build_question_types():
    """题型按官方 primitives：有序光谱 score、是非 noul、无序集合 choice + 兜底项。"""
    questions = build_questions(with_decision=True)

    assert questions["risk"]["type"] == "score"
    # score criteria 是发给 Jev 的英文等级（低→高）；展示标签另存 DIMENSIONS
    assert questions["risk"]["criteria"] == [
        "Safe; no real risk",
        "Suspicious; worth watching",
        "High risk: scam, attack, harassment, or privacy trap",
    ]
    assert questions["expectancy"]["type"] == "score"
    assert questions["expectancy"]["criteria"] == [
        "Casual remark; no reply is expected",
        "Wants a reply eventually; not urgent",
        "Wants a reply right away",
    ]

    assert questions["should_reply"]["type"] == "noul"
    assert set(questions["should_reply"]["criteria"]) == {"true", "false"}
    # 面向 Jev 的指令为英文（CJK 精度低）
    assert questions["addressed"]["instructions"].startswith("Judge who")

    for key in ("addressed", "intent", "emotion", "attitude"):
        assert questions[key]["type"] == "choice"
        assert isinstance(questions[key]["criteria"], dict)
    # 线上选项 key 为英文（含兜底项）；parse 边界映射回中文
    assert "other" in questions["intent"]["criteria"]
    assert questions["intent"]["criteria"]["asking_help"].startswith("Asking a question")
    assert all(k.isascii() for k in questions["intent"]["criteria"])
    assert all(k.isascii() for k in questions["addressed"]["criteria"])

    # 不带判定时无 should_reply
    assert "should_reply" not in build_questions(with_decision=False)
    print("build_questions 题型 ok")


def test_format_block():
    result = parse_answers(ANSWERS)
    block = format_block(result)
    assert block.startswith(INJECT_HEADER)
    assert block.endswith(INJECT_FOOTER)
    assert "说话对象：对机器人说（0.90）" in block
    # score 维度按等级标签展示
    assert "期待回复：期待即时回复（0.72）" in block
    assert "风险：安全 ✅" in block
    assert "行动建议：" in block

    no_advice = format_block(result, include_advice=False, show_confidence=False)
    assert "行动建议" not in no_advice
    assert "（0.90）" not in no_advice

    truncated = format_block(result, max_chars=60)
    assert len(truncated) <= 80
    assert truncated.endswith(INJECT_FOOTER)
    assert format_block({"ok": False}) == ""
    print("format_block ok")


def test_format_block_mention_scene():
    """被@场景：独立标题 + 意图/情绪置顶重点行，正文不再重复这两维。"""
    result = parse_answers(ANSWERS)
    block = format_block(result, scene="mention")
    assert block.startswith(MENTION_INJECT_HEADER)
    assert block.endswith(INJECT_FOOTER)
    # 重点行置顶且在其他维度之前
    focus_idx = block.index("重点 · ")
    assert "意图 提问求助（0.85）" in block[focus_idx : focus_idx + 120]
    assert "情绪 中性（0.80）" in block[focus_idx : focus_idx + 120]
    assert focus_idx < block.index("说话对象：")
    # 正文不再出现「意图：」「情绪：」两维
    assert "意图：" not in block
    assert "情绪：" not in block
    # 其余维度保留
    assert "说话对象：对机器人说（0.90）" in block
    assert "风险：安全 ✅" in block
    print("format_block(mention) ok")


def test_build_state():
    lines = [
        "[甲/1 10:00:01]: 早上好",
        "[乙/2 10:00:02]: 今天下雨了",
        "[丙/3 10:00:03]: 我的手机号是13812345678",
    ]
    state = build_state(lines, history_rounds=6, max_chars=1200, redact=True)
    # 当前消息（最后一条）放最前，历史放 [Context]；脱敏生效
    assert "[Current]" in state
    assert state.index("[Current]") < state.index("[Context]")
    assert "13812345678" not in state and "[手机号]" in state
    assert "今天下雨了" in state

    # 历史裁剪：rounds=0 时只留当前消息
    state_no_ctx = build_state(lines, history_rounds=0, redact=False)
    assert "[Context]" not in state_no_ctx
    assert "早上好" not in state_no_ctx

    # 超长截断：被截掉的是旧历史，不是当前消息（预算需容下场景行+当前消息）
    long_lines = ["x" * 500, "y" * 500, "这是当前消息"]
    state_trimmed = build_state(long_lines, history_rounds=6, max_chars=300, redact=False)
    assert len(state_trimmed) <= 320
    assert "这是当前消息" in state_trimmed
    assert build_state([]) == ""
    print("build_state ok")


# ---------------------------------------------------------------------------
# 判读上下文记录（随群消息自动记录）
# ---------------------------------------------------------------------------


def test_record_history():
    scenes = make_reading()
    config = make_config()

    # jev 开启 → 记录（@消息也记）
    event = FakeEvent("@烤箱(999) 你好呀", origin="g_rec1")
    assert scenes.record_history(event, config) is True
    assert scenes.histories["g_rec1"][-1].endswith("@烤箱(999) 你好呀")

    # jev 关闭 → 不记录
    event = FakeEvent("普通消息", origin="g_rec2")
    assert scenes.record_history(event, make_config(enabled=False)) is False
    assert scenes.histories["g_rec2"] == []

    # 空文本 → 不记录
    event = FakeEvent("", origin="g_rec4")
    assert scenes.record_history(event, config) is False

    # 超限裁剪：history_rounds=6 → 缓冲上限 max(60, 36)=60
    for i in range(65):
        event = FakeEvent(f"第{i}条", origin="g_rec5")
        scenes.record_history(event, config)
    assert len(scenes.histories["g_rec5"]) == 60
    assert scenes.histories["g_rec5"][-1].endswith("第64条")
    print("record_history ok")


# ---------------------------------------------------------------------------
# 被@（唤醒）判读
# ---------------------------------------------------------------------------


def _mention_event(text: str, origin: str = "g_mention") -> FakeEvent:
    event = FakeEvent(text, origin=origin)
    event.is_at_or_wake_command = True
    return event


def test_mention_reading_success_with_context():
    calls: list = []
    scenes = make_reading(calls=calls)
    config = make_config()

    # 先记录两条历史（含当前消息本身，真实流程由最高优先级 handler 完成）
    scenes.record_history(FakeEvent("群里刚才在聊乐队巡演", origin="g_m_ok"), config)
    event = _mention_event("@烤箱(999) 你觉得这个乐队怎么样", origin="g_m_ok")
    scenes.record_history(event, config)

    block = run(scenes.mention_reading(event, config))
    assert block and block.startswith(MENTION_INJECT_HEADER)
    assert "重点 · " in block
    assert len(calls) == 1
    assert calls[0]["with_decision"] is False
    assert "[Context]" in calls[0]["state"]  # 带上了前文
    assert "乐队巡演" in calls[0]["state"]
    # 判读结果不写回事件 extra（由 on_llm_request 直接注入）
    assert event.get_extra("oven_jev_reading") is None
    print("被@判读带上下文 ok")


def test_mention_reading_gates():
    calls: list = []
    scenes = make_reading(calls=calls)
    config = make_config()

    # 非唤醒消息 → 不判
    event = FakeEvent("普通一句话", origin="g_m_gate")
    assert run(scenes.mention_reading(event, config)) is None

    # 指令 → 不判
    event = _mention_event("/help 查帮助", origin="g_m_gate")
    assert run(scenes.mention_reading(event, config)) is None

    # 裸 @（去掉 @标记 后无正文）→ 不判
    event = _mention_event("@烤箱(999)", origin="g_m_gate")
    assert run(scenes.mention_reading(event, config)) is None

    # inject_on_mention 关 → 不判
    event = _mention_event("@烤箱(999) 在吗", origin="g_m_gate")
    assert run(scenes.mention_reading(event, make_config(inject_on_mention=False))) is None

    # jev 总开关关 → 不判
    event = _mention_event("@烤箱(999) 在吗", origin="g_m_gate")
    assert run(scenes.mention_reading(event, make_config(enabled=False))) is None

    assert calls == []
    print("被@判读闸门 ok")


def test_mention_reading_failure_degrades():
    scenes = make_reading(error="网络错误")
    event = _mention_event("@烤箱(999) 在吗", origin="g_m_fail")
    assert run(scenes.mention_reading(event, make_config())) is None
    print("被@判读失败降级 ok")


def test_mention_reading_history_empty_fallback():
    calls: list = []
    scenes = make_reading(calls=calls)
    config = make_config()
    event = _mention_event("@烤箱(999) 单独一句没人接话", origin="g_m_empty")

    block = run(scenes.mention_reading(event, config))
    assert block and block.startswith(MENTION_INJECT_HEADER)
    assert len(calls) == 1
    assert "[Current]" in calls[0]["state"]
    print("被@判读空历史兜底 ok")


def test_mention_reading_short_after_strip_skips():
    calls: list = []
    scenes = make_reading(calls=calls)
    config = make_config(min_message_chars=2)
    event = _mention_event("@烤箱(999) 嗯", origin="g_m_short2")
    # "嗯" 仅1字 < 2 → 跳过（去掉 @标记 后判定）
    assert run(scenes.mention_reading(event, config)) is None
    assert calls == []
    print("被@判读短消息 ok")


# ---------------------------------------------------------------------------
# @保留 与 原生群上下文补记
# ---------------------------------------------------------------------------


def test_restore_bot_mention():
    # 正常：@机器人 + 正文 → 补回 message_str，双写 event/message_obj
    chain = [At(qq="999", name="烤箱"), Plain("你好")]
    event = FakeEvent("你好", chain=chain)
    assert restore_bot_mention(event) is True
    assert event.message_obj.message_str.startswith("@烤箱(999) ")
    assert event.get_message_str().startswith("@烤箱(999) ")
    assert event.get_extra("oven_mention_preserved") is True
    # 幂等：只补一次
    assert restore_bot_mention(event) is False

    # 正文已含 @(第二个@) → 不重复补
    chain = [At(qq="999", name="烤箱"), Plain("@烤箱(999) 再问一次")]
    event = FakeEvent("@烤箱(999) 再问一次", chain=chain)
    assert restore_bot_mention(event) is False

    # 裸 @：无正文 → 不处理（留给原生空@流程）
    event = FakeEvent("", chain=[At(qq="999", name="烤箱")])
    assert restore_bot_mention(event) is False

    # 非 aiocqhttp → 不处理
    chain = [At(qq="999", name="烤箱"), Plain("你好")]
    event = FakeEvent("你好", chain=chain, platform="telegram")
    assert restore_bot_mention(event) is False

    # 无 @机器人 段 → 不处理
    event = FakeEvent("你好", chain=[Plain("你好")])
    assert restore_bot_mention(event) is False
    print("restore_bot_mention ok")


def test_should_backfill_native():
    # 纯 @机器人（裸@或仅 At 组件）→ 需要补记
    event = FakeEvent("", chain=[At(qq="999", name="烤箱")])
    assert should_backfill_native(event) is True

    # @ + 表情（无 Plain/Image/Json）→ 需要补记
    event = FakeEvent("", chain=[At(qq="999", name="烤箱"), Plain("")])
    assert should_backfill_native(event) is False  # 有 Plain 组件（空文本按原生口径有组件即记）

    # @ + 正文 → 原生会记录，不需要补
    event = FakeEvent("你好", chain=[At(qq="999", name="烤箱"), Plain("你好")])
    assert should_backfill_native(event) is False

    # 无 @机器人 → 不补
    event = FakeEvent("你好", chain=[Plain("你好")])
    assert should_backfill_native(event) is False
    print("should_backfill_native ok")


# ---------------------------------------------------------------------------
# 原生持久化历史读取（上下文优先走 AstrBot 自己的记录）
# ---------------------------------------------------------------------------


class FakeNativeRecord:
    def __init__(self, rid, sender_name, content, created_at=None):
        self.id = rid
        self.sender_name = sender_name
        self.content = content
        self.created_at = created_at


class FakeHistoryManager:
    def __init__(self, records):
        self.records = records

    async def get(self, platform_id=None, user_id=None, page_size=20):
        # 模拟真实现：db 取最近 page_size 条（倒序），manager.reverse() 成升序
        latest = list(reversed(self.records))[:page_size]
        return list(reversed(latest))


class FakeStarContext:
    def __init__(self, records, history_enable=True):
        self.message_history_manager = FakeHistoryManager(records)
        self._history_enable = history_enable

    def get_config(self, umo=None):
        return {"provider_ltm_settings": {"group_message_history_enable": self._history_enable}}


def _native_records():
    return [
        FakeNativeRecord(
            1, "甲", {"type": "user", "message": [{"type": "plain", "text": "早上想吃面包"}]}
        ),
        FakeNativeRecord(
            2, "bot", {"type": "bot", "message": [{"type": "plain", "text": "那挺好的"}]}
        ),
        # At 记录：格式化后不带 QQ 号
        FakeNativeRecord(
            3,
            "乙",
            {
                "type": "user",
                "message": [
                    {"type": "at", "user_id": "2971840857", "name": "烤箱"},
                    {"type": "plain", "text": "问问机器人"},
                ],
            },
        ),
    ]


def test_native_history_preferred():
    """原生「平台消息历史」开启时优先读它：升序、不含当前条、无 QQ 号。"""
    scenes = make_reading()
    scenes.context = FakeStarContext(_native_records())
    config = make_config()
    event = FakeEvent("@烤箱(999) 今天天气如何", origin="g_native")
    event.set_extra("_current_platform_message_history_id", 50)  # 3 条都早于当前

    lines, source = run(scenes._state_lines(event, config, "g_native", 6))
    assert source == "原生历史"
    assert len(lines) == 4  # 3 条原生 + 当前消息行
    assert "早上想吃面包" in lines[0]
    assert "[bot" in lines[1] and "那挺好的" in lines[1]
    assert "@烤箱" in lines[2] and "问问机器人" in lines[2]
    assert lines[-1].endswith("今天天气如何")  # 末尾恒为当前消息
    # QQ 号不进 state
    assert "2971840857" not in "\n".join(lines)
    print("原生历史优先 ok")


def test_native_history_fallback_to_buffer():
    """原生开关关闭时回退插件缓冲。"""
    scenes = make_reading()
    scenes.context = FakeStarContext(_native_records(), history_enable=False)
    config = make_config()
    event = FakeEvent("普通消息", origin="g_fb")
    scenes.record_history(event, config)

    lines, source = run(scenes._state_lines(event, config, "g_fb", 6))
    assert source == "插件缓冲"
    assert any("普通消息" in x for x in lines)

    # context=None（离线/异常）同样回退
    scenes.context = None
    lines, source = run(scenes._state_lines(event, config, "g_fb", 6))
    assert source == "插件缓冲"
    print("原生历史回退缓冲 ok")


# ---------------------------------------------------------------------------
# 注入块全文日志
# ---------------------------------------------------------------------------


def test_debug_logs_injection_block():
    """debug_mode 开启时输出被@判读的最终注入块全文（含行动建议）。"""
    _StubLogger.lines.clear()
    scenes = make_reading()
    config = make_config(debug_mode=True)
    event = _mention_event("@烤箱(999) 帮我看道题", origin="g_dbg_block")

    from unittest.mock import patch

    import astrbot_plugin_oven_multi.features.jev_scenes as scenes_module

    with patch.object(scenes_module, "logger", _StubLogger()):
        block = run(scenes.mention_reading(event, config))
    assert block and "行动建议" in block
    hits = [line for line in _StubLogger.lines if "注入块全文" in line]
    assert hits, "未找到注入块全文日志"
    print("注入块全文日志 ok")


if __name__ == "__main__":
    fns = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\n全部通过：{len(fns)} 项")
