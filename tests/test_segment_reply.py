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
"""离线测试：分段回复引擎（纯文本规则，无 astrbot / 网络依赖）。

运行方式：
    python -m pytest tests/test_segment_reply.py -q
    python tests/test_segment_reply.py
"""

from __future__ import annotations

import os
import sys
import types

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, os.path.dirname(_PLUGIN_DIR))


def _stub(name: str, **attrs):
    """分段引擎本身只用标准库；这里只为让 features 包的 __init__ 可导入。"""
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


try:
    import astrbot.api  # noqa: F401
except Exception:  # noqa: BLE001
    _stub("astrbot")
    _stub("astrbot.api", logger=types.SimpleNamespace(
        debug=lambda *a, **k: None, info=lambda *a, **k: None,
        warning=lambda *a, **k: None, error=lambda *a, **k: None,
    ))
    _stub("astrbot.api.message_components", Plain=object, At=object, Image=object, Json=object)
    _stub("astrbot.api.event", AstrMessageEvent=object, filter=object)

from astrbot_plugin_oven_multi.features.segment_reply import (  # noqa: E402
    SegmentReply,
)

engine = SegmentReply()


def cfg(**over):
    """默认配置（min/max 放宽，专测单项行为时再覆盖）。"""
    base = {
        "min_length": 1,
        "max_length": 50,
        "allow_exceed_max": True,
        "hard_max_limit": 100,
        "merge_short_tail": False,
        "short_tail_threshold": 8,
        "keep_symbol": True,
        "extend_to_trailing_symbols": True,
        "protect_paired_symbols": False,
        "force_split_symbols": [],
        "split_symbols": ["。", "，"],
        "delay_type": "random",
        "random_delay_range": [1, 3],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 强制分隔符
# ---------------------------------------------------------------------------


def test_force_split_symbols():
    text = "第一段\n\n第二段\n第三段"
    assert engine.segment(text, cfg()) == ["第一段", "第二段", "第三段"]


def test_force_split_symbol_escape_and_repeat_run():
    # 配置里的 \n 转义为真实换行；连续重复符号视为一个整体
    assert engine.segment("甲\n\n\n乙", cfg(force_split_symbols=["\\n"])) == ["甲", "乙"]


def test_force_split_keep_symbol_false_drops_semicolon():
    # 非空白分隔符在 keep_symbol=False 时被丢弃
    out = engine.segment(
        "甲；乙；丙",
        cfg(force_split_symbols=["；"], keep_symbol=False),
    )
    assert out == ["甲", "乙", "丙"]


# ---------------------------------------------------------------------------
# 字数规则分段
# ---------------------------------------------------------------------------


def test_length_bounds_respected():
    text = "一二三四五。六七八九十。十一二三四。五六七八九。十字"
    out = engine.segment(text, cfg(min_length=3, max_length=10))
    assert len(out) >= 2
    for seg in out:
        assert len(seg) <= 10 + 1  # 允许保留一个结尾标点的误差
    assert "".join(seg.rstrip("。，") for seg in out).startswith("一二三四五")
    # 不丢字：所有正文内容都在结果里
    joined = "".join(out)
    for ch in "一二三四五六七八九十十一二三四五六七八九十十字":
        assert ch in joined


def test_symbol_priority_stronger_pause_wins():
    # 窗口内句号与逗号并存时，优先按句号（优先级更高）切
    text = "甲乙丙丁戊。己庚辛壬，癸"
    out = engine.segment(text, cfg(min_length=2, max_length=10))
    assert out[0] == "甲乙丙丁戊。"


def test_window_picks_latest_symbol_in_range():
    # 范围内只有逗号时按逗号切；句号在窗外不影响
    text = "甲乙丙丁戊，己庚辛壬。癸"
    out = engine.segment(text, cfg(min_length=2, max_length=10))
    assert out[0] == "甲乙丙丁戊，"


def test_allow_exceed_max_hard_cut():
    text = "这是一段没有标点的很长文字超过最大长度了"
    # 允许超出：找不到标点时切在硬性上限
    out = engine.segment(text, cfg(min_length=5, max_length=10, hard_max_limit=15))
    assert out[0] == text[:15]
    # 不允许超出：切在最大字数处
    out = engine.segment(text, cfg(min_length=5, max_length=10, allow_exceed_max=False))
    assert out[0] == text[:10]


def test_hard_max_below_max_gets_raised():
    text = "没有标点的一段文字" * 4  # 36 字
    out = engine.segment(text, cfg(min_length=5, max_length=10, hard_max_limit=5))
    assert len(out[0]) == 30  # max_length + 20


def test_min_greater_than_max_clamped():
    text = "甲乙丙丁戊己庚辛"
    out = engine.segment(text, cfg(min_length=30, max_length=4))
    assert out == ["甲乙丙丁戊己庚辛"]  # 全文短于钳制后的 max，不分段


def test_keep_symbol_false():
    out = engine.segment(
        "甲乙丙丁。戊己庚。",
        cfg(min_length=2, max_length=5, keep_symbol=False),
    )
    # 切分点丢弃标点；末段未被切分，结尾句号原样保留
    assert out == ["甲乙丙丁", "戊己庚。"]


def test_extend_trailing_symbols():
    out = engine.segment(
        "真的吗？！！下一段",
        cfg(min_length=2, max_length=6, split_symbols=["？"]),
    )
    # 命中 ？ 后顺延吞掉 ！！，下一段干净开头
    assert out[0] == "真的吗？！！"
    assert out[1] == "下一段"


def test_protect_paired_symbols():
    text = "这是（括号里的内容，逗号）外面的句子"
    protected = engine.segment(text, cfg(min_length=2, max_length=8, protect_paired_symbols=True))
    assert protected == [text]  # 括号内逗号受保护，无处可切 → 整段
    free = engine.segment(text, cfg(min_length=2, max_length=8))
    assert free[0] == "这是（括号里的内容，"  # 未保护时按逗号切


def test_split_char_len_stops_at_open_bracket():
    # 顺延遇到开括号即停，开括号属于下一句
    out = engine.segment(
        "上午的！（/抱着终端）后面",
        cfg(min_length=2, max_length=6, split_symbols=["！"]),
    )
    assert out[0] == "上午的！"
    assert out[1].startswith("（")


# ---------------------------------------------------------------------------
# 短尾合并
# ---------------------------------------------------------------------------


def test_merge_short_tail():
    text = "甲乙丙丁。戊己庚辛。壬"
    over = {"min_length": 2, "max_length": 5}
    merged = engine.segment(text, cfg(merge_short_tail=True, short_tail_threshold=2, **over))
    assert merged == ["甲乙丙丁。", "戊己庚辛。壬"]
    unmerged = engine.segment(text, cfg(merge_short_tail=False, **over))
    assert unmerged == ["甲乙丙丁。", "戊己庚辛。", "壬"]


# ---------------------------------------------------------------------------
# 跳过闸门
# ---------------------------------------------------------------------------


def test_should_skip_keywords_and_length():
    assert engine.should_skip("菜单\n/ping 测试\n/status 状态", cfg()) is True
    assert engine.should_skip("HELP\n/ping Test\n/status Status", cfg()) is True  # 不区分大小写
    assert engine.should_skip("普通回复内容", cfg()) is False
    assert engine.should_skip("很短", cfg(skip_longer_than=2)) is False
    assert engine.should_skip("超长的内容", cfg(skip_longer_than=2)) is True
    assert engine.should_skip("任意长度", cfg(skip_longer_than=0)) is False


# ---------------------------------------------------------------------------
# 延迟
# ---------------------------------------------------------------------------


def test_delay_fixed():
    assert engine.delay("上一段", "下一段", cfg(delay_type="fixed", fixed_delay_seconds=1.5)) == 1.5
    # 负值钳为 0
    assert engine.delay("a", "b", cfg(delay_type="fixed", fixed_delay_seconds=-2)) == 0.0


def test_delay_random_in_range():
    c = cfg(delay_type="random", random_delay_range=[0.5, 1.0])
    for _ in range(30):
        assert 0.5 <= engine.delay("a", "b", c) <= 1.0


def test_delay_random_range_swapped():
    c = cfg(delay_type="random", random_delay_range=[3, 1])
    for _ in range(10):
        assert 1.0 <= engine.delay("a", "b", c) <= 3.0


def test_delay_random_invalid_range_falls_back():
    c = cfg(delay_type="random", random_delay_range="bad")
    assert 1.0 <= engine.delay("a", "b", c) <= 3.0


def test_delay_smart_non_negative():
    c = cfg(delay_type="smart")
    for _ in range(30):
        assert engine.delay("为什么？", "嗯……让我想想，好的！", c) >= 0.0


def test_delay_unknown_type_uses_random():
    c = cfg(delay_type="per_char", random_delay_range=[0.5, 0.6])
    assert 0.5 <= engine.delay("a", "b", c) <= 0.6


if __name__ == "__main__":
    fns = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\n全部通过：{len(fns)} 项")


def test_help_in_ordinary_reply_does_not_disable_segmentation():
    text = "help 是问我能干什么吧\n\n我能帮你查群成员和群资料\n其他的直接把想要的说出来就行"
    assert not engine.should_skip(text, cfg())
    assert len(engine.segment(text, cfg())) >= 3
    assert not engine.should_skip("需要帮助的话可以说，我把菜单说明一下", cfg())
    assert engine.should_skip("普通回复CUSTOM内容", cfg(exclude_keywords=["CUSTOM"]))
