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

# 合并转发消息缓存：unified_msg_origin -> 解析后的文本内容
_forward_cache: dict[str, str] = {}

# LLM Tool 相关导入（运行时可用）
try:
    from pydantic import Field
    from pydantic.dataclasses import dataclass as pydantic_dataclass
    from astrbot.core.agent.tool import FunctionTool, ToolExecResult
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext

    _TOOL_IMPORTS_OK = True
except Exception:
    _TOOL_IMPORTS_OK = False


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


async def _extract_forward_content(event: AstrMessageEvent) -> str | None:
    """检测并提取合并转发消息内容

    遍历消息组件检测 Forward/Reply 组件，
    调用 OneBot11 get_forward_msg API 提取文本内容。

    Returns:
        格式化后的合并转发文本，未检测到则返回 None
    """
    message_segments = getattr(event.message_obj, "message", None)
    if not message_segments or not isinstance(message_segments, list):
        logger.debug(f"[烤箱-合并转发] 消息段为空或非列表: {type(message_segments)}")
        return None

    # 打印消息段类型用于诊断
    seg_types = []
    for seg in message_segments:
        if isinstance(seg, dict):
            seg_types.append(f"dict:{seg.get('type', 'unknown')}")
        else:
            seg_types.append(type(seg).__name__)
    logger.info(f"[烤箱-合并转发] 消息段类型: {seg_types}")

    forward_id = ""

    # 场景1: 直接发送的合并转发
    try:
        from astrbot.api.message_components import Forward as ForwardComp

        for seg in message_segments:
            if isinstance(seg, ForwardComp):
                forward_id = str(getattr(seg, "id", ""))
                logger.info(f"[烤箱-合并转发] 检测到 Forward 组件: id={forward_id}")
                break
            if isinstance(seg, dict) and seg.get("type") == "forward":
                forward_id = str(seg.get("data", {}).get("id", ""))
                logger.info(f"[烤箱-合并转发] 检测到 dict forward: id={forward_id}")
                break
    except ImportError as e:
        logger.debug(f"[烤箱-合并转发] Forward 组件导入失败: {e}")
    except Exception as e:
        logger.debug(f"[烤箱-合并转发] Forward 检测异常: {e}")

    # 场景2: 回复中引用的合并转发
    if not forward_id:
        try:
            from astrbot.api.message_components import Reply as ReplyComp

            for seg in message_segments:
                if isinstance(seg, ReplyComp):
                    reply_id = getattr(seg, "id", "")
                    logger.debug(f"[烤箱-合并转发] 检测到 Reply 组件: id={reply_id}")
                    if not reply_id:
                        break
                    bot = getattr(event, "bot", None)
                    if bot is None:
                        logger.debug("[烤箱-合并转发] event.bot 不存在")
                        break
                    try:
                        result = await asyncio.wait_for(
                            bot.call_action("get_msg", message_id=int(str(reply_id))),
                            timeout=5.0,
                        )
                        logger.debug(f"[烤箱-合并转发] get_msg 结果: {type(result)}")
                        if result and isinstance(result, dict):
                            original_segments = result.get("message", [])
                            logger.debug(
                                f"[烤箱-合并转发] 被回复消息段: {original_segments}"
                            )
                            if isinstance(original_segments, list):
                                for segment in original_segments:
                                    if (
                                        isinstance(segment, dict)
                                        and segment.get("type") == "forward"
                                    ):
                                        forward_id = str(
                                            segment.get("data", {}).get("id", "")
                                        )
                                        logger.debug(
                                            f"[烤箱-合并转发] 从 Reply 中提取 forward_id: {forward_id}"
                                        )
                                        break
                    except asyncio.TimeoutError:
                        logger.debug(f"[烤箱-合并转发] get_msg 超时: reply_id={reply_id}")
                    except Exception as e:
                        logger.debug(f"[烤箱-合并转发] get_msg 失败: {e}")
                    break
        except ImportError as e:
            logger.debug(f"[烤箱-合并转发] Reply 组件导入失败: {e}")
        except Exception as e:
            logger.debug(f"[烤箱-合并转发] Reply 检测异常: {e}")

    if not forward_id:
        logger.info("[烤箱-合并转发] 未检测到 forward_id")
        return None

    # 调用 get_forward_msg API
    bot = getattr(event, "bot", None)
    if bot is None:
        logger.info("[烤箱-合并转发] event.bot 不存在，无法调用 API")
        return None

    try:
        forward_data = await asyncio.wait_for(
            bot.call_action("get_forward_msg", id=forward_id),
            timeout=10.0,
        )
        logger.info(f"[烤箱-合并转发] get_forward_msg API 返回: {type(forward_data)}, keys={list(forward_data.keys()) if isinstance(forward_data, dict) else 'N/A'}")
    except asyncio.TimeoutError:
        logger.info(f"[烤箱-合并转发] get_forward_msg API 超时: forward_id={forward_id}")
        return None
    except Exception as e:
        logger.info(f"[烤箱-合并转发] get_forward_msg API 失败: {e}")
        return None

    if not forward_data or not isinstance(forward_data, dict):
        logger.info(f"[烤箱-合并转发] API 返回无效: forward_data={forward_data}")
        return None

    messages = forward_data.get("messages", [])
    logger.info(f"[烤箱-合并转发] messages 数量: {len(messages)}")
    if not messages:
        logger.info("[烤箱-合并转发] messages 为空数组，可能是 NapCat API 限制")
        return None

    lines: list[str] = ["【合并转发内容】"]
    for node in messages:
        sender_name = node.get("sender", {}).get("nickname", "未知用户") or "未知用户"
        raw_content = node.get("message") or node.get("content", [])

        text_parts: list[str] = []
        if isinstance(raw_content, list):
            for seg in raw_content:
                if isinstance(seg, dict):
                    seg_type = seg.get("type", "")
                    seg_data = seg.get("data", {})
                    if seg_type == "text":
                        text_parts.append(seg_data.get("text", ""))
                    elif seg_type == "at":
                        text_parts.append(f"[At: {seg_data.get('qq', '')}]")
                    elif seg_type == "image":
                        text_parts.append("[图片]")
        elif isinstance(raw_content, str):
            text_parts.append(raw_content)

        node_text = f"{sender_name}: {''.join(text_parts)}".strip()
        if node_text and node_text != f"{sender_name}:":
            lines.append(node_text)

    if len(lines) == 1:
        return None

    return "\n".join(lines)


