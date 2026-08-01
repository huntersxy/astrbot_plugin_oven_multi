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
import re

from quart import jsonify

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register

from .constants import (
    FEATURE_ACTIVE_REPLY,
    FEATURE_BRACKET,
    FEATURE_MENTION_PARSER,
    FEATURE_REMOVE_BLANK,
    FEATURE_REPETITION,
    FEATURE_STYLE,
    FEATURE_THINKING,
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)
from .features.active_reply import ActiveReply
from .features.balance_checker import BalanceChecker
from .features.bracket_matcher import BracketMatcher
from .features.mention_parser import ActiveSpeakersTracker, transform_mention_in_chain
from .features.repeater import Repeater
from .features.learning_style import (
    CATEGORY_SITUATIONAL,
    CATEGORY_STABLE,
    StyleManager,
    clean_system_prompt,
)
from .features.thinking_manager import ThinkingManager


# ── 配置访问辅助 ─────────────────────────────────────────────────────────

def feature_cfg(config, name: str) -> dict:
    """读取功能配置节（容错非 dict 值）。"""
    cfg = config.get(name, {})
    return cfg if isinstance(cfg, dict) else {}


def feature_enabled(config, name: str, default: bool = True) -> bool:
    return bool(feature_cfg(config, name).get("enabled", default))


def cfg_value(config, name: str, key: str, default=None):
    return feature_cfg(config, name).get(key, default)


def blocked_by_blacklist(config, group_id=None, user_id=None) -> bool:
    """群/用户是否命中黑名单。"""
    return (
        bool(group_id) and str(group_id) in config.get("blacklist_groups", [])
    ) or (
        bool(user_id) and str(user_id) in config.get("blacklist_users", [])
    )


