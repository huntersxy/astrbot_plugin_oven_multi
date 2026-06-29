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
#   - astrbot_plugin_image_caption_cache (AGPL-3.0) by Florance — image caption caching
# Date: 2026-06-23

import asyncio
import json
import re

from quart import jsonify

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import Provider
import astrbot.api.message_components as Comp

from .core.config_manager import ConfigManager
from .utils.constants import (
    PLUGIN_NAME,
    PLUGIN_VERSION,
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    FEATURE_BRACKET,
    FEATURE_REPETITION,
    FEATURE_STYLE,
    FEATURE_ACTIVE_REPLY,
    FEATURE_REMOVE_BLANK,
    FEATURE_THINKING,
    FEATURE_IMAGE_CAPTION_CACHE,
    FEATURE_MENTION_PARSER,
)
from .features.bracket_matcher import BracketMatcher
from .features.repeater import Repeater
from .features.thinking_manager import ThinkingManager
from .features.active_reply import ActiveReply
from .features.image_caption_cache import ImageCaptionCacheFeature
from .features.mention_parser import ActiveSpeakersTracker, transform_mention_in_chain
from .learning_style.data_manager import DataManager as StyleDataManager
from .learning_style.learning_manager import LearningManager
from .learning_style.scheduler import Scheduler as StyleScheduler
from .learning_style.style_injector import StyleInjector
from .balance_checker import BalanceChecker

