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
# Modified from astrbot_plugin_astrbot_enhance_mode (AGPL-3.0) by 阿汐

import asyncio
import datetime
import random
import uuid
from collections import defaultdict

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import Provider


class ActiveReply:
    """主动回复：概率模式随机触发；模型判定模式累积消息栈后由 LLM 决策。"""

    def __init__(self):
        self.stacks: dict[str, list[str]] = defaultdict(list)
        self.histories: dict[str, list[str]] = defaultdict(list)

    async def should_active_reply(self, event: AstrMessageEvent, config: dict, context) -> bool:
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict) or not ar.get("enable", False):
            return False
        if event.is_at_or_wake_command:
            return False

        whitelist = str(ar.get("whitelist", "") or "").strip()
        if whitelist:
            allowed = [x.strip() for x in whitelist.split(",") if x.strip()]
            origin = event.unified_msg_origin
            gid = str(event.get_group_id() or "")
            if allowed and origin not in allowed and gid not in allowed:
                return False

        msg = event.message_obj
        if not msg or not getattr(msg, "message", None):
            return False
        if not any(
            isinstance(comp, Comp.Plain) and (comp.text or "").strip()
            for comp in msg.message
        ):
            return False

        mode = str(ar.get("mode", "probability") or "probability").strip()
        if mode == "model_choice":
            return await self._judge_model_choice_mode(event, config, context)

        hit = random.random() < ar.get("possibility", 0.1)
        logger.info(
            f"[烤箱-主动回复] probability | origin={event.unified_msg_origin} "
            f"{'命中' if hit else '未命中'}"
        )
        return hit

    async def _judge_model_choice_mode(
        self, event: AstrMessageEvent, config: dict, context
    ) -> bool:
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        origin = event.unified_msg_origin
        nickname = event.message_obj.sender.nickname
        sender_id = event.get_sender_id()
        text = (event.get_message_str() or "").strip() or "[Empty]"
        now = datetime.datetime.now().strftime("%H:%M:%S")

        self.stacks[origin].append(f"[{nickname}/{sender_id}]: {text}")
        self.histories[origin].append(f"[{nickname}/{sender_id}/{now}]: {text}")

        stack_size = int(ar.get("model_stack_size", 8) or 8)
        history_limit = max(60, stack_size * 6, int(ar.get("model_history_messages", 0) or 0) * 6)
        if len(self.histories[origin]) > history_limit:
            del self.histories[origin][:-history_limit]

        if len(self.stacks[origin]) < stack_size:
            logger.info(
                f"[烤箱-主动回复] model_choice | 栈填充 | origin={origin} "
                f"progress={len(self.stacks[origin])}/{stack_size}"
            )
            return False

        messages = self.stacks[origin][-stack_size:]
        self.stacks[origin].clear()
        return await self._judge_model_choice(event, origin, messages, config, context)

    def _resolve_provider(self, event: AstrMessageEvent, config: dict, context):
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
        self, event: AstrMessageEvent, origin: str, messages: list[str], config: dict, context
    ) -> bool:
        ar = config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        history_max = int(ar.get("model_history_messages", 0) or 0)
        history_lines = self.histories[origin][-history_max:] if history_max > 0 else []
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
        passed = decision.startswith("REPLY")
        logger.info(
            f"[烤箱-主动回复] model_choice | 判定"
            f"{'通过(REPLY)' if passed else '拒绝(SKIP)'} "
            f"| origin={origin} stack_size={len(messages)} output={decision}"
        )
        return passed
