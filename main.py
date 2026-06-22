import asyncio
import re
from collections import defaultdict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

from .learning_style.data_manager import DataManager as StyleDataManager
from .learning_style.learning_manager import LearningManager
from .learning_style.scheduler import Scheduler as StyleScheduler
from .learning_style.style_injector import StyleInjector

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


@register(PLUGIN_NAME, "汐兮雨", "插座的多功能烤箱", "1.5.0")
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
                response += f"   └─ 情境: {summary['contextual_count']} 条\n"
                response += f"   └─ 特定: {summary['specific_count']} 条\n"

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
                # 异步发送补全的括号，不 yield 阻断事件流
                # 机器人会回复 "）"，同时事件继续传递给 LLM
                asyncio.create_task(self._send_bracket_reply(event, brackets))

        rep = self.config.get("repetition", {})
        if isinstance(rep, dict) and rep.get("enabled"):
            result = self.repeater.check(event.unified_msg_origin, event.message_obj.message, rep)
            if result:
                if result[0] == "break":
                    yield event.plain_result(result[1])
                elif result[0] == "repeat":
                    yield event.chain_result(result[1])

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

        message = {
            "sender": event.get_sender_name(),
            "content": message_content,
            "timestamp": asyncio.get_running_loop().time(),
        }
        await self.style_data_manager.add_message_to_history(session_id, message)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        if not self.style_injector:
            return
        cfg = self.config.get("style_learning", {})
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            return
        session_id = event.unified_msg_origin
        user_message = event.get_message_str() or ""

        if cfg.get("inject_as_system_prompt", True):
            original_prompt = req.system_prompt or ""
            new_prompt = self.style_injector.inject_style_to_prompt(
                session_id, original_prompt, user_message=user_message
            )
            req.system_prompt = new_prompt
        else:
            style_text = self.style_injector.build_raw_style_text(session_id, user_message=user_message)
            if style_text:
                req.prompt = f"[风格参考：{style_text}]\n{req.prompt or user_message}"

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
        response += f"通用表征：{summary['universal_count']} 条\n"
        response += f"情境表征：{summary['contextual_count']} 条\n"
        response += f"特定表征：{summary['specific_count']} 条\n"

        if summary["universal_preview"]:
            response += f"通用 Top-3：{', '.join(summary['universal_preview'])}\n"
        if summary["contextual_preview"]:
            response += f"情境 Top-3：{', '.join(summary['contextual_preview'])}\n"
        if summary["specific_preview"]:
            response += f"特定 Top-3：{', '.join(summary['specific_preview'])}"

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
        if session_id in self.style_data_manager.contextual:
            self.style_data_manager.contextual[session_id] = []
            self.style_data_manager._dirty_contextual = True
        if session_id in self.style_data_manager.specific:
            self.style_data_manager.specific[session_id] = []
            self.style_data_manager._dirty_specific = True

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
            await self.style_learning_manager.analyze_and_learn(session_id)

            summary = self.style_injector.get_style_summary(session_id)
            response = "学习分析完成！\n"
            response += f"通用表征：{summary['universal_count']} 条\n"
            response += f"情境表征：{summary['contextual_count']} 条\n"
            response += f"特定表征：{summary['specific_count']} 条"

            if summary["universal_preview"]:
                response += f"\n通用 Top-3：{', '.join(summary['universal_preview'])}"
            if summary["contextual_preview"]:
                response += f"\n情境 Top-3：{', '.join(summary['contextual_preview'])}"
            if summary["specific_preview"]:
                response += f"\n特定 Top-3：{', '.join(summary['specific_preview'])}"

            yield event.plain_result(response)

        except Exception as e:
            logger.error(f"[烤箱-风格学习] 手动触发学习分析失败: {e}")
            yield event.plain_result(f"学习分析失败：{e}")