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
#   - astrbot_plugin_iearning_style (AGPL-3.0) by qa296
#     historical style learning (retired in 1.49.0)
#   - astrbot_plugin_remove_blank_lines (MIT) by Codex — remove blank lines from LLM output
#   - astrbot_plugin_file_reader_pro (MIT) by zz6zz666 — file reading, RAG indexing
#     (original license: see features/file_reader/LICENSE)
# Date: 2026-06-23

import re
from sys import maxsize

import astrbot.api.message_components as components
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .constants import (
    FEATURE_ACTIVE_REPLY,
    FEATURE_BRACKET,
    FEATURE_FILE_READER,
    FEATURE_JEV,
    FEATURE_MENTION_PARSER,
    FEATURE_REMOVE_BLANK,
    FEATURE_REPETITION,
    FEATURE_THINKING,
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)
from .features.active_reply import ActiveReply
from .features.balance_checker import BalanceChecker
from .features.bracket_matcher import BracketMatcher
from .features.file_reader import FileParseError, FileReaderManager
from .features.mention_parser import ActiveSpeakersTracker, transform_mention_in_chain
from .features.mention_preserve import restore_bot_mention, should_backfill_native
from .features.repeater import Repeater
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
    return (bool(group_id) and str(group_id) in config.get("blacklist_groups", [])) or (
        bool(user_id) and str(user_id) in config.get("blacklist_users", [])
    )


def collapse_blank_lines(text: str, max_newlines: int = 1) -> str:
    """将连续换行压缩为至多 max_newlines 个。"""
    limit = max(int(max_newlines), 0)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(rf"\n{{{limit + 1},}}", "\n" * limit, normalized)


