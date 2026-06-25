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
# Modified from:
#   - astrbot_plugin_pairit (AGPL-3.0) by GamerNoTitle — bracket matching
#   - astrbot_plugin_astrbot_enhance_mode by 阿汐 — active reply, model_choice
#   - astrbot_plugin_repetition by FengYing1314 — message repetition
#   - astrbot_plugin_iamthinking (AGPL-3.0) by sssn-tech — thinking emoji reaction
#   - astrbot_plugin_iearning_style (AGPL-3.0) by qa296 — style learning integration
#   - astrbot_plugin_remove_blank_lines (MIT) by Codex — remove blank lines from LLM output
# Date: 2026-06-23

import asyncio
import datetime
import json
import random
import re
import uuid
from collections import defaultdict

from quart import jsonify

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import Provider
import astrbot.api.message_components as Comp

from .learning_style.data_manager import DataManager as StyleDataManager
from .learning_style.learning_manager import LearningManager
from .learning_style.scheduler import Scheduler as StyleScheduler
from .learning_style.style_injector import StyleInjector
from .balance_checker import BalanceChecker

PLUGIN_NAME = "astrbot_plugin_oven_multi"


def collapse_blank_lines(text, max_newlines=1):
    limit = max(int(max_newlines), 0)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{" + str(limit + 1) + r",}", "\n" * limit, normalized)


PAIR_LIST = {
    "(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
    "<": ">", ">": "<",
    "（": "）", "）": "（",
    "［": "］", "］": "［",
    "｛": "｝", "｝": "｛",
    "〈": "〉", "〉": "〈",
    "《": "》", "》": "《",
    "【": "】", "】": "【",
    "〖": "〗", "〗": "〖",
    "〔": "〕", "〕": "〔",
    "「": "」", "」": "「",
    "『": "』", "』": "『",
    "｢": "｣", "｣": "｢",
    "“": "”", "”": "“",
    "‘": "’", "’": "‘",
    "‚": "‛", "‛": "‚",
    "〘": "〙", "〙": "〘",
    "〚": "〛", "〛": "〚",
}


class BracketMatcher:
    def check(self, content):
        stack = []
        for char in content:
            if char in PAIR_LIST:
                if stack and stack[-1] == PAIR_LIST[char]:
                    stack.pop()
                else:
                    stack.append(char)
        return "".join([PAIR_LIST[c] for c in reversed(stack)]) if stack else None


class Repeater:
    def __init__(self):
        self.last = defaultdict(str)
        self.count = defaultdict(int)

    def check(self, session_id, message, config):
        if not config.get("enabled"):
            return None
        if any(isinstance(m, Comp.Poke) for m in message):
            return None
        msg_id = str([str(m) for m in message])
        threshold = config.get("repeat_threshold", 2)
        if msg_id == self.last[session_id]:
            self.count[session_id] += 1
            if self.count[session_id] >= threshold - 1:
                if __import__('random').random() < config.get("break_spell_probability", 0.3):
                    return ("break", config.get("break_spell_text", "打断施法！"))
                else:
                    chain = [Comp.Image.fromURL(m.url) if isinstance(m, Comp.Image) else m for m in message]
                    return ("repeat", chain)
        else:
            self.count[session_id] = 0
        self.last[session_id] = msg_id
        return None


class ThinkingManager:
    def is_aiocqhttp(self, event):
        return getattr(event, "platform_meta", None) and event.get_platform_name() == "aiocqhttp" and bool(event.get_group_id())

    async def emoji(self, event, msg_id, ids, set_):
        if not ids:
            return
        bot = getattr(event, "bot", None)
        if bot and hasattr(bot, "set_msg_emoji_like"):
            for eid in set(ids):
                try:
                    await bot.set_msg_emoji_like(message_id=msg_id, emoji_id=eid, set=set_)
                except:
                    pass


