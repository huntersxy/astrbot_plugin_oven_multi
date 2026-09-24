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
#
# 保留「@机器人」文本：aiocqhttp 适配器构造 message_str 时会跳过第一个指向
# 机器人的 At 段（At 组件本身保留在消息链里），导致模型正文看不到被 @。
# 本模块把 @文本 补回 message_str（不动消息链），并提供与 AstrBot 原生
# 「群聊消息记录注入上下文」记录闸门一致的谓词。
# 实现思路参考 astrbot_plugin_qq_group_enhance（MIT）by rytte，按本插件场景独立实现。

from __future__ import annotations

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

# 事件 extra 标记：本轮已补回 @文本
MENTION_PRESERVED_KEY = "oven_mention_preserved"


def _find_bot_at(event: AstrMessageEvent):
    """返回消息链中第一个指向机器人的 At 段；没有则返回 None。"""
    try:
        messages = getattr(event.message_obj, "message", None) or []
        self_id = str(event.get_self_id())
        for comp in messages:
            if isinstance(comp, Comp.At) and str(comp.qq) == self_id:
                return comp
    except Exception:  # noqa: BLE001
        return None
    return None


def restore_bot_mention(event: AstrMessageEvent) -> bool:
    """把被适配器丢弃的首个 @机器人 补回 message_str。

    仅在 aiocqhttp 生效（其余适配器的正文本就包含 @ 文本）；无正文（裸 @）
    或已包含该 @ 时不处理。返回是否发生了补回。
    """
    try:
        if event.get_platform_name() != "aiocqhttp":
            return False
        if event.get_extra(MENTION_PRESERVED_KEY, False):
            return False
        mention = _find_bot_at(event)
        if mention is None:
            return False

        text = (event.get_message_str() or "").strip()
        if not text:
            # 裸 @：无正文可补，走 AstrBot 原生空@流程
            return False

        self_id = str(event.get_self_id())
        if f"({self_id})" in text:
            # 第二个及以后的 @机器人 由适配器正常写入正文，勿重复补
            return False

        nickname = str(getattr(mention, "name", "") or "").strip() or "机器人"
        # 与适配器对其它 @ 的写法保持一致：@昵称(qq)
        restored = f"@{nickname}({self_id}) {text}"
        event.message_str = restored
        if getattr(event, "message_obj", None) is not None:
            event.message_obj.message_str = restored
        event.set_extra(MENTION_PRESERVED_KEY, True)
        return True
    except Exception:  # noqa: BLE001
        return False


def content_components(event: AstrMessageEvent) -> tuple:
    """顶层消息组件（与 AstrBot 原生 _iter_message_components 一致）。"""
    messages = getattr(getattr(event, "message_obj", None), "message", None)
    if not isinstance(messages, (list, tuple)):
        return ()
    return tuple(messages)


def should_backfill_native(event: AstrMessageEvent) -> bool:
    """判断是否需要为本消息补齐原生「群聊消息记录注入上下文」记录。

    原生记录要求消息含 Plain/Image/Json 组件且事件未被拦截；「纯 @机器人」
    消息（如只发一个 @，或 @ + 表情/引用）两项都不满足，会被原生漏记，
    导致「先发一句话、再单独 @机器人」带不上前文。仅对这类消息返回 True；
    是否真的补记还应检查 group_icl_enable 与 _group_context_record_id 去重。
    """
    if _find_bot_at(event) is None:
        return False
    for comp in content_components(event):
        if isinstance(comp, (Comp.Plain, Comp.Image, Comp.Json)):
            return False
    return True
