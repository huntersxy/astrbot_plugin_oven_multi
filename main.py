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
    FEATURE_FAVOR,
    FEATURE_ACTIVE_REPLY,
    FEATURE_REMOVE_BLANK,
    FEATURE_THINKING,
)
from .features.bracket_matcher import BracketMatcher
from .features.repeater import Repeater
from .features.thinking_manager import ThinkingManager
from .features.active_reply import ActiveReply
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

        # 好感度系统
        self.favor_manager = None
        self._init_favor_system()

        # 余额查询
        self.balance_checker = BalanceChecker(self.config)

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
            self.style_injector = StyleInjector(self.style_data_manager, self.config)
            logger.info("[烤箱-风格学习] 初始化完成")
        except Exception as e:
            logger.error(f"[烤箱-风格学习] 初始化失败: {e}")

    def _init_favor_system(self):
        if not self.config_mgr.is_feature_enabled(FEATURE_FAVOR):
            return
        try:
            from .favor_manager import FavorManager

            self.favor_manager = FavorManager(self.config)
        except Exception as e:
            logger.error(f"[烤箱-好感度] 初始化失败: {e}")

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
        if self.favor_manager:
            self.favor_manager.force_save()

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

        fcfg = self.config_mgr.get_feature_config(FEATURE_FAVOR)
        favor_enabled = isinstance(fcfg, dict) and fcfg.get("enabled", True)
        response += f"💖 好感度: {'✅ 启用' if favor_enabled else '❌ 禁用'}\n"

        ar = self.config_mgr.get_feature_config(FEATURE_ACTIVE_REPLY)
        ar_enabled = isinstance(ar, dict) and ar.get("enable", False)
        response += f"🎯 主动回复: {'✅ 启用' if ar_enabled else '❌ 禁用'}\n"
        if ar_enabled:
            response += f"   └─ 模式: {ar.get('mode', 'probability')}\n"
            if ar.get("mode") == "probability":
                response += f"   └─ 回复概率: {ar.get('possibility', 0.1):.0%}\n"

        yield event.plain_result(response)

    # ── Handler：群消息处理 ──

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if not self._is_enabled(event):
            return

        content = event.message_obj.message_str

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

        # 静态好感度指令
        if self.favor_manager:
            from .favor_manager import FavorManager

            req.system_prompt += "\n\n" + FavorManager.INSTRUCTION_TEXT

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

        # 黑名单检查
        user_id = str(event.get_sender_id())
        _is_blacklisted = False
        if self.favor_manager:
            _is_blacklisted = self.favor_manager.is_blacklisted(
                user_id,
                session_id if self.favor_manager.session_based_blacklist else None,
            )
        if _is_blacklisted:
            logger.debug(f"[烤箱-好感度] 用户 {user_id} 在黑名单中，拦截 LLM 请求")
            event.stop_event()
            return

        # 风格注入
        style_text = None
        if self.style_injector and self.config_mgr.is_feature_enabled(FEATURE_STYLE):
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

        # 好感度关系描述
        favor_text = None
        if self.favor_manager:
            favor_text = self.favor_manager.build_relationship_text(
                user_id,
                session_id if self.favor_manager.session_based_favor else None,
            )
            if favor_text:
                _d["favor_len"] = len(favor_text)
                from astrbot.core.agent.message import TextPart

                part = TextPart(text=favor_text)
                if hasattr(part, "mark_as_temp"):
                    part.mark_as_temp()
                req.extra_user_content_parts.append(part)

        # 差分日志
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
        if not self.favor_manager:
            return
        if not self.config_mgr.is_feature_enabled(FEATURE_FAVOR):
            return

        fcfg = self.config_mgr.get_feature_config(FEATURE_FAVOR)
        user_id = str(event.get_sender_id())
        session_id = event.unified_msg_origin
        text = resp.completion_text or ""

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

        if fcfg.get("clean_response", True):
            cleaned = self.favor_manager.clean_response_text(text)
            if cleaned != text:
                resp.completion_text = cleaned

    # ── Handler：风格/好感度命令 ──

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

    # ── Handler：好感度命令 ──

    @filter.command("好感度")
    async def query_favor(self, event: AstrMessageEvent):
        if not self.favor_manager:
            yield event.plain_result("好感度系统未初始化。")
            return
        user_id = str(event.get_sender_id())
        session_id = event.unified_msg_origin
        f_session = session_id if self.favor_manager.session_based_favor else None

        if self.favor_manager.is_blacklisted(
            user_id,
            session_id if self.favor_manager.session_based_blacklist else None,
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
    async def admin_control(
        self, event: AstrMessageEvent, cmd: str, target: str = None, value: int = None
    ):
        if not self.favor_manager:
            yield event.plain_result("好感度系统未初始化。")
            return
        admins = self._parse_favor_admins()
        if str(event.get_sender_id()) not in admins:
            yield event.plain_result("⚠️ 你没有权限执行此操作")
            return

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
                    fm.reset_low_counter(
                        target, session_id if fm.session_based_counter else None
                    )
                    fm.set_favor(target, 0, f_session)
                    yield event.plain_result(
                        f"✅ 用户 {target} 已移出黑名单，并重置好感度和计数器"
                    )
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
                            yield event.plain_result(
                                f"✅ 已设置计数器减少间隔为 {value} 小时"
                            )
                    elif target == "数量" and value is not None:
                        if value <= 0:
                            yield event.plain_result("⚠️ 减少数量必须大于0")
                        else:
                            fm.auto_decrease_amount = value
                            yield event.plain_result(
                                f"✅ 已设置计数器每次减少数量为 {value}"
                            )
                    else:
                        yield event.plain_result(
                            "❌ 无效的参数，可用参数：开启/关闭/间隔/数量"
                        )
            else:
                yield event.plain_result(
                    "❌ 无效指令，可用命令：好感度/黑名单/移出黑名单/白名单/移出白名单/计数器"
                )
        except ValueError:
            yield event.plain_result("❌ 数值参数必须为整数")
        except Exception as e:
            yield event.plain_result(f"⚠️ 操作失败：{str(e)}")

    def _parse_favor_admins(self) -> list[str]:
        fcfg = self.config_mgr.get_feature_config(FEATURE_FAVOR)
        admins = fcfg.get("admins_id", [])
        if isinstance(admins, str):
            return [x.strip() for x in admins.split(",")]
        return [str(x) for x in admins]