@register(PLUGIN_NAME, "汐兮雨", "插座的多功能烤箱", "1.20.0")
class OvenMultiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)

        if config is None:
            config = AstrBotConfig({})
        self.config = config

        self.matcher = BracketMatcher()
        self.repeater = Repeater()
        self.thinking = ThinkingManager()

        # 风格学习
        self.style_data_manager = None
        self.style_learning_manager = None
        self.style_scheduler = None
        self.style_injector = None
        self._init_style_learning()

        # 主动回复
        self.active_reply_stacks: dict[str, list[str]] = defaultdict(list)
        self.model_choice_histories: dict[str, list[str]] = defaultdict(list)

        # 余额查询
        self.balance_checker = BalanceChecker(self.config)

        # 注册 Web API
        self._register_web_api()

    def _register_web_api(self):
        """注册 Web API 用于插件页面"""
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/style_status",
            self._api_style_status,
            ["GET"],
            "获取风格学习状态",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/balance",
            self._api_balance,
            ["GET"],
            "获取余额信息",
        )
    async def _api_balance(self):
        """获取余额查询结果"""
        results = await self.balance_checker.query_all()
        return jsonify({"success": True, "data": results})

    async def _api_style_status(self):
        """获取所有会话的完整风格学习数据"""
        if not self.style_data_manager:
            return jsonify({"success": True, "data": {}})

        result = {}
        for session_id in self.style_data_manager.universal:
            universal = self.style_data_manager.get_universal_for_session(session_id)
            history = self.style_data_manager.get_chat_history(session_id, limit=50)

            result[session_id] = {
                "session_id": session_id,
                "display_name": session_id.split("_")[-1] if "_" in session_id else session_id,
                "universal": universal,
                "history": history,
            }

        return jsonify({"success": True, "data": result})

    def _init_style_learning(self):
        cfg = self.config.get("style_learning", {})
        if isinstance(cfg, dict) and cfg.get("enabled", True):
            try:
                data_dir = StarTools.get_data_dir(PLUGIN_NAME)
                self.style_data_manager = StyleDataManager(data_dir, self.config)
                self.style_learning_manager = LearningManager(self, self.style_data_manager, self.config)
                self.style_scheduler = StyleScheduler(self.style_data_manager, self.style_learning_manager, self.config)
                self.style_injector = StyleInjector(self.style_data_manager, self.config)

                logger.info("[烤箱-风格学习] 初始化完成")
            except Exception as e:
                logger.error(f"[烤箱-风格学习] 初始化失败: {e}")

    async def initialize(self):
        if self.style_scheduler:
            self.style_scheduler.start()
        logger.info(f"[插座烤箱] 启动")

    async def terminate(self):
        if self.style_scheduler:
            await self.style_scheduler.stop()
        if self.style_data_manager:
            await self.style_data_manager.force_save()

    async def _send_bracket_reply(self, event: AstrMessageEvent, brackets: str):
        """异步发送括号补全，不阻塞事件流"""
        try:
            await event.send(event.plain_result(brackets))
        except Exception as e:
            logger.error(f"[插座烤箱] 发送括号补全失败: {e}")

    async def _send_repeat_reply(self, event: AstrMessageEvent, result):
        """异步发送复读/打断消息，不阻塞事件流"""
        try:
            if result[0] == "break":
                await event.send(event.plain_result(result[1]))
            elif result[0] == "repeat":
                await event.send(event.chain_result(result[1]))
        except Exception as e:
            logger.error(f"[插座烤箱] 发送复读消息失败: {e}")

    # ==================== 主动回复 ====================

    def _allow_active_reply(self, event: AstrMessageEvent) -> bool:
        """检查是否允许主动回复"""
        ar = self.config.get("active_reply", {})
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

    def _resolve_model_choice_provider(self, event: AstrMessageEvent) -> Provider | None:
        """解析 model_choice 模式的判定用 Provider"""
        ar = self.config.get("active_reply", {})
        if not isinstance(ar, dict):
            return None
        provider_id = str(ar.get("model_choice_provider_id", "") or "").strip()
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider and isinstance(provider, Provider):
                return provider
        return self.context.get_using_provider(event.unified_msg_origin)

    async def _judge_model_choice(
        self, event: AstrMessageEvent, origin: str, messages: list[str]
    ) -> bool:
        """调用 LLM 判断是否应该主动回复"""
        ar = self.config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        history = self.model_choice_histories[origin]
        history_max = ar.get("model_history_messages", 0)
        history_lines = history[-history_max:] if history_max > 0 else []
        history_context = "\n".join(history_lines) if history_lines else "(无)"

        provider = self._resolve_model_choice_provider(event)
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

    async def _need_active_reply_model_choice(self, event: AstrMessageEvent) -> bool:
        """model_choice 模式：累积消息栈，满后触发 LLM 判定"""
        origin = event.unified_msg_origin
        ar = self.config.get("active_reply", {})
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

        messages = stack[-ar.get("model_stack_size", 8) :]
        stack.clear()
        return await self._judge_model_choice(event, origin, messages)

    def _has_text_content(self, event: AstrMessageEvent) -> bool:
        """检查消息是否包含有效文本内容"""
        msg = event.message_obj
        if not msg or not getattr(msg, "message", None):
            return False
        for comp in msg.message:
            if isinstance(comp, Comp.Plain):
                if (comp.text or "").strip():
                    return True
        return False

    async def _should_active_reply(self, event: AstrMessageEvent) -> bool:
        """判断当前消息是否应该触发主动回复"""
        if not self._allow_active_reply(event):
            return False
        if not self._has_text_content(event):
            return False

        ar = self.config.get("active_reply", {})
        if not isinstance(ar, dict):
            return False

        mode = str(ar.get("mode", "probability") or "probability").strip()
        if mode == "model_choice":
            return await self._need_active_reply_model_choice(event)

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

    @filter.command("烤箱状态")
    async def oven_status(self, event: AstrMessageEvent):
        bm = self.config.get("bracket_matching", {})
        rep = self.config.get("repetition", {})
        blank = self.config.get("remove_blank_lines", {})
        thinking = self.config.get("iam_thinking", {})
        style = self.config.get("style_learning", {})

        response = "🍳 插座烤箱状态\n\n"
        response += f"🔗 括号匹配: {'✅ 启用' if bm.get('enabled') else '❌ 禁用'}\n"
        response += f"🔄 消息复读: {'✅ 启用' if rep.get('enabled') else '❌ 禁用'}\n"
        if rep.get('enabled'):
            response += f"   └─ 打断施法概率: {(rep.get('break_spell_probability', 0.3) * 100):.0f}%\n"
            response += f"   └─ 打断文本: {rep.get('break_spell_text', '打断施法！')}\n"
        response += f"📝 移除空行: {'✅ 启用' if blank.get('enabled') else '❌ 禁用'}\n"
        if blank.get('enabled'):
            response += f"   └─ 最大连续换行: {blank.get('max_consecutive_newlines', 1)} 行\n"
        response += f"💭 思考表情: {'✅ 启用' if thinking.get('enabled') else '❌ 禁用'}\n"
        style_enabled = style.get('enabled', True)
        response += f"🎭 风格学习: {'✅ 启用' if style_enabled else '❌ 禁用'}\n"
        if self.style_injector and style_enabled:
            session_id = event.unified_msg_origin
            summary = self.style_injector.get_style_summary(session_id)
            if summary["has_styles"]:
                response += f"   └─ 通用: {summary['universal_count']} 条\n"

        ar = self.config.get("active_reply", {})
        ar_enabled = isinstance(ar, dict) and ar.get("enable", False)
        response += f"🎯 主动回复: {'✅ 启用' if ar_enabled else '❌ 禁用'}\n"
        if ar_enabled:
            response += f"   └─ 模式: {ar.get('mode', 'probability')}\n"
            if ar.get("mode") == "probability":
                response += f"   └─ 回复概率: {ar.get('possibility', 0.1):.0%}\n"

        yield event.plain_result(response)

    def _is_enabled(self, event):
        uid = event.message_obj.sender.user_id
        gid = event.message_obj.group_id
        return gid not in self.config.get("blacklist_groups", []) and uid not in self.config.get("blacklist_users", [])

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if not self._is_enabled(event):
            return
        content = event.message_obj.message_str

        bm = self.config.get("bracket_matching", {})
        if isinstance(bm, dict) and bm.get("enabled"):
            brackets = self.matcher.check(content)
            if brackets:
                asyncio.create_task(self._send_bracket_reply(event, brackets))

        rep = self.config.get("repetition", {})
        if isinstance(rep, dict) and rep.get("enabled"):
            result = self.repeater.check(event.unified_msg_origin, event.message_obj.message, rep)
            if result:
                asyncio.create_task(self._send_repeat_reply(event, result))

        # 主动回复
        if await self._should_active_reply(event):
            conv_id = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin,
                )
            )
            if not conv_id:
                conv_id = await self.context.conversation_manager.new_conversation(
                    event.unified_msg_origin,
                )
            conv = await self.context.conversation_manager.get_conversation(
                event.unified_msg_origin,
                conv_id,
            )
            if not conv:
                return
            yield event.request_llm(
                prompt=event.get_message_str() or "",
                session_id=event.session_id,
                conversation=conv,
            )

    # ==================== 移除空行 ====================

    @filter.on_decorating_result(priority=-100)
    async def remove_blank_lines(self, event: AstrMessageEvent):
        cfg = self.config.get("remove_blank_lines", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return
        result = event.get_result()
        if result and getattr(result, "chain", None):
            for comp in result.chain:
                if isinstance(comp, Comp.Plain):
                    comp.text = collapse_blank_lines(comp.text, cfg.get("max_consecutive_newlines", 1))

    @filter.on_waiting_llm_request()
    async def on_waiting(self, event: AstrMessageEvent):
        cfg = self.config.get("iam_thinking", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return
        if not self.thinking.is_aiocqhttp(event):
            return
        msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if msg_id:
            event.set_extra("thinking_active", True)
            event.set_extra("thinking_msg_id", msg_id)
            await self.thinking.emoji(event, msg_id, cfg.get("thinking_emoji_ids", []), True)

    @filter.after_message_sent()
    async def after_sent(self, event: AstrMessageEvent):
        cfg = self.config.get("iam_thinking", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return
        if not event.get_extra("thinking_active", False):
            return
        msg_id = event.get_extra("thinking_msg_id")
        if msg_id:
            if cfg.get("add_done_emoji"):
                await self.thinking.emoji(event, msg_id, cfg.get("done_emoji_ids", []), True)
            if cfg.get("remove_thinking_on_done"):
                await self.thinking.emoji(event, msg_id, cfg.get("thinking_emoji_ids", []), False)
            event.set_extra("thinking_done", True)

    # ==================== 风格学习 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        if not self._is_enabled(event):
            return
        if event.get_sender_id() == event.get_self_id():
            return
        if not self.style_data_manager:
            return
        cfg = self.config.get("style_learning", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            return

        session_id = event.unified_msg_origin
        message_content = event.get_message_str()
        if not message_content:
            return

        # 跳过指令消息，防止指令被当作群聊风格学习
        if message_content.startswith("/"):
            return

        message = {
            "sender": event.get_sender_name(),
            "content": message_content,
            "timestamp": asyncio.get_running_loop().time(),
        }
        await self.style_data_manager.add_message_to_history(session_id, message)

    @filter.on_llm_request(priority=17)
    async def on_llm_request_style(self, event: AstrMessageEvent, req):
        if not self.style_injector:
            return
        cfg = self.config.get("style_learning", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            return

        session_id = event.unified_msg_origin

        # ── 差分捕捉：快照其他插件的注入 ──
        _d = {}  # debug info
        try:
            _d["ctx_before"] = list(req.contexts) if isinstance(req.contexts, list) else []
        except Exception:
            _d["ctx_before"] = []
        try:
            _d["prompt_before"] = req.prompt or ""
        except Exception:
            _d["prompt_before"] = ""
        try:
            _d["extra_count_before"] = (
                len(req.extra_user_content_parts)
                if hasattr(req, "extra_user_content_parts")
                and isinstance(req.extra_user_content_parts, list)
                else 0
            )
        except Exception:
            _d["extra_count_before"] = 0

        # ── 前置清理：剥离平台 LTM 并去重 ──
        from .learning_style.system_prompt_rewriter import SystemPromptRewriter

        original_prompt = req.system_prompt or ""
        rewrite_result = SystemPromptRewriter.rewrite(original_prompt, "")
        if rewrite_result.ltm_detected or rewrite_result.duplicate_suspected:
            logger.info(
                f"[烤箱-风格学习] system_prompt 清理: "
                f"ltm={rewrite_result.ltm_detected}, "
                f"dup={rewrite_result.duplicate_suspected}, "
                f"chars={len(original_prompt)}→{len(rewrite_result.merged_system_prompt)}"
            )
        req.system_prompt = rewrite_result.merged_system_prompt

        # ── 风格注入 → extra_user_content_parts ──
        style_text = self.style_injector.build_injection_text(session_id)
        if style_text:
            _d["style_len"] = len(style_text)
            from astrbot.core.agent.message import TextPart

            part = TextPart(text=style_text)
            if hasattr(part, "mark_as_temp"):
                part.mark_as_temp()
                _d["mark_as_temp"] = True
            else:
                _d["mark_as_temp"] = False
            req.extra_user_content_parts.append(part)

        # ── 差分日志 ──
        try:
            if hasattr(req, "extra_user_content_parts") and isinstance(
                req.extra_user_content_parts, list
            ):
                _d["extra_count_after"] = len(req.extra_user_content_parts)
                other_count = (
                    _d["extra_count_after"] - _d["extra_count_before"] - (1 if style_text else 0)
                )
                if other_count > 0:
                    logger.info(
                        f"[烤箱-风格学习] 检测到其他插件注入: "
                        f"{other_count} 个 extra_user_content_parts"
                    )
        except Exception:
            pass

        _d["injected"] = bool(style_text)
        logger.debug(f"[烤箱-风格学习] on_llm_request 摘要: {_d}")

    @filter.command("风格状态")
    async def style_status(self, event: AstrMessageEvent):
        if not self.style_injector:
            yield event.plain_result("风格学习功能未初始化。")
            return
        session_id = event.unified_msg_origin
        summary = self.style_injector.get_style_summary(session_id)

        if not summary["has_styles"]:
            yield event.plain_result("当前会话还没有学习到任何风格特点。")
            return

        response = "当前会话风格状态：\n"
        response += f"通用风格：{summary['universal_count']} 条\n"

        if summary["universal_preview"]:
            response += f"Top-{min(3, summary['universal_count'])}：{', '.join(summary['universal_preview'])}"

        yield event.plain_result(response.strip())

    @filter.command("清空风格")
    async def clear_styles(self, event: AstrMessageEvent):
        if not self.style_data_manager:
            yield event.plain_result("风格学习功能未初始化。")
            return
        session_id = event.unified_msg_origin

        if session_id in self.style_data_manager.universal:
            self.style_data_manager.universal[session_id] = []
            self.style_data_manager._dirty_universal = True

        asyncio.create_task(self.style_data_manager._schedule_save())
        yield event.plain_result("已清空当前会话的所有学习风格。")

    @filter.command("学习总结")
    async def learn_now(self, event: AstrMessageEvent):
        if not self.style_learning_manager or not self.style_injector:
            yield event.plain_result("风格学习功能未初始化。")
            return
        session_id = event.unified_msg_origin

        chat_history = self.style_data_manager.get_chat_history(session_id, limit=100)
        min_history = self.config.get("min_history_for_analysis", 10)
        if len(chat_history) < min_history:
            yield event.plain_result(
                f"当前会话聊天记录不足 {min_history} 条，无法进行分析。"
            )
            return

        yield event.plain_result("正在分析聊天记录并学习风格特征，请稍候...")

        try:
            style_cfg = self.config.get("style_learning", {})
            style_provider = (style_cfg.get("style_provider_id", "") or "").strip()
            await self.style_learning_manager.analyze_and_learn(session_id, provider_id=style_provider)

            summary = self.style_injector.get_style_summary(session_id)
            response = "学习分析完成！\n"
            response += f"通用风格：{summary['universal_count']} 条"

            if summary["universal_preview"]:
                response += f"\nTop-{min(3, summary['universal_count'])}：{', '.join(summary['universal_preview'])}"

            yield event.plain_result(response)

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 手动触发学习分析失败: {e}")
            yield event.plain_result(f"学习分析失败：{e}")