# 主动回复触发时注入 LLM 的默认引导，用于让模型理解“这是机器人主动加入话题，不是用户来找机器人”
DEFAULT_ACTIVE_REPLY_GUIDANCE = (
    "这是一条由机器人主动发起的群聊回复，不是用户主动寻找机器人或向机器人提问。\n"
    "用户没有在找机器人，机器人是在看到群聊话题后主动加入聊天。\n"
    "请以群聊参与者的自然口吻回复：\n"
    "1. 不要使用“有什么可以帮你”“我在”“已收到”等服务式或被召唤式的开头；\n"
    "2. 不要解释或提及自己是主动回复、被动触发等机制；\n"
    "3. 尽量简短自然地融入当前话题，像普通群友一样接话；\n"
    "4. 如果当前话题不适合接话，可以克制或不强行发言。"
)


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
        self.active_reply = ActiveReply(context=self.context)

        # 余额查询
        self.balance_checker = BalanceChecker(self.config)

        # @功能 - 活跃发言人追踪
        self.speakers = ActiveSpeakersTracker(
            max_speakers=int(
                cfg_value(self.config, FEATURE_MENTION_PARSER, "max_speakers", 50) or 50
            ),
            data_dir=self.data_dir,
        )

        # 文件读取（预读取 / RAG 检索 / LLM Tool）
        self.file_reader: FileReaderManager | None = None
        if feature_enabled(self.config, FEATURE_FILE_READER, False):
            try:
                self.file_reader = FileReaderManager(
                    self,
                    feature_cfg(self.config, FEATURE_FILE_READER),
                    self.data_dir,
                )
                logger.info("[烤箱-文件读取] 初始化完成")
            except Exception as e:
                logger.error(f"[烤箱-文件读取] 初始化失败: {e}")

        # Remove retired handlers retained by older versions during hot reload.
        registrations = getattr(self.context, "registered_web_apis", [])
        retired_routes = {f"/{PLUGIN_NAME}/style_status", f"/{PLUGIN_NAME}/style/manage"}
        registrations[:] = [item for item in registrations if item[0] not in retired_routes]

        # Register only read-only native Panel APIs.
        for route, handler, description in (
            (f"/{PLUGIN_NAME}/status", self._api_status, "Oven Multi feature status"),
            (f"/{PLUGIN_NAME}/balance", self._api_balance, "Oven Multi provider balances"),
        ):
            self.context.register_web_api(route, handler, ["GET"], description)

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def initialize(self):
        if self.file_reader:
            await self.file_reader.start()
        logger.info("[插座烤箱] 启动")

    async def terminate(self):
        if self.file_reader:
            await self.file_reader.stop()
        await self.active_reply.close()
        await self.balance_checker.terminate()
        registrations = getattr(self.context, "registered_web_apis", [])
        registrations[:] = [
            item for item in registrations if getattr(item[1], "__self__", None) is not self
        ]

    # ── Web API ──────────────────────────────────────────────────────────

    def _feature_status(self) -> list[dict]:
        """Describe configured features without external requests or private data.

        Returns:
            Feature identifiers, configuration switches and human-readable details.
        """
        active = feature_cfg(self.config, FEATURE_ACTIVE_REPLY)
        jev = feature_cfg(self.config, FEATURE_JEV)
        files = feature_cfg(self.config, FEATURE_FILE_READER)
        blank_limit = cfg_value(self.config, FEATURE_REMOVE_BLANK, "max_consecutive_newlines", 1)
        mode = "Jev 判定" if active.get("mode") == "model_choice" else "概率触发"
        rows = [
            (
                FEATURE_BRACKET,
                "括号匹配",
                feature_enabled(self.config, FEATURE_BRACKET),
                "补齐消息中未闭合的中英文括号",
            ),
            (
                FEATURE_REPETITION,
                "消息复读",
                feature_enabled(self.config, FEATURE_REPETITION),
                "跟随连续相同消息，支持打断复读",
            ),
            (
                FEATURE_REMOVE_BLANK,
                "空行整理",
                feature_enabled(self.config, FEATURE_REMOVE_BLANK),
                f"最多保留 {blank_limit} 个连续换行",
            ),
            (
                FEATURE_THINKING,
                "思考表情",
                feature_enabled(self.config, FEATURE_THINKING),
                "用 QQ 表情标记开始处理与回复完成",
            ),
            (
                FEATURE_MENTION_PARSER,
                "成员提及",
                feature_enabled(self.config, FEATURE_MENTION_PARSER),
                "追踪活跃成员，将模型提及标签转换为真实 @",
            ),
            (FEATURE_ACTIVE_REPLY, "主动回复", active.get("enable", False), mode),
            (
                FEATURE_JEV,
                "Jev 多维判读",
                jev.get("enabled", False),
                "理解说话对象、意图、情绪、态度、回复期待与风险",
            ),
            (
                FEATURE_FILE_READER,
                "文件读取",
                self.file_reader is not None,
                (
                    "预读取与按需工具"
                    if (files.get("preread") or {}).get("enabled", True)
                    else "按需文件工具"
                )
                if self.file_reader
                else "在插件配置中开启文件读取",
            ),
        ]
        return [
            {"id": key, "name": name, "enabled": bool(enabled), "detail": detail}
            for key, name, enabled, detail in rows
        ]

    async def _api_status(self):
        """Return fast, credential-free Panel state without querying providers.

        Returns:
            Native Panel response containing configuration state and actionable notices.
        """
        active = feature_cfg(self.config, FEATURE_ACTIVE_REPLY)
        jev = feature_cfg(self.config, FEATURE_JEV)
        notices = []
        if jev.get("enabled") and not str(jev.get("api_key") or "").strip():
            notices.append("Jev 已开启但未配置 API Key，请在插件配置中填写后使用判读。")
        if active.get("enable") and active.get("mode") == "model_choice" and not jev.get("enabled"):
            notices.append("主动回复选择了 Jev 判定模式，但 Jev 总开关未开启，当前不会触发。")
        if feature_enabled(self.config, FEATURE_FILE_READER, False) and not self.file_reader:
            notices.append("文件读取已配置开启，但尚未初始化成功，请检查 AstrBot 日志。")
        return {
            "success": True,
            "data": {
                "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
                "features": self._feature_status(),
                "notices": notices,
                "balance_configured": bool(
                    str(feature_cfg(self.config, "balance").get("config_content") or "").strip()
                ),
            },
        }

    async def _api_balance(self):
        """Query balances on demand, reusing a short-lived shared result.

        Returns:
            Native Panel response with results and the actual query timestamp.
        """
        items = await self.balance_checker.query_all()
        return {
            "success": True,
            "data": {
                "items": items,
                "checked_at": self.balance_checker.checked_at,
                "cache_seconds": self.balance_checker.cache_seconds,
            },
        }

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

        yield event.plain_result("\n".join(lines))

    # ── Handler：群消息预处理（最高优先级）────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=maxsize)
    async def on_early_group_message(self, event: AstrMessageEvent):
        """抢在 AstrBot 裸@拦截（handle_empty_mention，优先级 maxsize-1）之前：

        1. 把 aiocqhttp 适配器丢弃的首个「@机器人」补回 message_str；
        2. 为「纯@消息」补齐原生「群聊消息记录注入上下文」记录；
        3. 记录 Jev 判读上下文（含@消息，与主动回复触发闸门解耦）。
        """
        blocked = self._blocked(event)

        # debug_mode 开启时，每条群消息打一行待命状态：开关一开、下一条消息即可
        # 看到当前 enabled/api_key/场景开关/唤醒状态，静默不再是黑盒
        jev_cfg = feature_cfg(self.config, FEATURE_JEV)
        if jev_cfg.get("debug_mode"):
            api_key_ok = bool(str(jev_cfg.get("api_key") or "").strip())
            ar_cfg = feature_cfg(self.config, FEATURE_ACTIVE_REPLY)
            logger.info(
                f"[Jev-DEBUG] 待命 | origin={event.unified_msg_origin} "
                f"blocked={blocked} enabled={jev_cfg.get('enabled', False)} "
                f"api_key={'已配置' if api_key_ok else '未配置'} "
                f"主动注入={jev_cfg.get('inject_on_active_reply', True)} "
                f"被@注入={jev_cfg.get('inject_on_mention', True)} | "
                f"active_reply.enable={ar_cfg.get('enable', False)} "
                f"mode={ar_cfg.get('mode', 'probability')} "
                f"wake={event.is_at_or_wake_command}"
            )

        if blocked:
            return

        # 1. 保留 @机器人（改 message_str，不动消息链，不影响原生记录）
        restored = restore_bot_mention(event)
        if restored:
            logger.debug(
                f"[烤箱-@保留] 已补回 @机器人 | origin={event.unified_msg_origin} "
                f"text={event.get_message_str()!r}"
            )

        # 2. 补齐原生群聊上下文的纯@漏记（仅「群聊消息记录注入上下文」开启时）
        if should_backfill_native(event):
            try:
                await self._backfill_native_record(event)
            except Exception as e:  # noqa: BLE001
                # 跨版本原生实现差异只降级，不影响消息流程
                logger.debug(f"[烤箱-群上下文] 补记纯@消息失败：{e}")

        # 3. Jev 判读历史记录（含@消息）
        self.active_reply.record_history(event, self.config)

    async def _backfill_native_record(self, event: AstrMessageEvent) -> None:
        """为纯@消息调用原生 GroupChatContext.handle_message 补记一条上下文。"""
        if event.get_extra("_group_context_record_id") is not None:
            return  # 原生已记录，勿重复
        if event.get_extra("handlers_parsed_params", {}):
            return  # 指令消息不算群聊上下文
        if event.get_extra("oven_native_record_backfilled", False):
            return
        ltm = (
            self.context.get_config(umo=event.unified_msg_origin).get("provider_ltm_settings", {})
            or {}
        )
        if not ltm.get("group_icl_enable"):
            return  # 未开启「群聊消息记录注入上下文」
        from astrbot.core.star.star import star_map

        main_star = getattr(star_map.get("astrbot.builtin_stars.astrbot.main"), "star_cls", None)
        group_chat_context = getattr(main_star, "group_chat_context", None)
        if group_chat_context is None:
            return
        await group_chat_context.handle_message(event)
        event.set_extra("oven_native_record_backfilled", True)
        logger.debug(f"[烤箱-群上下文] 已为纯@消息补记原生记录 | origin={event.unified_msg_origin}")

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
            brackets = self.matcher.check(
                content,
                only_first=bool(
                    cfg_value(self.config, FEATURE_BRACKET, "only_first_missing", False)
                ),
            )
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
                    event.plain_result(payload) if kind == "break" else event.chain_result(payload)
                )

        # 主动回复
        if await self.active_reply.should_active_reply(event, self.config):
            # 标记本次 LLM 请求由主动回复触发，供 on_llm_request 注入“被动触发”引导
            event.set_extra("oven_active_reply_triggered", True)
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
            cfg_value(self.config, FEATURE_REMOVE_BLANK, "max_consecutive_newlines", 1) or 1
        )
        for comp in result.chain:
            if isinstance(comp, components.Plain):
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
                cfg_value(self.config, FEATURE_THINKING, "thinking_emoji_ids", []),
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

    # Native system prompts and histories remain unchanged.

    @filter.on_llm_request(priority=17)
    async def on_llm_request(self, event: AstrMessageEvent, req):
        from astrbot.core.agent.message import TextPart

        # 主动回复触发引导注入（临时内容，不持久化）
        if event.get_extra("oven_active_reply_triggered", False):
            ar_cfg = feature_cfg(self.config, FEATURE_ACTIVE_REPLY)
            guidance = ar_cfg.get("active_reply_guidance")
            if guidance is None:
                # 旧配置没有该字段时使用内置默认引导；显式留空则禁用注入
                guidance = DEFAULT_ACTIVE_REPLY_GUIDANCE
            guidance = str(guidance or "").strip()
            if guidance:
                req.extra_user_content_parts.append(TextPart(text=guidance).mark_as_temp())

            # Jev 多维判读注入（ActiveReply 触发时挂到 extra，同样不进历史）
            reading = event.get_extra("oven_jev_reading")
            if reading:
                req.extra_user_content_parts.append(TextPart(text=str(reading)).mark_as_temp())
        elif feature_enabled(self.config, FEATURE_JEV, False) and not self._blocked(event):
            # 被@/唤醒触发（非主动回复）的一次 Jev 判读注入：重点是意向与情绪
            block = await self.active_reply.mention_reading(event, self.config)
            if block:
                req.extra_user_content_parts.append(TextPart(text=str(block)).mark_as_temp())

        # 活跃发言人列表注入（@ 功能）
        if feature_enabled(self.config, FEATURE_MENTION_PARSER):
            from astrbot.api.platform import MessageType

            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                speakers_text = self.speakers.build_speakers_prompt(event.unified_msg_origin)
                if speakers_text:
                    req.extra_user_content_parts.append(TextPart(text=speakers_text).mark_as_temp())

    # ── Handler：文件读取 ────────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_file_message(self, event: AstrMessageEvent):
        """接收消息中的文件组件：登记副本，并按配置决定是否预读取向量化。"""
        if not self.file_reader or self._blocked(event):
            return
        if event.get_sender_id() == event.get_self_id():
            return
        file_items = [
            item for item in event.message_obj.message if isinstance(item, components.File)
        ]
        if not file_items:
            return

        fr_cfg = feature_cfg(self.config, FEATURE_FILE_READER)
        notify = bool((fr_cfg.get("preread") or {}).get("notify", True))

        for item in file_items:
            notice = None
            try:
                file_path = str(await item.get_file())
                file_name = str(getattr(item, "name", "") or "").strip() or file_path
                notice = await self.file_reader.ingest(
                    event.unified_msg_origin, file_path, file_name
                )
            except FileParseError as e:
                notice = str(e)
            except Exception as e:
                logger.error(f"[烤箱-文件读取] 处理文件失败: {e}")
            if notice and notify:
                yield event.plain_result(notice)

    @filter.on_llm_request(priority=16)
    async def on_llm_request_files(self, event: AstrMessageEvent, req):
        """检索当前会话已上传文件并注入请求上下文（RAG）。"""
        if not self.file_reader or self._blocked(event):
            return
        rag_cfg = feature_cfg(self.config, FEATURE_FILE_READER).get("rag") or {}
        if not rag_cfg.get("enabled", True):
            return
        if not (req.prompt or "").strip():
            return
        context_text = await self.file_reader.build_context(event.unified_msg_origin, req.prompt)
        if not context_text:
            return
        if str(rag_cfg.get("injection_type", "temp_part")) == "prompt":
            req.prompt = f"{req.prompt}\n\n{context_text}"
        else:
            from astrbot.core.agent.message import TextPart

            req.extra_user_content_parts.append(TextPart(text=context_text).mark_as_temp())
        logger.debug("[烤箱-文件读取] 已注入文件检索结果")

    @filter.llm_tool("file_list")
    async def tool_file_list(self, event: AstrMessageEvent):
        """列出当前会话中已上传、可供读取的文件。当用户询问“有什么文件/刚才发的文件”时调用。"""
        if not self.file_reader:
            return "文件读取功能未启用"
        if not (feature_cfg(self.config, FEATURE_FILE_READER).get("tool") or {}).get(
            "enabled", True
        ):
            return "文件读取的 Tool 功能已关闭"
        return self.file_reader.list_files(event.unified_msg_origin)

    @filter.llm_tool("file_read")
    async def tool_file_read(self, event: AstrMessageEvent, file_name: str):
        """读取指定文件的文本内容。调用前建议先用 file_list 获取准确的文件名。

        Args:
            file_name(string): 要读取的文件名，必须来自 file_list 的列表
        """
        if not self.file_reader:
            return "文件读取功能未启用"
        if not (feature_cfg(self.config, FEATURE_FILE_READER).get("tool") or {}).get(
            "enabled", True
        ):
            return "文件读取的 Tool 功能已关闭"
        return await self.file_reader.read_file_text(
            event.unified_msg_origin, str(file_name or "").strip()
        )

    @filter.llm_tool("file_search")
    async def tool_file_search(self, event: AstrMessageEvent, query: str):
        """在当前会话已上传的文件中进行语义检索，返回与查询最相关的片段。适合大文件或只需部分内容时使用。

        Args:
            query(string): 检索关键词或问题
        """
        if not self.file_reader:
            return "文件读取功能未启用"
        if not (feature_cfg(self.config, FEATURE_FILE_READER).get("tool") or {}).get(
            "enabled", True
        ):
            return "文件读取的 Tool 功能已关闭"
        return await self.file_reader.search_text(event.unified_msg_origin, str(query or ""))

    @filter.command("清除文件")
    async def clear_files(self, event: AstrMessageEvent):
        """清理当前会话的所有已上传文件"""
        if not self.file_reader:
            yield event.plain_result("文件读取功能未启用")
            return
        count = await self.file_reader.clear_session(event.unified_msg_origin)
        yield event.plain_result(f"已清理当前会话的 {count} 个文件")
