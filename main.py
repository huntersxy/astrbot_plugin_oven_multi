import re
from collections import defaultdict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

PLUGIN_NAME = "astrbot_plugin_oven_multi"


def collapse_blank_lines(text, max_newlines=1):
    limit = max(int(max_newlines), 0)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{" + str(limit + 1) + r",}", "\n" * limit, normalized)


PAIR_LIST = {
    "(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
    "<": ">", ">": "<", "「": "」", "」": "「", "（": "）", "）": "（",
    "【": "】", "】": "【", "《": "》", "》": "《", "『": "』", "』": "『",
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
        msg_id = str([str(m) for m in message])
        if msg_id == self.last[session_id]:
            self.count[session_id] += 1
            if self.count[session_id] == 1:
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


@register(PLUGIN_NAME, "汐兮雨", "插座的多功能烤箱", "1.4.1")
class OvenMultiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)

        if config is None:
            config = AstrBotConfig({})
        self.config = config

        self.matcher = BracketMatcher()
        self.repeater = Repeater()
        self.thinking = ThinkingManager()

    async def initialize(self):
        logger.info(f"[插座烤箱] 启动")

    async def terminate(self):
        pass

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
                yield event.plain_result(brackets)

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