def collapse_blank_lines(text: str, max_newlines: int = 1) -> str:
    """将连续换行压缩为至多 max_newlines 个。"""
    limit = max(int(max_newlines), 0)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(rf"\n{{{limit + 1},}}", "\n" * limit, normalized)


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class OvenMultiPlugin(Star):
    """插座的多功能烤箱 - 主插件类"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config is not None else AstrBotConfig({})
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)

        # 轻量功能
        self.matcher = BracketMatcher()
        self.repeater = Repeater()
        self.thinking = ThinkingManager()
        self.active_reply = ActiveReply()

        # 余额查询
        self.balance_checker = BalanceChecker(self.config)

        # @功能 - 活跃发言人追踪
        self.speakers = ActiveSpeakersTracker(
            max_speakers=int(
                cfg_value(self.config, FEATURE_MENTION_PARSER, "max_speakers", 50) or 50
            ),
            data_dir=self.data_dir,
        )

        # 风格学习（聊天记录收集 + 分析 + 注入）
        self.style: StyleManager | None = None
        if feature_enabled(self.config, FEATURE_STYLE):
            try:
                self.style = StyleManager(self, self.data_dir)
                logger.info("[烤箱-风格学习] 初始化完成")
            except Exception as e:
                logger.error(f"[烤箱-风格学习] 初始化失败: {e}")

        # Web API
        for route, handler, desc in (
            (f"/{PLUGIN_NAME}/status", self._api_status, "烤箱状态总览"),
            (f"/{PLUGIN_NAME}/balance", self._api_balance, "余额信息"),
            (f"/{PLUGIN_NAME}/style_status", self._api_style_status, "风格学习状态"),
        ):
            self.context.register_web_api(route, handler, ["GET"], desc)

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def initialize(self):
        if self.style:
            self.style.start()
        logger.info("[插座烤箱] 启动")

    async def terminate(self):
        if self.style:
            await self.style.stop()
            await self.style.data.force_save()
        await self.balance_checker.terminate()

    # ── Web API ──────────────────────────────────────────────────────────

    def _feature_status(self) -> list[dict]:
        """汇总各功能启用状态（供命令与页面共用）。"""
        items = []

        def add(name, enabled, detail=""):
            items.append({"name": name, "enabled": bool(enabled), "detail": detail})

        rep_cfg = feature_cfg(self.config, FEATURE_REPETITION)
        add("括号匹配", feature_enabled(self.config, FEATURE_BRACKET))
        add(
            "消息复读",
            feature_enabled(self.config, FEATURE_REPETITION),
            (
                f"打断概率 {float(rep_cfg.get('break_spell_probability', 0.3)):.0%}"
                if rep_cfg.get("enabled")
                else ""
            ),
        )
        blank_cfg = feature_cfg(self.config, FEATURE_REMOVE_BLANK)
        add(
            "移除空行",
            feature_enabled(self.config, FEATURE_REMOVE_BLANK),
            (
                f"最大连续换行 {blank_cfg.get('max_consecutive_newlines', 1)} 行"
                if blank_cfg.get("enabled")
                else ""
            ),
        )
        add("思考表情", feature_enabled(self.config, FEATURE_THINKING))
        add("风格学习", feature_enabled(self.config, FEATURE_STYLE, True))

        ar_cfg = feature_cfg(self.config, FEATURE_ACTIVE_REPLY)
        ar_enabled = bool(ar_cfg.get("enable", False))
        add(
            "主动回复",
            ar_enabled,
            f"模式 {ar_cfg.get('mode', 'probability')}" if ar_enabled else "",
        )

        add("@功能", feature_enabled(self.config, FEATURE_MENTION_PARSER))
        return items

    def _style_status_data(self) -> dict:
        if not self.style:
            return {}
        result = {}
        for session_id in self.style.data.universal:
            traits = self.style.data.get_universal_for_session(session_id)
            result[session_id] = {
                "session_id": session_id,
                "display_name": session_id.split("_")[-1] if "_" in session_id else session_id,
                "universal": [
                    t for t in traits if t.get("category", CATEGORY_STABLE) == CATEGORY_STABLE
                ],
                "situational": [t for t in traits if t.get("category") == CATEGORY_SITUATIONAL],
                "history": self.style.data.get_chat_history(session_id, limit=50),
            }
        return result

    async def _api_status(self):
        return jsonify(
            {
                "success": True,
                "data": {
                    "features": self._feature_status(),
                    "balance": await self.balance_checker.query_all(),
                    "style": self._style_status_data(),
                },
            }
        )

    async def _api_balance(self):
        return jsonify({"success": True, "data": await self.balance_checker.query_all()})

    async def _api_style_status(self):
        return jsonify({"success": True, "data": self._style_status_data()})

    # ── 工具方法 ─────────────────────────────────────────────────────────

    def _blocked(self, event: AstrMessageEvent) -> bool:
        msg = event.message_obj
        return blocked_by_blacklist(self.config, msg.group_id, msg.sender.user_id)

    # ── Handler：烤箱状态 ────────────────────────────────────────────────

    @filter.command("烤箱状态")
    async def oven_status(self, event: AstrMessageEvent):
        lines = ["🍳 插座烤箱状态", ""]
        for item in self._feature_status():
            mark = "✅ 启用" if item["enabled"] else "❌ 禁用"
            line = f"  {item['name']}: {mark}"
            if item["detail"]:
                line += f"（{item['detail']}）"
            lines.append(line)

        if self.style and feature_enabled(self.config, FEATURE_STYLE, True):
            summary = self.style.injector.get_style_summary(event.unified_msg_origin)
            if summary["has_styles"]:
                line = f"  └─ 本群已学习 {summary['universal_count']} 条稳定风格"
                if summary.get("situational_count"):
                    line += f"，{summary['situational_count']} 条场景梗"
                lines.append(line)

        yield event.plain_result("\n".join(lines))

    # ── Handler：群消息处理 ──────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if self._blocked(event):
            return

        content = event.message_obj.message_str

        # 追踪活跃发言人（用于 @ 功能）
        if feature_enabled(self.config, FEATURE_MENTION_PARSER):
            user_id = event.get_sender_id()
            nickname = event.message_obj.sender.nickname
            if user_id and nickname:
                self.speakers.record(event.unified_msg_origin, str(user_id), nickname)

        # 括号匹配
        if feature_enabled(self.config, FEATURE_BRACKET):
            brackets = self.matcher.check(content)
            if brackets:
                await event.send(event.plain_result(brackets))

        # 消息复读
        if feature_enabled(self.config, FEATURE_REPETITION):
            result = self.repeater.check(
                event.unified_msg_origin,
                event.message_obj.message,
                feature_cfg(self.config, FEATURE_REPETITION),
            )
            if result:
                kind, payload = result
                await event.send(
                    event.plain_result(payload)
                    if kind == "break"
                    else event.chain_result(payload)
                )

        # 主动回复
        if await self.active_reply.should_active_reply(event, self.config, self.context):
            cm = self.context.conversation_manager
            conv_id = await cm.get_curr_conversation_id(event.unified_msg_origin)
            if not conv_id:
                conv_id = await cm.new_conversation(event.unified_msg_origin)
            conv = await cm.get_conversation(event.unified_msg_origin, conv_id)
            if not conv:
                return
            yield event.request_llm(
                prompt=event.get_message_str() or "",
                session_id=event.session_id,
                conversation=conv,
            )

    # ── Handler：移除空行 ────────────────────────────────────────────────

    @filter.on_decorating_result(priority=-100)
    async def remove_blank_lines(self, event: AstrMessageEvent):
        if not feature_enabled(self.config, FEATURE_REMOVE_BLANK):
            return
        result = event.get_result()
        if not result or not getattr(result, "chain", None):
            return
        max_nl = int(
            cfg_value(self.config, FEATURE_REMOVE_BLANK, "max_consecutive_newlines", 1)
            or 1
        )
        for comp in result.chain:
            if isinstance(comp, Comp.Plain):
                comp.text = collapse_blank_lines(comp.text, max_nl)

    # ── Handler：Mention 标签解析（@ 功能）───────────────────────────────

    @filter.on_decorating_result(priority=-50)
    async def parse_mention_tags(self, event: AstrMessageEvent):
        """将 LLM 输出中的 <mention> 标签转换为平台 At 组件。"""
        if not feature_enabled(self.config, FEATURE_MENTION_PARSER):
            return
        result = event.get_result()
        if not result or not result.chain:
            return
        transformed = transform_mention_in_chain(result.chain)
        if transformed is not None:
            result.chain = transformed

    # ── Handler：思考表情 ────────────────────────────────────────────────

    @filter.on_waiting_llm_request()
    async def on_waiting(self, event: AstrMessageEvent):
        if not feature_enabled(self.config, FEATURE_THINKING):
            return
        if not self.thinking.is_aiocqhttp(event):
            return
        msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if msg_id:
            event.set_extra("thinking_active", True)
            event.set_extra("thinking_msg_id", msg_id)
            await self.thinking.emoji(
                event,
                msg_id,
                cfg_value(
                    self.config, FEATURE_THINKING, "thinking_emoji_ids", []
                ),
                True,
            )

    @filter.after_message_sent()
    async def after_sent(self, event: AstrMessageEvent):
        if not feature_enabled(self.config, FEATURE_THINKING):
            return
        if not event.get_extra("thinking_active", False):
            return
        msg_id = event.get_extra("thinking_msg_id")
        if not msg_id:
            return
        thinking_cfg = feature_cfg(self.config, FEATURE_THINKING)
        if thinking_cfg.get("add_done_emoji"):
            await self.thinking.emoji(event, msg_id, thinking_cfg.get("done_emoji_ids", []), True)
        if thinking_cfg.get("remove_thinking_on_done"):
            await self.thinking.emoji(
                event, msg_id, thinking_cfg.get("thinking_emoji_ids", []), False
            )
        event.set_extra("thinking_done", True)

    # ── Handler：风格学习 - 聊天记录收集 ────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        if self._blocked(event) or not self.style:
            return
        if not feature_enabled(self.config, FEATURE_STYLE):
            return
        if event.get_sender_id() == event.get_self_id():
            return
        content = event.get_message_str()
        if not content or content.startswith("/"):
            return
        await self.style.data.add_message_to_history(
            event.unified_msg_origin,
            {
                "sender": event.get_sender_name(),
                "content": content,
                "timestamp": asyncio.get_running_loop().time(),
            },
        )

    # ── Handler：LLM 请求注入（风格 + 活跃发言人）───────────────────────

    @filter.on_llm_request(priority=17)
    async def on_llm_request(self, event: AstrMessageEvent, req):
        # 剥离平台 LTM 与重复片段，避免 prompt 膨胀
        cleaned = clean_system_prompt(req.system_prompt or "")
        if cleaned != (req.system_prompt or ""):
            req.system_prompt = cleaned

        from astrbot.core.agent.message import TextPart

        # 风格注入（临时内容，不持久化）
        if self.style and feature_enabled(self.config, FEATURE_STYLE):
            style_text = await self.style.injector.build_injection_text(
                event.unified_msg_origin,
                user_message=req.prompt or "",
            )
            if style_text:
                req.extra_user_content_parts.append(TextPart(text=style_text).mark_as_temp())

        # 活跃发言人列表注入（@ 功能）
        if feature_enabled(self.config, FEATURE_MENTION_PARSER):
            from astrbot.api.platform import MessageType

            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                speakers_text = self.speakers.build_speakers_prompt(event.unified_msg_origin)
                if speakers_text:
                    req.extra_user_content_parts.append(
                        TextPart(text=speakers_text).mark_as_temp()
                    )

    # ── Handler：风格命令 ────────────────────────────────────────────────

    @filter.command("风格状态")
    async def style_status(self, event: AstrMessageEvent):
        if not self.style:
            yield event.plain_result("风格学习功能未初始化。")
            return
        summary = self.style.injector.get_style_summary(event.unified_msg_origin)
        if not summary["has_styles"]:
            yield event.plain_result("当前会话还没有学习到任何风格特点。")
            return
        preview = "、".join(summary["universal_preview"])
        response = f"当前会话风格状态：\n通用风格：{summary['universal_count']} 条"
        if preview:
            response += f"\nTop-{min(3, summary['universal_count'])}：{preview}"
        if summary.get("situational_count"):
            response += f"\n场景化表达：{summary['situational_count']} 条（仅在语境匹配时注入）"
        yield event.plain_result(response)

    @filter.command("清空风格")
    async def clear_styles(self, event: AstrMessageEvent):
        if not self.style:
            yield event.plain_result("风格学习功能未初始化。")
            return
        await self.style.data.clear_universal(event.unified_msg_origin)
        yield event.plain_result("已清空当前会话的所有学习风格。")

    @filter.command("学习总结")
    async def learn_now(self, event: AstrMessageEvent):
        if not self.style:
            yield event.plain_result("风格学习功能未初始化。")
            return
        session_id = event.unified_msg_origin
        chat_history = self.style.data.get_chat_history(session_id, limit=100)
        min_history = int(
            cfg_value(self.config, FEATURE_STYLE, "min_history_for_analysis", 10) or 10
        )
        if len(chat_history) < min_history:
            yield event.plain_result(
                f"当前会话聊天记录不足 {min_history} 条，无法进行分析。"
            )
            return

        yield event.plain_result("正在分析聊天记录并学习风格特征，请稍候...")
        try:
            provider_id = (
                cfg_value(self.config, FEATURE_STYLE, "style_provider_id", "")
                or ""
            ).strip()
            await self.style.learn(session_id, provider_id=provider_id)
            summary = self.style.injector.get_style_summary(session_id)
            preview = "、".join(summary["universal_preview"])
            response = f"学习分析完成！\n通用风格：{summary['universal_count']} 条"
            if preview:
                response += f"\nTop-{min(3, summary['universal_count'])}：{preview}"
            yield event.plain_result(response)
        except Exception as e:
            logger.error(f"[烤箱-风格学习] 手动触发学习分析失败: {e}")
            yield event.plain_result(f"学习分析失败：{e}")