if _TOOL_IMPORTS_OK:

    @pydantic_dataclass
    class ParseForwardTool(FunctionTool[AstrAgentContext]):
        """解析合并转发消息的 Tool

        当用户发送合并转发消息时，LLM 可调用此工具获取转发的文本内容。
        """

        name: str = "parse_forward_message"
        description: str = (
            "解析用户消息中的合并转发（Forward）内容。"
            "当用户发送或引用合并转发消息时，调用此工具获取其中的文本对话内容。"
            "返回格式为每条消息一行：发送者: 内容"
        )
        parameters: dict = Field(
            default_factory=lambda: {
                "type": "object",
                "properties": {},
                "required": [],
            }
        )

        async def call(
            self, context: ContextWrapper[AstrAgentContext], **kwargs
        ) -> ToolExecResult:
            """执行合并转发解析

            Returns:
                合并转发文本内容，或提示无转发内容
            """
            try:
                event = context.context.event
                # 优先从缓存获取（预检测的结果）
                key = event.unified_msg_origin
                content = _forward_cache.pop(key, None)
                if content:
                    return f"【合并转发解析结果】\n{content}"

                # 缓存中没有则实时检测当前消息
                content = await _extract_forward_content(event)
                if content:
                    return f"【合并转发解析结果】\n{content}"

                return "当前消息中没有检测到合并转发内容。"
            except Exception as e:
                return f"解析合并转发失败: {e}"


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