def collapse_blank_lines(text, max_newlines=1):
    limit = max(int(max_newlines), 0)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{" + str(limit + 1) + r",}", "\n" * limit, normalized)


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class OvenMultiPlugin(Star):
    """插座的多功能烤箱 - 主插件类

    作为插件入口，协调各功能模块。Handler 注册在本类中，
    具体逻辑委托给 features/ 下的独立模块。
    """

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)

        if config is None:
            config = AstrBotConfig({})
        self.config = config
        self.config_mgr = ConfigManager(config)

        # 初始化功能模块
        self.matcher = BracketMatcher()
        self.repeater = Repeater()
        self.thinking = ThinkingManager()
        self.active_reply = ActiveReply()

        # 风格学习
        self.style_data_manager = None
        self.style_learning_manager = None
        self.style_scheduler = None
        self.style_injector = None
        self._init_style_learning()

        # 余额查询
        self.balance_checker = BalanceChecker(self.config)

        # 图片转述缓存
        self.image_caption_cache = ImageCaptionCacheFeature(self.config_mgr)
        self.image_caption_cache.initialize()

        # @功能 - 活跃发言人追踪
        max_speakers = self.config_mgr.get_config_value(
            FEATURE_MENTION_PARSER, "max_speakers", 50
        )
        self.speakers_tracker = ActiveSpeakersTracker(max_speakers=max_speakers)

        # 注册 Web API
        self._register_web_api()

    # ── 初始化方法 ──

    def _init_style_learning(self):
        if not self.config_mgr.is_feature_enabled(FEATURE_STYLE):
            return
        try:
            data_dir = StarTools.get_data_dir(PLUGIN_NAME)
            self.style_data_manager = StyleDataManager(data_dir, self.config)
            self.style_learning_manager = LearningManager(
                self, self.style_data_manager, self.config
            )
            self.style_scheduler = StyleScheduler(
                self.style_data_manager, self.style_learning_manager, self.config
            )
            self.style_injector = StyleInjector(self.style_data_manager, self.config, self.context)
            logger.info("[烤箱-风格学习] 初始化完成")
        except Exception as e:
            logger.error(f"[烤箱-风格学习] 初始化失败: {e}")

    def _register_web_api(self):
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

    # ── 生命周期 ──

    async def initialize(self):
        if self.style_scheduler:
            self.style_scheduler.start()
        logger.info("[插座烤箱] 启动")

    async def terminate(self):
        if self.style_scheduler:
            await self.style_scheduler.stop()
        if self.style_data_manager:
            await self.style_data_manager.force_save()
        self.image_caption_cache.cleanup()

    # ── Web API ──

    async def _api_balance(self):
        results = await self.balance_checker.query_all()
        return jsonify({"success": True, "data": results})

    async def _api_style_status(self):
        if not self.style_data_manager:
            return jsonify({"success": True, "data": {}})
        result = {}
        for session_id in self.style_data_manager.universal:
            universal = self.style_data_manager.get_universal_for_session(session_id)
            history = self.style_data_manager.get_chat_history(session_id, limit=50)
            result[session_id] = {
                "session_id": session_id,
                "display_name": (
                    session_id.split("_")[-1] if "_" in session_id else session_id
                ),
                "universal": universal,
                "history": history,
            }
        return jsonify({"success": True, "data": result})

    # ── 工具方法 ──

    def _is_enabled(self, event):
        uid = event.message_obj.sender.user_id
        gid = event.message_obj.group_id
        return not self.config_mgr.is_blacklisted(
            group_id=str(gid), user_id=str(uid)
        )

    # ── Handler：烤箱状态 ──

    @filter.command("烤箱状态")
    async def oven_status(self, event: AstrMessageEvent):
        bm = self.config_mgr.get_feature_config(FEATURE_BRACKET)
        rep = self.config_mgr.get_feature_config(FEATURE_REPETITION)
        blank = self.config_mgr.get_feature_config(FEATURE_REMOVE_BLANK)
        thinking_cfg = self.config_mgr.get_feature_config(FEATURE_THINKING)
        style = self.config_mgr.get_feature_config(FEATURE_STYLE)

        response = "🍳 插座烤箱状态\n\n"
        response += f"🔗 括号匹配: {'✅ 启用' if bm.get('enabled') else '❌ 禁用'}\n"
        response += f"🔄 消息复读: {'✅ 启用' if rep.get('enabled') else '❌ 禁用'}\n"
        if rep.get("enabled"):
            response += f"   └─ 打断施法概率: {(rep.get('break_spell_probability', 0.3) * 100):.0f}%\n"
            response += f"   └─ 打断文本: {rep.get('break_spell_text', '打断施法！')}\n"
        response += f"📝 移除空行: {'✅ 启用' if blank.get('enabled') else '❌ 禁用'}\n"
        if blank.get("enabled"):
            response += f"   └─ 最大连续换行: {blank.get('max_consecutive_newlines', 1)} 行\n"
        response += f"💭 思考表情: {'✅ 启用' if thinking_cfg.get('enabled') else '❌ 禁用'}\n"
        style_enabled = style.get("enabled", True)
        response += f"🎭 风格学习: {'✅ 启用' if style_enabled else '❌ 禁用'}\n"
        if self.style_injector and style_enabled:
            session_id = event.unified_msg_origin
            summary = self.style_injector.get_style_summary(session_id)
            if summary["has_styles"]:
                response += f"   └─ 通用: {summary['universal_count']} 条\n"
            cross_group = style.get("enable_cross_group", False)
            response += f"   └─ 跨群风格: {'✅ 启用' if cross_group else '❌ 禁用'}\n"
            if cross_group and summary.get("cross_group_trait_sources"):
                response += f"   └─ 全局风格来源: {summary['cross_group_trait_sources']} 条\n"

        ar = self.config_mgr.get_feature_config(FEATURE_ACTIVE_REPLY)
        ar_enabled = isinstance(ar, dict) and ar.get("enable", False)
        response += f"🎯 主动回复: {'✅ 启用' if ar_enabled else '❌ 禁用'}\n"
        if ar_enabled:
            response += f"   └─ 模式: {ar.get('mode', 'probability')}\n"
            if ar.get("mode") == "probability":
                response += f"   └─ 回复概率: {ar.get('possibility', 0.1):.0%}\n"

        icc = self.config_mgr.get_feature_config(FEATURE_IMAGE_CAPTION_CACHE)
        icc_enabled = icc.get("enabled", True) if isinstance(icc, dict) else True
        response += f"📷 图片转述缓存: {'✅ 启用' if icc_enabled else '❌ 禁用'}\n"
        if icc_enabled:
            response += f"   └─ TTL: {icc.get('image_caption_cache_ttl', 600)} 秒\n"
            response += f"   └─ 上限: {icc.get('max_cached_images', 200)} 张\n"

        yield event.plain_result(response)

    # ── Handler：群消息处理 ──

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if not self._is_enabled(event):
            return

        content = event.message_obj.message_str

        # 追踪活跃发言人（用于 @ 功能）
        if self.config_mgr.is_feature_enabled(FEATURE_MENTION_PARSER):
            user_id = event.get_sender_id()
            nickname = event.message_obj.sender.nickname
            if user_id and nickname:
                self.speakers_tracker.record(
                    event.unified_msg_origin, str(user_id), nickname
                )

        # 括号匹配
        if self.config_mgr.is_feature_enabled(FEATURE_BRACKET):
            brackets = self.matcher.check(content)
            if brackets:
                asyncio.create_task(self._send_bracket_reply(event, brackets))

        # 消息复读
        if self.config_mgr.is_feature_enabled(FEATURE_REPETITION):
            rep_cfg = self.config_mgr.get_feature_config(FEATURE_REPETITION)
            result = self.repeater.check(
                event.unified_msg_origin, event.message_obj.message, rep_cfg
            )
            if result:
                asyncio.create_task(self._send_repeat_reply(event, result))

        # 主动回复
        if await self.active_reply.should_active_reply(event, self.config, self.context):
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin,
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

    async def _send_bracket_reply(self, event: AstrMessageEvent, brackets: str):
        try:
            await event.send(event.plain_result(brackets))
        except Exception as e:
            logger.error(f"[插座烤箱] 发送括号补全失败: {e}")

    async def _send_repeat_reply(self, event: AstrMessageEvent, result):
        try:
            if result[0] == "break":
                await event.send(event.plain_result(result[1]))
            elif result[0] == "repeat":
                await event.send(event.chain_result(result[1]))
        except Exception as e:
            logger.error(f"[插座烤箱] 发送复读消息失败: {e}")

    # ── Handler：移除空行 ──

    @filter.on_decorating_result(priority=-100)
    async def remove_blank_lines(self, event: AstrMessageEvent):
        if not self.config_mgr.is_feature_enabled(FEATURE_REMOVE_BLANK):
            return
        result = event.get_result()
        if result and getattr(result, "chain", None):
            max_nl = self.config_mgr.get_config_value(
                FEATURE_REMOVE_BLANK, "max_consecutive_newlines", 1
            )
            for comp in result.chain:
                if isinstance(comp, Comp.Plain):
                    comp.text = collapse_blank_lines(comp.text, max_nl)

    # ── Handler：Mention 标签解析（@ 功能）──

    @filter.on_decorating_result(priority=-50)
    async def parse_mention_tags(self, event: AstrMessageEvent):
        """将 LLM 输出中的 <mention> 标签转换为平台 At 组件。"""
        if not self.config_mgr.is_feature_enabled(FEATURE_MENTION_PARSER):
            return
        result = event.get_result()
        if not result or not result.chain:
            return
        transformed = transform_mention_in_chain(result.chain)
        if transformed is None:
            return
        result.chain = transformed

    # ── Handler：思考表情 ──

    @filter.on_waiting_llm_request()
    async def on_waiting(self, event: AstrMessageEvent):
        if not self.config_mgr.is_feature_enabled(FEATURE_THINKING):
            return
        if not self.thinking.is_aiocqhttp(event):
            return
        thinking_cfg = self.config_mgr.get_feature_config(FEATURE_THINKING)
        msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if msg_id:
            event.set_extra("thinking_active", True)
            event.set_extra("thinking_msg_id", msg_id)
            await self.thinking.emoji(
                event, msg_id, thinking_cfg.get("thinking_emoji_ids", []), True
            )

    @filter.after_message_sent()
    async def after_sent(self, event: AstrMessageEvent):
        if not self.config_mgr.is_feature_enabled(FEATURE_THINKING):
            return
        if not event.get_extra("thinking_active", False):
            return
        thinking_cfg = self.config_mgr.get_feature_config(FEATURE_THINKING)
        msg_id = event.get_extra("thinking_msg_id")
        if msg_id:
            if thinking_cfg.get("add_done_emoji"):
                await self.thinking.emoji(
                    event, msg_id, thinking_cfg.get("done_emoji_ids", []), True
                )
            if thinking_cfg.get("remove_thinking_on_done"):
                await self.thinking.emoji(
                    event, msg_id, thinking_cfg.get("thinking_emoji_ids", []), False
                )
            event.set_extra("thinking_done", True)

    # ── Handler：风格学习 ──

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        if not self._is_enabled(event):
            return
        if event.get_sender_id() == event.get_self_id():
            return
        if not self.style_data_manager:
            return
        if not self.config_mgr.is_feature_enabled(FEATURE_STYLE):
            return

        session_id = event.unified_msg_origin
        message_content = event.get_message_str()
        if not message_content:
            return
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

        # 前置清理：剥离平台 LTM 并去重
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

        # 差分捕捉
        _d = {}
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

        # 风格注入
        style_text = None
        if self.style_injector and self.config_mgr.is_feature_enabled(FEATURE_STYLE):
            # 获取 Provider 用于嵌入选择
            _provider = None
            try:
                _provider = self.context.get_using_provider()
            except Exception:
                pass
            style_text = await self.style_injector.build_injection_text(
                session_id,
                user_message=req.prompt or "",
                provider=_provider,
            )
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

        # 差分日志
        try:
            if hasattr(req, "extra_user_content_parts") and isinstance(
                req.extra_user_content_parts, list
            ):
                _d["extra_count_after"] = len(req.extra_user_content_parts)
                injected = bool(style_text)
                other_count = _d["extra_count_after"] - _d["extra_count_before"] - injected
                if other_count > 0:
                    logger.info(
                        f"[烤箱] 检测到其他插件 {other_count} 个 extra_user_content_parts"
                    )
        except Exception:
            pass

        _d["injected"] = bool(style_text)
        logger.debug(f"[烤箱] on_llm_request 摘要: {_d}")

    # ── Handler：活跃发言人注入（@ 功能 ──

    @filter.on_llm_request(priority=18)
    async def on_llm_request_speakers(self, event: AstrMessageEvent, req):
        """在风格注入之后追加活跃发言人列表，让 AI 知道可以 @ 谁。"""
        if not self.config_mgr.is_feature_enabled(FEATURE_MENTION_PARSER):
            return
        from astrbot.api.platform import MessageType
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return

        _debug = self.config_mgr.get_config_value("mention_parser", "debug_mode", False)

        speakers_text = self.speakers_tracker.build_speakers_prompt(
            event.unified_msg_origin
        )
        if not speakers_text:
            if _debug:
                tracked = self.speakers_tracker._speakers.get(event.unified_msg_origin)
                logger.info(
                    f"[烤箱-@功能] Debug 模式 - 无注入内容 | origin={event.unified_msg_origin}"
                    f" | 追踪中发言人: {len(tracked) if tracked else 0} 人"
                )
            return

        from astrbot.core.agent.message import TextPart

        part = TextPart(text=speakers_text)
        if hasattr(part, "mark_as_temp"):
            part.mark_as_temp()
        req.extra_user_content_parts.append(part)

        if _debug:
            tracked = self.speakers_tracker._speakers.get(event.unified_msg_origin)
            logger.info(
                f"[烤箱-@功能] Debug 模式 - 注入内容 | origin={event.unified_msg_origin}"
                f" | 追踪中发言人: {len(tracked) if tracked else 0} 人\n"
                f"完整注入文本:\n{speakers_text}"
            )

        logger.debug(
            f"[烤箱-@功能] 注入活跃发言人列表 | origin={event.unified_msg_origin}"
        )

    # ── Handler：AstrBot 加载完成 ──

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """在 AstrBot 完全加载后再次尝试应用缓存补丁。"""
        self.image_caption_cache.apply_patches_on_loaded()

    # ── Handler：图片转述缓存命令 ──

    @filter.command("image_caption_cache_stats")
    async def image_caption_cache_stats(self, event: AstrMessageEvent):
        """查看图片转述缓存状态。"""
        yield event.plain_result(self.image_caption_cache.get_stats_text())

    @filter.command("image_caption_cache_clear")
    async def image_caption_cache_clear(self, event: AstrMessageEvent):
        """清空图片转述缓存。"""
        removed = self.image_caption_cache.clear_cache()
        yield event.plain_result(f"已清空图片转述缓存（{removed} 条）。")

    # ── Handler：风格命令 ──

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
            style_cfg = self.config_mgr.get_feature_config(FEATURE_STYLE)
            style_provider = (style_cfg.get("style_provider_id", "") or "").strip()
            await self.style_learning_manager.analyze_and_learn(
                session_id, provider_id=style_provider
            )
            summary = self.style_injector.get_style_summary(session_id)
            response = "学习分析完成！\n"
            response += f"通用风格：{summary['universal_count']} 条"
            if summary["universal_preview"]:
                response += f"\nTop-{min(3, summary['universal_count'])}：{', '.join(summary['universal_preview'])}"
            yield event.plain_result(response)
        except Exception as e:
            logger.error(f"[烤箱-风格学习] 手动触发学习分析失败: {e}")
            yield event.plain_result(f"学习分析失败：{e}")

    # ── 工具方法 ──

    def _is_enabled(self, event):
        uid = event.message_obj.sender.user_id
        gid = event.message_obj.group_id
        return not self.config_mgr.is_blacklisted(
            group_id=str(gid), user_id=str(uid)
        )
