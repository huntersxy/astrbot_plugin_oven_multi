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

import asyncio
import datetime
import random
import uuid
from collections import defaultdict
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import Provider


class ActiveReply:
    """主动回复功能

    支持概率模式和模型判定模式，在群聊中主动触发 LLM 回复。
    """

    def __init__(self):
        self.active_reply_stacks: dict[str, list[str]] = defaultdict(list)
        self.model_choice_histories: dict[str, list[str]] = defaultdict(list)

    def allow_active_reply(self, event: AstrMessageEvent, config: dict) -> bool:
        """检查是否允许主动回复

        Args:
            event: 消息事件
            config: 主动回复配置

        Returns:
            是否允许
        """
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict) or not ar.get("enable", False):
            return False
        if event.is_at_or_wake_command:
            return False
        whitelist = str(ar.get("whitelist", "") or "").strip()
        if whitelist:
            allowed = [x.strip() for x in whitelist.split(",") if x.strip()]
            if allowed:
                origin = event.unified_msg_origin
                gid = str(event.get_group_id() or "")
                if origin not in allowed and gid not in allowed:
                    return False
        return True

    def has_text_content(self, event: AstrMessageEvent) -> bool:
        """检查消息是否包含有效文本内容"""
        import astrbot.api.message_components as Comp

        msg = event.message_obj
        if not msg or not getattr(msg, "message", None):
            return False
        for comp in msg.message:
            if isinstance(comp, Comp.Plain):
                if (comp.text or "").strip():
                    return True
        return False

    async def should_active_reply(
        self, event: AstrMessageEvent, config: dict, context
    ) -> bool:
        """判断当前消息是否应该触发主动回复

        Args:
            event: 消息事件
            config: 插件配置
            context: AstrBot Context

        Returns:
            是否应该触发
        """
        if not self.allow_active_reply(event, config):
            return False
        if not self.has_text_content(event):
            return False

        ar = config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        mode = str(ar.get("mode", "probability") or "probability").strip()
        if mode == "model_choice":
            return await self._judge_model_choice_mode(event, config, context)

        # probability 模式
        possibility = ar.get("possibility", 0.1)
        sample = random.random()
        hit = sample < possibility
        logger.info(
            f"[烤箱-主动回复] probability | "
            f"origin={event.unified_msg_origin} "
            f"{'命中' if hit else '未命中'} "
            f"(sample={sample:.4f} threshold={possibility:.4f})"
        )
        return hit

    async def _judge_model_choice_mode(
        self, event: AstrMessageEvent, config: dict, context
    ) -> bool:
        """model_choice 模式：累积消息栈，满后触发 LLM 判定"""
        origin = event.unified_msg_origin
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        text = (event.get_message_str() or "").strip() or "[Empty]"
        nickname = event.message_obj.sender.nickname
        sender_id = event.get_sender_id()
        stack = self.active_reply_stacks[origin]
        history = self.model_choice_histories[origin]

        stack.append(f"[{nickname}/{sender_id}]: {text}")
        history.append(
            f"[{nickname}/{sender_id}/{datetime.datetime.now().strftime('%H:%M:%S')}]: {text}"
        )

        history_limit = max(
            60,
            ar.get("model_stack_size", 8) * 6,
            ar.get("model_history_messages", 0) * 6,
        )
        if len(history) > history_limit:
            del history[:-history_limit]

        if len(stack) < ar.get("model_stack_size", 8):
            logger.info(
                f"[烤箱-主动回复] model_choice | 栈填充 | "
                f"origin={origin} progress={len(stack)}/{ar.get('model_stack_size', 8)} "
                f"sender={sender_id}"
            )
            return False

        messages = stack[-ar.get("model_stack_size", 8):]
        stack.clear()
        return await self._judge_model_choice(event, origin, messages, config, context)

    def _resolve_provider(
        self, event: AstrMessageEvent, config: dict, context
    ) -> Optional[Provider]:
        """解析 model_choice 模式的判定用 Provider"""
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict):
            return None
        provider_id = str(ar.get("model_choice_provider_id", "") or "").strip()
        if provider_id:
            provider = context.get_provider_by_id(provider_id)
            if provider and isinstance(provider, Provider):
                return provider
        return context.get_using_provider(event.unified_msg_origin)

    async def _judge_model_choice(
        self,
        event: AstrMessageEvent,
        origin: str,
        messages: list[str],
        config: dict,
        context,
    ) -> bool:
        """调用 LLM 判断是否应该主动回复"""
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        history = self.model_choice_histories[origin]
        history_max = ar.get("model_history_messages", 0)
        history_lines = history[-history_max:] if history_max > 0 else []
        history_context = "\n".join(history_lines) if history_lines else "(无)"

        provider = self._resolve_provider(event, config, context)
        if not provider:
            return False

        prompt_tmpl = ar.get(
            "model_choice_prompt",
            "你正在群聊中扮演助手。以下是最近 {stack_size} 条群聊消息：\n{messages}\n\n"
            "额外历史上下文（最近 {history_count} 条）：\n{history_context}\n\n"
            "请判断你是否应该主动回复。如果需要回复，只输出 REPLY；如果不需要，只输出 SKIP。",
        )
        try:
            judge_prompt = prompt_tmpl.format(
                stack_size=len(messages),
                messages="\n".join(messages),
                history_count=len(history_lines),
                history_context=history_context,
            )
        except Exception:
            judge_prompt = (
                f"{prompt_tmpl}\n\n最近消息:\n{chr(10).join(messages)}\n\n"
                f"额外历史上下文({len(history_lines)}):\n{history_context}\n\n"
                "请仅输出 REPLY 或 SKIP。"
            )

        try:
            judge_resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=judge_prompt,
                    session_id=uuid.uuid4().hex,
                    persist=False,
                ),
                timeout=30,
            )
        except Exception as e:
            logger.warning(f"[烤箱-主动回复] 模型判定失败: {e}")
            return False

        decision = (judge_resp.completion_text or "").strip().upper()
        if decision.startswith("REPLY"):
            logger.info(
                f"[烤箱-主动回复] model_choice | 判定通过(REPLY) | "
                f"origin={origin} stack_size={len(messages)}"
            )
            return True
        logger.info(
            f"[烤箱-主动回复] model_choice | 判定拒绝(SKIP) | "
            f"origin={origin} stack_size={len(messages)} output={decision}"
        )
        return False