@register(PLUGIN_NAME, "汐兮雨", "插座的多功能烤箱", "1.22.1")
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

        # 好感度系统
        self.favor_manager = None
        self._init_favor_system()

        # 主动回复
        self.active_reply_stacks: dict[str, list[str]] = defaultdict(list)
        self.model_choice_histories: dict[str, list[str]] = defaultdict(list)

        # 余额查询
        self.balance_checker = BalanceChecker(self.config)

        # 注册合并转发解析 Tool
        self._register_forward_tool()

        # 注册 Web API
        self._register_web_api()

    def _register_forward_tool(self):
        """注册 parse_forward_message LLM Tool"""
        if not _TOOL_IMPORTS_OK:
            logger.debug("[烤箱-合并转发] LLM Tool 导入不可用，跳过注册")
            return
        try:
            self.context.add_llm_tools(ParseForwardTool())
            logger.info("[烤箱-合并转发] 已注册 parse_forward_message Tool")
        except Exception as e:
            logger.warning(f"[烤箱-合并转发] 注册 Tool 失败: {e}")

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

    def _init_favor_system(self):
        fcfg = self.config.get("favor_system", {})
        if isinstance(fcfg, dict) and fcfg.get("enabled", True):
            try:
                from .favor_manager import FavorManager
                self.favor_manager = FavorManager(self.config)
            except Exception as e:
                logger.error(f"[烤箱-好感度] 初始化失败: {e}")

    async def initialize(self):
        if self.style_scheduler:
            self.style_scheduler.start()
        logger.info(f"[插座烤箱] 启动")

    async def terminate(self):
        if self.style_scheduler:
            await self.style_scheduler.stop()
        if self.style_data_manager:
            await self.style_data_manager.force_save()
        if self.favor_manager:
            self.favor_manager.force_save()

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

        fcfg = self.config.get("favor_system", {})
        favor_enabled = isinstance(fcfg, dict) and fcfg.get("enabled", True)
        response += f"💖 好感度: {'✅ 启用' if favor_enabled else '❌ 禁用'}\n"

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

        # 检测合并转发消息并缓存内容
        cfg = self.config.get("forward_message", {})
        if isinstance(cfg, dict) and cfg.get("enabled", True):
            forward_text = await _extract_forward_content(event)
            if forward_text:
                _forward_cache[event.unified_msg_origin] = forward_text
                logger.info(
                    f"[烤箱-合并转发] 已缓存转发内容 "
                    f"({len(forward_text)} 字符) | origin={event.unified_msg_origin}"
                )

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
        session_id = event.unified_msg_origin

        # ── 前置清理：剥离平台 LTM 并去重（无论风格/好感度都执行） ──
        from .learning_style.system_prompt_rewriter import SystemPromptRewriter

        original_prompt = req.system_prompt or ""
        rewrite_result = SystemPromptRewriter.rewrite(original_prompt, "")
        if rewrite_result.ltm_detected or rewrite_result.duplicate_suspected:
            logger.info(
                f"[烤箱] system_prompt 清理: "
                f"ltm={rewrite_result.ltm_detected}, "
                f"dup={rewrite_result.duplicate_suspected}, "
                f"chars={len(original_prompt)}→{len(rewrite_result.merged_system_prompt)}"
            )
        req.system_prompt = rewrite_result.merged_system_prompt

        # ── 静态好感度指令 → system_prompt（不随用户变化，不影响缓存） ──
        if self.favor_manager:
            from .favor_manager import FavorManager

            req.system_prompt += "\n\n" + FavorManager.INSTRUCTION_TEXT

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

        # ── 判断黑名单（好感度系统） ──
        user_id = str(event.get_sender_id())
        _is_blacklisted = False
        if self.favor_manager:
            _is_blacklisted = self.favor_manager.is_blacklisted(
                user_id, session_id if self.favor_manager.session_based_blacklist else None
            )
        if _is_blacklisted:
            logger.debug(f"[烤箱-好感度] 用户 {user_id} 在黑名单中，拦截 LLM 请求")
            event.stop_event()
            return

        # ── 风格注入 → extra_user_content_parts ──
        style_cfg = self.config.get("style_learning", {})
        if (
            self.style_injector
            and isinstance(style_cfg, dict)
            and style_cfg.get("enabled", True)
        ):
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

        # ── 好感度关系描述 → extra_user_content_parts（动态，随好感度变化） ──
        favor_text = None
        if self.favor_manager:
            favor_text = self.favor_manager.build_relationship_text(
                user_id, session_id if self.favor_manager.session_based_favor else None
            )
            if favor_text:
                _d["favor_len"] = len(favor_text)
                from astrbot.core.agent.message import TextPart

                part = TextPart(text=favor_text)
                if hasattr(part, "mark_as_temp"):
                    part.mark_as_temp()
                req.extra_user_content_parts.append(part)

        # ── 差分日志 ──
        try:
            if hasattr(req, "extra_user_content_parts") and isinstance(
                req.extra_user_content_parts, list
            ):
                _d["extra_count_after"] = len(req.extra_user_content_parts)
                injected = bool(style_text) + bool(favor_text)
                other_count = _d["extra_count_after"] - _d["extra_count_before"] - injected
                if other_count > 0:
                    logger.info(
                        f"[烤箱] 检测到其他插件 {other_count} 个 extra_user_content_parts"
                    )
        except Exception:
            pass

        _d["injected"] = bool(style_text) or bool(favor_text)
        logger.debug(f"[烤箱] on_llm_request 摘要: {_d}")

    @filter.on_llm_response()
    async def on_llm_resp_favor(self, event: AstrMessageEvent, resp):
        """好感度：从 LLM 回复中解析标记，更新好感度并清理。"""
        if not self.favor_manager:
            return
        fcfg = self.config.get("favor_system", {})
        if not isinstance(fcfg, dict) or not fcfg.get("enabled", True):
            return

        user_id = str(event.get_sender_id())
        session_id = event.unified_msg_origin
        text = resp.completion_text or ""

        # 更新好感度
        before = self.favor_manager.get_favor(
            user_id, session_id if self.favor_manager.session_based_favor else None
        )
        self.favor_manager.update_favor(
            user_id, text, session_id if self.favor_manager.session_based_favor else None
        )
        after = self.favor_manager.get_favor(
            user_id, session_id if self.favor_manager.session_based_favor else None
        )
        if before != after:
            logger.debug(
                f"[烤箱-好感度] 用户 {user_id} "
                f"{before} → {after} "
                f"({self.favor_manager.get_favor_level_short(after)})"
            )

        # 清理回复文本
        if fcfg.get("clean_response", True):
            cleaned = self.favor_manager.clean_response_text(text)
            if cleaned != text:
                resp.completion_text = cleaned

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

    # ── 好感度命令 ──

    @filter.command("好感度")
    async def query_favor(self, event: AstrMessageEvent):
        if not self.favor_manager:
            yield event.plain_result("好感度系统未初始化。")
            return
        user_id = str(event.get_sender_id())
        session_id = event.unified_msg_origin
        f_session = session_id if self.favor_manager.session_based_favor else None

        if self.favor_manager.is_blacklisted(
            user_id, session_id if self.favor_manager.session_based_blacklist else None
        ):
            yield event.plain_result("你已被列入黑名单")
            return

        favor = self.favor_manager.get_favor(user_id, f_session)
        level = self.favor_manager.get_favor_level_short(favor)
        counter = self.favor_manager.get_low_counter(
            user_id, session_id if self.favor_manager.session_based_counter else None
        )
        yield event.plain_result(f"当前好感度：{favor} ({level})\n低好感度计数：{counter}")

    @filter.command("管理")
    async def admin_control(self, event: AstrMessageEvent, cmd: str, target: str = None, value: int = None):
        if not self.favor_manager:
            yield event.plain_result("好感度系统未初始化。")
            return
        admins = self._parse_favor_admins()
        if str(event.get_sender_id()) not in admins:
            yield event.plain_result("⚠️ 你没有权限执行此操作")
            return

        fcfg = self.config.get("favor_system", {})
        fm = self.favor_manager
        session_id = event.unified_msg_origin
        f_session = session_id if fm.session_based_favor else None
        bl_session = session_id if fm.session_based_blacklist else None

        target = str(target).strip() if target else None

        try:
            if cmd == "好感度":
                if target and value is not None:
                    fm.set_favor(target, value, f_session)
                    yield event.plain_result(f"✅ 用户 {target} 好感度已设为 {value}")
                else:
                    yield event.plain_result(
                        json.dumps(
                            fm.session_favor_data.get(session_id, {})
                            if fm.session_based_favor
                            else fm.favor_data,
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
            elif cmd == "黑名单":
                if not target:
                    yield event.plain_result(
                        json.dumps(
                            fm.session_blacklist.get(session_id, {})
                            if fm.session_based_blacklist
                            else fm.blacklist,
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                else:
                    if fm.is_blacklisted(target, bl_session):
                        yield event.plain_result("⚠️ 该用户已在黑名单中")
                    else:
                        fm.add_to_blacklist(target, bl_session)
                        yield event.plain_result(f"⛔ 用户 {target} 已加入黑名单")
            elif cmd == "移出黑名单":
                if not target:
                    yield event.plain_result("⚠️ 请指定要移出黑名单的用户")
                elif not fm.is_blacklisted(target, bl_session):
                    yield event.plain_result("⚠️ 该用户不在黑名单中")
                else:
                    fm.remove_from_blacklist(target, bl_session)
                    fm.reset_low_counter(target, session_id if fm.session_based_counter else None)
                    fm.set_favor(target, 0, f_session)
                    yield event.plain_result(f"✅ 用户 {target} 已移出黑名单，并重置好感度和计数器")
            elif cmd == "白名单":
                if not target:
                    yield event.plain_result(
                        json.dumps(fm.whitelist, indent=2, ensure_ascii=False)
                    )
                else:
                    if target in fm.whitelist:
                        yield event.plain_result("⚠️ 该用户已在白名单中")
                    else:
                        fm.whitelist[target] = True
                        fm._save_data(fm.whitelist, "whitelist.json")
                        yield event.plain_result(f"✅ 用户 {target} 已加入白名单")
            elif cmd == "移出白名单":
                if not target:
                    yield event.plain_result("⚠️ 请指定要移出白名单的用户")
                elif target not in fm.whitelist:
                    yield event.plain_result("⚠️ 该用户不在白名单中")
                else:
                    del fm.whitelist[target]
                    fm._save_data(fm.whitelist, "whitelist.json")
                    yield event.plain_result(f"✅ 用户 {target} 已移出白名单")
            elif cmd == "计数器":
                if not target:
                    yield event.plain_result(
                        f"当前计数器设置：\n"
                        f"自动减少：{'开启' if fm.auto_decrease_enabled else '关闭'}\n"
                        f"减少间隔：{fm.auto_decrease_hours}小时\n"
                        f"减少数量：{fm.auto_decrease_amount}"
                    )
                else:
                    if target == "开启":
                        fm.auto_decrease_enabled = True
                        yield event.plain_result("✅ 已开启计数器自动减少功能")
                    elif target == "关闭":
                        fm.auto_decrease_enabled = False
                        yield event.plain_result("✅ 已关闭计数器自动减少功能")
                    elif target == "间隔" and value is not None:
                        if value <= 0:
                            yield event.plain_result("⚠️ 间隔时间必须大于0")
                        else:
                            fm.auto_decrease_hours = value
                            yield event.plain_result(f"✅ 已设置计数器减少间隔为 {value} 小时")
                    elif target == "数量" and value is not None:
                        if value <= 0:
                            yield event.plain_result("⚠️ 减少数量必须大于0")
                        else:
                            fm.auto_decrease_amount = value
                            yield event.plain_result(f"✅ 已设置计数器每次减少数量为 {value}")
                    else:
                        yield event.plain_result("❌ 无效的参数，可用参数：开启/关闭/间隔/数量")
            else:
                yield event.plain_result("❌ 无效指令，可用命令：好感度/黑名单/移出黑名单/白名单/移出白名单/计数器")
        except ValueError:
            yield event.plain_result("❌ 数值参数必须为整数")
        except Exception as e:
            yield event.plain_result(f"⚠️ 操作失败：{str(e)}")

    def _parse_favor_admins(self) -> list[str]:
        fcfg = self.config.get("favor_system", {})
        admins = fcfg.get("admins_id", [])
        if isinstance(admins, str):
            return [x.strip() for x in admins.split(",")]
        return [str(x) for x in admins]



