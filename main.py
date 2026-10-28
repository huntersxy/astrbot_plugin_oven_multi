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
#   - astrbot_plugin_repetition by FengYing1314 — message repetition
#   - astrbot_plugin_iamthinking (AGPL-3.0) by sssn-tech — thinking emoji reaction
#   - astrbot_plugin_iearning_style (AGPL-3.0) by qa296
#     historical style learning (retired in 1.49.0)
#   - astrbot_plugin_remove_blank_lines (MIT) by Codex — remove blank lines from LLM output
#   - astrbot_plugin_custome_segment_reply (MIT) by LinJohn8 — segment reply engine
#   - astrbot_plugin_file_reader_pro (MIT) by zz6zz666 — file reading, RAG indexing
#     (original license: see core/file_reader/LICENSE)
# Date: 2026-06-23

import asyncio
import contextlib
import json
import re
from pathlib import Path
from sys import maxsize
from traceback import extract_tb

import astrbot.api.message_components as components
from astrbot.api import AstrBotConfig

from astrbot.api import logger  # isort: skip
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import request
from astrbot.core.message.message_event_result import ResultContentType

from .core.balance_checker import BalanceChecker
from .core.bili_auto import BiliAutoTakeover
from .core.bili_dashboard import BiliDashboard
from .core.bili_proxy import BiliProxyManager
from .core.bili_requests import BiliRequestBridge
from .core.bili_wire import BiliWireManager
from .core.bracket_matcher import BracketMatcher
from .core.constants import (
    FEATURE_BILI,
    FEATURE_BRACKET,
    FEATURE_FILE_READER,
    FEATURE_JEV,
    FEATURE_MENTION_PARSER,
    FEATURE_REMOVE_BLANK,
    FEATURE_REPETITION,
    FEATURE_SEGMENT,
    FEATURE_THINKING,
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)
from .core.douyin_live import DouyinLiveMonitor
from .core.file_reader import FileParseError, FileReaderManager
from .core.jev_active import JevActive
from .core.jev_scenes import JevScenes
from .core.mention_parser import ActiveSpeakersTracker, transform_mention_in_chain
from .core.mention_preserve import restore_bot_mention, should_backfill_native
from .core.ovenheart_link import OvenHeartLink
from .core.panel_settings import PanelSettingsManager, StaleSettingsError
from .core.repeater import Repeater
from .core.segment_reply import SegmentReply
from .core.thinking_manager import ThinkingManager

# ── 配置访问辅助 ─────────────────────────────────────────────────────────


def log_settings_error(message: str, exc: Exception) -> None:
    """Keep failure locations without serializing config values into diagnostics."""
    locations = " > ".join(
        f"{Path(frame.filename).name}:{frame.lineno}/{frame.name}"
        for frame in extract_tb(exc.__traceback__)[-6:]
    )
    logger.error(f"[烤箱-面板] {message}: {type(exc).__name__} ({locations})")


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


# 分段回复不支持的发送渠道（与 AstrBot 原生分段回复的排除清单一致）
SEGMENT_SKIP_PLATFORMS = {"qq_official_webhook", "weixin_official_account", "dingtalk"}


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class OvenMultiPlugin(Star):
    """插座的多功能烤箱 - 主插件类"""

    ovenheart_protocol = 1

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.closing = False
        self.config = config if config is not None else AstrBotConfig({})
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)

        # 轻量功能
        self.matcher = BracketMatcher()
        self.repeater = Repeater()
        self.thinking = ThinkingManager()
        self.segment = SegmentReply()

        # Jev 判读场景接入（上下文记录 + 被@判读）
        self.jev_scenes = JevScenes(context=self.context)
        self.ovenheart = OvenHeartLink(self)
        self.jev_active = JevActive(self)
        self.jev_scenes.jev.transport = self.jev_active.client

        # 面板快捷设置（revision 防陈旧 + 校验写回）
        self.panel_settings = PanelSettingsManager()
        self.douyin = DouyinLiveMonitor(self)

        # 上下文压缩（billion-context 代理）：懒人获取链 + 接管/恢复
        self.bili_proxy = BiliProxyManager(
            lambda: feature_cfg(self.config, FEATURE_BILI), self.data_dir
        )
        self.bili_wire = BiliWireManager(lambda: self._host_config(), self.data_dir)
        self.bili_dashboard = BiliDashboard(
            self.bili_proxy, lambda: feature_cfg(self.config, FEATURE_BILI)
        )
        self._bili_task = None
        self.bili_requests = BiliRequestBridge(self)
        self.bili_auto = BiliAutoTakeover(self)

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

        # Register only native Panel APIs.
        for route, handler, method, description in (
            (
                f"/{PLUGIN_NAME}/billion/dashboard",
                self._api_billion_dashboard,
                "GET",
                "Oven Multi read-only compression statistics",
            ),
            (f"/{PLUGIN_NAME}/status", self._api_status, "GET", "Oven Multi feature status"),
            (f"/{PLUGIN_NAME}/balance", self._api_balance, "GET", "Oven Multi provider balances"),
            (f"/{PLUGIN_NAME}/settings", self._api_settings, "GET", "Oven Multi panel settings"),
            (
                f"/{PLUGIN_NAME}/settings/save",
                self._api_settings_save,
                "POST",
                "Oven Multi save panel settings",
            ),
            (
                f"/{PLUGIN_NAME}/billion/status",
                self._api_billion_status,
                "GET",
                "Oven Multi billion-context compression status",
            ),
            (
                f"/{PLUGIN_NAME}/billion/install",
                self._api_billion_install,
                "POST",
                "Oven Multi install or retry billion-context",
            ),
            (
                f"/{PLUGIN_NAME}/billion/wire",
                self._api_billion_wire,
                "POST",
                "Oven Multi wire providers to billion-context",
            ),
            (
                f"/{PLUGIN_NAME}/billion/restore",
                self._api_billion_restore,
                "POST",
                "Oven Multi restore providers from billion-context",
            ),
        ):
            self.context.register_web_api(route, handler, [method], description)

    @filter.command("抖音监控")
    async def douyin_help(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_help", event):
            yield result

    @filter.command("抖音订阅")
    async def douyin_subscribe(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_subscribe", event):
            yield result

    @filter.command("抖音取关")
    async def douyin_unsubscribe(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_unsubscribe", event):
            yield result

    @filter.command("抖音列表")
    async def douyin_list(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_list", event):
            yield result

    @filter.command("抖音检查")
    async def douyin_check(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_check", event):
            yield result

    @filter.command("抖音Cookie")
    async def douyin_set_cookie(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_set_cookie", event):
            yield result

    @filter.command("抖音CookieFile")
    async def douyin_cookie_from_file(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_cookie_from_file", event):
            yield result

    @filter.command("抖音登录")
    async def douyin_login_status(self, event: AstrMessageEvent):
        async for result in self.douyin.dispatch("cmd_login_status", event):
            yield result

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def initialize(self):
        await self.douyin.configure()
        self.bili_requests.install()
        if self.file_reader:
            await self.file_reader.start()
        if feature_enabled(self.config, FEATURE_BILI, False):
            self._bili_task = asyncio.get_event_loop().create_task(self._bili_startup())
        elif self.bili_wire.record_exists():
            # 懒人对称：功能停用但接管仍在 → 自动尝试恢复
            result = self.bili_wire.restore()
            logger.info(f"[烤箱-压缩] 功能停用且存在接管记录，自动恢复：{result['message']}")
        logger.info("[插座烤箱] 启动")

    async def terminate(self):
        self.closing = True
        await self.douyin.stop()
        self.bili_requests.disconnect()
        if self._bili_task is not None:
            self._bili_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._bili_task
            self._bili_task = None
        # Persist restoration, but do not cut off SDK instances awaiting a host reload.
        if self.bili_wire.record_exists():
            result = self.bili_wire.restore()
            logger.info(f"[烤箱-压缩] 卸载恢复：{result['message']}")
        proxy_url = self.bili_proxy.url.rstrip("/") + "/bili/"
        keep_process = self.bili_proxy.owned_process and any(
            url.startswith(proxy_url) for url in self._chat_provider_view().values()
        )
        await self.bili_proxy.stop(keep_process=keep_process)
        if keep_process:
            logger.warning(
                "[烤箱-压缩] 运行中的 Provider 尚在使用代理，保留进程；请重启 AstrBot 完成恢复。"
            )
        if self.file_reader:
            await self.file_reader.stop()
        await self.jev_scenes.close()
        await self.jev_active.close()
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
        jev = feature_cfg(self.config, FEATURE_JEV)
        douyin = self.douyin.status()
        files = feature_cfg(self.config, FEATURE_FILE_READER)
        blank_limit = cfg_value(self.config, FEATURE_REMOVE_BLANK, "max_consecutive_newlines", 1)
        seg = feature_cfg(self.config, FEATURE_SEGMENT)
        seg_detail = "在插件配置中开启"
        if feature_enabled(self.config, FEATURE_SEGMENT, False):
            min_length, max_length = self.segment.length_range(seg)
            delay_mode = seg.get("delay_type", "random")
            seg_detail = f"单段 {min_length}-{max_length} 字，间隔「{delay_mode}」"
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
            (
                FEATURE_SEGMENT,
                "分段回复",
                feature_enabled(self.config, FEATURE_SEGMENT, False),
                seg_detail,
            ),
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
            (
                FEATURE_BILI,
                "上下文压缩",
                feature_enabled(self.config, FEATURE_BILI, False),
                self._bili_status_detail(),
            ),
            (
                "douyin",
                "抖音直播通知",
                douyin["enabled"],
                "独立抖音插件仍启用，内置监控暂停以避免重复通知"
                if douyin["legacy_active"]
                else f"{douyin['sessions']} 个会话 / {douyin['rooms']} 个账号，"
                f"每 {douyin['interval']} 秒检查；订阅与取关沿用抖音命令",
            ),
        ]
        features = [
            {"id": key, "name": name, "enabled": bool(enabled), "detail": detail}
            for key, name, enabled, detail in rows
        ]
        if self.ovenheart.core() is not None:
            for feature in features:
                if feature["id"] == FEATURE_MENTION_PARSER:
                    feature.update(
                        configured_enabled=feature["enabled"],
                        enabled=False,
                        managed_by="ovenheart",
                        detail="烤箱之心已接管；独立运行时恢复原设置",
                    )
                if feature["id"] == FEATURE_JEV:
                    feature["detail"] = "向烤箱之心提供目标与上下文一致的结构化判读"
        return features

    def ovenheart_jev_signature(self):
        return self.ovenheart.signature()

    async def analyze_for_ovenheart(self, event, payload):
        return await self.ovenheart.analyze(event, payload)

    async def _api_status(self):
        """Return fast, credential-free Panel state without querying providers.

        Returns:
            Native Panel response containing configuration state and actionable notices.
        """
        jev = feature_cfg(self.config, FEATURE_JEV)
        notices = []
        if jev.get("enabled") and not str(jev.get("api_key") or "").strip():
            notices.append("Jev 已开启但未配置 API Key，请在插件配置中填写后使用判读。")
        if feature_enabled(self.config, FEATURE_FILE_READER, False) and not self.file_reader:
            notices.append("文件读取已配置开启，但尚未初始化成功，请检查 AstrBot 日志。")
        if self.bili_wire.record_exists():
            notices.extend(self._bili_doctor())
        douyin = self.douyin.status()
        if douyin["enabled"] and douyin["last_error"]:
            notices.append(f"抖音通知：{douyin['last_error']}；保留上次状态，等待后续轮询。")
        return {
            "success": True,
            "data": {
                "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
                "features": self._feature_status(),
                "notices": notices,
                "jev_usage": self.jev_active.client.status(),
                "douyin": douyin,
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

    async def _api_settings(self):
        """Return editable panel settings and the current form revision.

        Returns:
            Native Panel response with grouped fields and stale-guard revision.
        """
        data = self.panel_settings.describe(self.config)
        if self.ovenheart.core() is not None:
            for group in data["groups"]:
                for field in group["fields"]:
                    if field["key"] == "mention_parser.enabled":
                        field.update(
                            readonly=True,
                            effective_value=False,
                            hint="已由烤箱之心接管，当前自动关闭；保存值保留，独立运行时恢复。",
                        )
                    if field["key"] == "jev.active_reply_enabled":
                        field.update(
                            readonly=True,
                            effective_value=False,
                            hint="烤箱之心独占主动回复；本插件的意图与情绪判断仍可独立开启。",
                        )
        return {"success": True, "data": data}

    async def _api_settings_save(self):
        """Validate and persist panel settings submitted from the Panel.

        Returns:
            Native Panel response with the new revision, or an actionable error.
            ``stale`` marks a rejected outdated form (the client offers a reload).
        """
        try:
            raw = await request.body()
            if len(raw) > 65536:
                raise ValueError("请求内容超过 64 KB，请减少修改项。")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("请求不是有效的 JSON。") from exc
            if not isinstance(body, dict) or not isinstance(body.get("values"), dict):
                raise ValueError("请提交要保存的设置。")
            if self.bili_proxy.owned_process or self.bili_wire.record_exists():
                current = feature_cfg(self.config, FEATURE_BILI)
                for field in ("host", "port", "mode", "executable", "session_header"):
                    key = f"bili.{field}"
                    if key in body["values"] and body["values"][key] != current.get(field):
                        raise ValueError(
                            "代理使用期间不能更换地址或运行方式；请恢复并重启 AstrBot 后修改。"
                        )
            previous_bili = dict(feature_cfg(self.config, FEATURE_BILI))
            previous_douyin = dict(feature_cfg(self.config, "douyin"))
            revision = await self.panel_settings.save(
                self.config, body["values"], body.get("revision")
            )
            logger.info(f"[烤箱-面板] 已保存 {len(body['values'])} 项设置")
            if previous_douyin != feature_cfg(self.config, "douyin"):
                await self.douyin.configure()
            if previous_bili != feature_cfg(self.config, FEATURE_BILI):
                if self._bili_task is not None:
                    self._bili_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._bili_task
                self._bili_task = asyncio.create_task(self._bili_revaluate())
            return {
                "success": True,
                "data": {
                    "revision": revision,
                    "message": "设置已保存并立即生效。",
                },
            }
        except StaleSettingsError as exc:
            return {"success": False, "stale": True, "message": str(exc)}
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except PermissionError as exc:
            log_settings_error("宿主插件配置文件写入被拒绝", exc)
            return {
                "success": False,
                "message": "配置文件写入被拒绝，请检查 AstrBot 插件配置目录的写入权限。",
            }
        except OSError as exc:
            log_settings_error("宿主插件配置写盘失败", exc)
            return {
                "success": False,
                "message": "配置写盘失败，请检查磁盘空间与插件配置目录，再重试。",
            }
        except Exception as exc:
            log_settings_error("设置保存流程异常", exc)
            return {
                "success": False,
                "message": "设置保存流程发生异常，请刷新确认配置，并查看插件日志中的具体原因。",
            }

    # ── 上下文压缩（billion-context）─────────────────────────────────────

    def _host_config(self):
        """默认 profile 的宿主配置（AstrBotConfig）。"""
        return self.context.get_config()

    def _chat_provider_view(self) -> dict[str, str]:
        """运行中对话 provider 视图 {id: api_base}；异常环境（离线测试）返回空。"""
        try:
            insts = getattr(self.context.provider_manager, "provider_insts", None) or []
            view = {}
            for provider in insts:
                pcfg = getattr(provider, "provider_config", None)
                if isinstance(pcfg, dict):
                    pid = str(pcfg.get("id") or "")
                    if pid:
                        client = getattr(provider, "client", None)
                        view[pid] = str(
                            getattr(client, "base_url", None) or pcfg.get("api_base") or ""
                        )
            return view
        except Exception:  # noqa: BLE001
            return {}

    async def _using_chat_provider_id(self, umo: str | None = None) -> str | None:
        """当前生效的对话 provider ID（umo 存在时按会话解析，同 AstrBot 原生规则）。"""
        with contextlib.suppress(Exception):
            from astrbot.core.provider.entities import ProviderType

            provider = await self.context.provider_manager.get_using_provider_async(
                ProviderType.CHAT_COMPLETION, umo
            )
            pcfg = getattr(provider, "provider_config", None)
            if isinstance(pcfg, dict):
                return str(pcfg.get("id") or "") or None
        return None

    def _fallback_provider_ids(self) -> list[str]:
        try:
            model = ((self._host_config().get("agent_runner") or {}).get("config") or {}).get(
                "model"
            )
            return [str(x) for x in (model or {}).get("fallback_provider_ids") or []]
        except Exception:  # noqa: BLE001
            return []

    def _bili_endpoint(self) -> tuple[str, int]:
        cfg = feature_cfg(self.config, FEATURE_BILI)
        host = str(cfg.get("host") or "127.0.0.1")
        port = int(cfg.get("port") or 8787)
        return host, port

    def _bili_doctor(self) -> list[str]:
        active_id = self._sync_using_provider_id()
        return self.bili_wire.doctor(
            proxy_healthy=self.bili_proxy.healthy,
            runtime_client_api=self._chat_provider_view(),
            fallback_ids=self._fallback_provider_ids(),
            active_ids=[active_id] if active_id else [],
            auto_follow=self.bili_auto.enabled(),
        )

    def _bili_status_detail(self) -> str:
        if not feature_enabled(self.config, FEATURE_BILI, False):
            return "未启用"
        parts = [self.bili_proxy.status()["state"]]
        summary = self.bili_wire.record_summary()
        if summary:
            parts.append(f"已接管 {'、'.join(summary['providers'])}")
        if self.bili_auto.paused:
            parts.append("自动跟随已暂停，重新保存压缩设置或执行接管后恢复")
        if self.bili_proxy.session_header_state != "ok":
            parts.append(f"会话标识{self.bili_proxy.session_header_state}")
        return "，".join(parts)

    def _bili_header_warning(self) -> str:
        """Explain why an unverified proxy cannot be wired."""
        state = self.bili_proxy.session_header_state
        if state == "ok":
            return ""
        detail = self.bili_proxy.session_header_detail or "会话标识未确认"
        return f"⚠ 会话标识{state}：{detail} 校验通过后才可接管。"

    async def _bili_startup(self, *, install: bool = False) -> None:
        """懒人链：确保代理可用 → （无接管记录时）检测目标并自动接管。"""
        cfg = feature_cfg(self.config, FEATURE_BILI)
        if not cfg.get("enabled", False):
            return
        status = await self.bili_proxy.ensure_started(install=install)
        logger.info(f"[烤箱-压缩] 代理状态：{status['state']} {status['detail']}".rstrip())
        if not status["healthy"]:
            return
        if status.get("session_header_state") != "ok":
            # 会话标识未确认（不匹配或未知）：阻断自动接管，只留告警
            logger.warning(
                "[烤箱-压缩] 懒人接管未执行："
                f"{status.get('session_header_detail') or '会话标识未确认'}"
            )
            return
        if cfg.get("auto_follow", True):
            await self.bili_auto.follow(initial=True)
            return
        if self.bili_wire.record_exists():
            return
        host, port = self._bili_endpoint()
        chat_ids = list(self._chat_provider_view().keys())
        using_id = self._sync_using_provider_id()
        explicit = [str(x) for x in (cfg.get("provider_ids") or [])]
        targets, message = self.bili_wire.detect_targets(explicit, chat_ids, using_id)
        if not targets:
            if message:
                logger.warning(f"[烤箱-压缩] 懒人接管未执行：{message}")
            return
        result = self.bili_wire.wire(
            targets, host, port, health_check=lambda: self.bili_proxy.healthy
        )
        log = logger.info if result["ok"] else logger.warning
        log(f"[烤箱-压缩] {result['message']}")

    async def _bili_revaluate(self) -> None:
        """面板保存 bili 节后的重评估：关闭→自动恢复；开启→懒人链。

        停用不立即停代理：运行中的 SDK 客户端与管线压缩配置要等重载/重启
        才切回原地址，卸载时也会检查实际 SDK 是否仍依赖代理再决定是否停进程。
        """
        self.bili_auto.paused = False
        if not feature_enabled(self.config, FEATURE_BILI, False):
            if self.bili_wire.record_exists():
                result = self.bili_wire.restore()
                logger.info(f"[烤箱-压缩] 停用自动恢复：{result['message']}")
            logger.info(
                "[烤箱-压缩] 已停用；代理保留运行直至重载/重启（重载后原配置生效），"
                "插件卸载时停止。"
            )
            return
        await self._bili_startup()

    async def _bili_targets_or_message(self, umo: str | None = None) -> tuple[list[str], str]:
        """命令/面板共用：解析接管目标（显式列表或懒人检测；带 umo 时按会话解析）。"""
        cfg = feature_cfg(self.config, FEATURE_BILI)
        explicit = [str(x) for x in (cfg.get("provider_ids") or [])]
        using_id = (
            await self._using_chat_provider_id(umo) if umo else self._sync_using_provider_id()
        )
        return self.bili_wire.detect_targets(
            explicit, list(self._chat_provider_view().keys()), using_id
        )

    def _sync_using_provider_id(self) -> str | None:
        """同步版当前 provider 解析（优先原生解析结果，回退首实例）。"""
        view = self._chat_provider_view()
        if not view:
            return None
        with contextlib.suppress(Exception):
            model = ((self._host_config().get("agent_runner") or {}).get("config") or {}).get(
                "model"
            )
            pid = str((model or {}).get("provider_id") or "")
            if pid in view:
                return pid
        return next(iter(view)) if len(view) == 1 else None

    async def _api_billion_dashboard(self):
        try:
            page = int(request.query.get("page", "1"))
        except (ValueError, TypeError):
            return {"success": False, "message": "页码必须是整数。"}
        data = await self.bili_dashboard.get(
            page=page,
            query=request.query.get("q", ""),
            session_id=request.query.get("session", ""),
        )
        if data.get("available") and "sessions" in data:
            data = data | {"sessions": [dict(row) for row in data["sessions"]]}
            try:
                for row in data["sessions"]:
                    conversation = await self.context.conversation_manager.db.get_conversation_by_id(
                        row["id"]
                    )
                    if conversation:
                        row["astrbot_session"] = conversation.user_id
                        row["astrbot_title"] = conversation.title or ""
            except Exception as exc:
                logger.warning(f"[烤箱-压缩] 无法读取 AstrBot 会话归属: {exc}")
                data["session_lookup_failed"] = True
        return {"success": True, "data": data}

    async def _api_billion_status(self):
        """压缩代理与接管状态（含巡检告警），供状态卡展示。"""
        cfg = feature_cfg(self.config, FEATURE_BILI)
        record = self.bili_wire.record_summary()
        doctor = self._bili_doctor() if self.bili_wire.record_exists() else []
        if self.bili_auto.message:
            doctor.append(self.bili_auto.message)
        if cfg.get("enabled", False):
            warning = self._bili_header_warning()
            if warning:
                doctor.insert(0, warning)
        return {
            "success": True,
            "data": {
                "enabled": bool(cfg.get("enabled", False)),
                "busy": self._bili_task is not None and not self._bili_task.done(),
                "proxy": self.bili_proxy.status(),
                "wire": record,
                "doctor": doctor,
                "requests": self.bili_requests.status(),
                "auto_follow": feature_cfg(self.config, FEATURE_BILI).get("auto_follow", True),
                "auto_follow_paused": self.bili_auto.paused,
            },
        }

    async def _api_billion_install(self):
        """Queue an explicit install/retry without holding the Panel request open."""
        cfg = feature_cfg(self.config, FEATURE_BILI)
        if not cfg.get("enabled", False):
            return {"success": False, "message": "请先启用上下文压缩并保存，再安装／重试。"}
        if cfg.get("mode", "managed") != "managed":
            return {
                "success": False,
                "message": "外部模式请在代理所在环境安装；托管模式支持快捷安装。",
            }
        if self._bili_task is not None and not self._bili_task.done():
            return {"success": True, "data": {"message": "安装／启动仍在进行，请查看状态。"}}
        self._bili_task = asyncio.create_task(self._bili_startup(install=True))
        return {
            "success": True,
            "data": {"message": "已开始安装／重试，完成后会自动启动并尝试接管。"},
        }

    async def _api_billion_wire(self):
        """接管 provider（需 body.confirm 二次确认；未确认时返回预览）。"""
        try:
            raw = await request.body()
            body = json.loads(raw) if len(raw) <= 65536 else {}
            if not isinstance(body, dict):
                body = {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        confirmed = body.get("confirm") is True
        if confirmed:
            healthy, _ = await self.bili_proxy.probe_health()
            await self.bili_proxy.verify_session_header()
            if not healthy:
                return {"success": False, "message": "代理健康检查未通过，已拒绝接管。"}
            if self.bili_proxy.session_header_state != "ok":
                return {"success": False, "message": "会话标识未验证，已拒绝接管。"}
        targets, message = await self._bili_targets_or_message()
        if not targets:
            return {"success": False, "message": message or "未找到可接管的对话 provider。"}
        host, port = self._bili_endpoint()
        if not confirmed:
            data = {
                "preview": self.bili_wire.preview(targets, host, port),
                "message": "预览已生成，请带 confirm: true 再次提交以执行接管。",
            }
            warning = self._bili_header_warning()
            if warning:
                data["warning"] = warning
            return {"success": True, "data": data}
        result = self.bili_wire.wire(
            targets, host, port, health_check=lambda: self.bili_proxy.healthy
        )
        if not result["ok"]:
            return {"success": False, "message": result["message"]}
        self.bili_auto.paused = False
        await self.bili_auto.follow(initial=True)
        return {"success": True, "data": {"message": result["message"]}}

    async def _api_billion_restore(self):
        """恢复被接管的配置（需 body.confirm 二次确认）。"""
        try:
            raw = await request.body()
            body = json.loads(raw) if len(raw) <= 65536 else {}
            if not isinstance(body, dict):
                body = {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        record = self.bili_wire.record_summary()
        if not record:
            return {"success": False, "message": "没有接管记录，无需恢复。"}
        if body.get("confirm") is not True:
            return {
                "success": True,
                "data": {"wire": record, "message": "请带 confirm: true 再次提交以执行恢复。"},
            }
        result = self.bili_wire.restore()
        if result["ok"]:
            self.bili_auto.paused = True
        return {"success": bool(result["ok"]), "data": {"message": result["message"]}}

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

    # ── Handler：上下文压缩（billion-context）───────────────────────────

    @filter.command("烤箱压缩状态")
    async def bili_status(self, event: AstrMessageEvent):
        cfg = feature_cfg(self.config, FEATURE_BILI)
        lines = ["🗜 上下文压缩 (billion-context)", ""]
        if not cfg.get("enabled", False) and not self.bili_wire.record_exists():
            lines.append("未启用。开启「状态总览 → 设置 → 上下文压缩」即进入懒人模式。")
            yield event.plain_result("\n".join(lines))
            return
        status = self.bili_proxy.status()
        lines.append(f"代理：{status['state']}（{status['mode']} · {status['url']}）")
        if status["detail"]:
            lines.append(f"  {status['detail']}")
        summary = self.bili_wire.record_summary()
        if summary:
            lines.append(
                f"接管：{'、'.join(summary['providers'])}"
                f"（{summary['wired_at']} → {summary['proxy_url']}）"
            )
        else:
            lines.append("接管：未接管任何 provider")
        doctor = self._bili_doctor()
        if doctor:
            lines.append("")
            lines.extend(f"⚠ {notice}" for notice in doctor)
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("烤箱压缩接管")
    async def bili_wire_cmd(self, event: AstrMessageEvent):
        confirmed = "确认" in (event.message_str or "").split()
        if confirmed:
            healthy, _ = await self.bili_proxy.probe_health()
            await self.bili_proxy.verify_session_header()
            if not healthy:
                yield event.plain_result("代理健康检查未通过，已拒绝接管。")
                return
            if self.bili_proxy.session_header_state != "ok":
                yield event.plain_result("会话标识未验证，已拒绝接管。")
                return
        targets, message = await self._bili_targets_or_message(event.unified_msg_origin)
        if not targets:
            yield event.plain_result(f"未执行接管：{message}")
            return
        host, port = self._bili_endpoint()
        if not confirmed:
            preview = self.bili_wire.preview(targets, host, port)
            lines = ["接管预览（未做任何修改）："]
            lines.extend(
                f"  · {row['target']}.{row['field']}: {row['old']!r} → {row['new']!r}"
                for row in preview["changes"]
            )
            lines.extend(f"  · 跳过 {item['id']}：{item['reason']}" for item in preview["skipped"])
            warning = self._bili_header_warning()
            if warning:
                lines.append(warning)
            lines.append("")
            lines.append("确认无误请回复「烤箱压缩接管 确认」。")
            yield event.plain_result("\n".join(lines))
            return
        result = self.bili_wire.wire(
            targets, host, port, health_check=lambda: self.bili_proxy.healthy
        )
        if result["ok"]:
            self.bili_auto.paused = False
            await self.bili_auto.follow(initial=True)
        yield event.plain_result(result["message"])

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("烤箱压缩恢复")
    async def bili_restore_cmd(self, event: AstrMessageEvent):
        confirmed = "确认" in (event.message_str or "").split()
        summary = self.bili_wire.record_summary()
        if not summary:
            yield event.plain_result("没有接管记录，无需恢复。")
            return
        if not confirmed:
            yield event.plain_result(
                f"当前接管：{'、'.join(summary['providers'])}（{summary['wired_at']}）。\n"
                "恢复将还原 api_base 与压缩配置并删除记录，确认请回复「烤箱压缩恢复 确认」。"
            )
            return
        result = self.bili_wire.restore()
        if result["ok"]:
            self.bili_auto.paused = True
        yield event.plain_result(result["message"])

    # ── Handler：群消息预处理（最高优先级）────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=maxsize)
    async def on_early_group_message(self, event: AstrMessageEvent):
        """抢在 AstrBot 裸@拦截（handle_empty_mention，优先级 maxsize-1）之前：

        1. 把 aiocqhttp 适配器丢弃的首个「@机器人」补回 message_str；
        2. 为「纯@消息」补齐原生「群聊消息记录注入上下文」记录；
        3. 记录 Jev 判读上下文（含@消息）。
        """
        blocked = self._blocked(event)
        self.jev_active.notice(event, await self.ovenheart.owned(event))

        # debug_mode 开启时，每条群消息打一行待命状态：开关一开、下一条消息即可
        # 看到当前 enabled/api_key/场景开关/唤醒状态，静默不再是黑盒
        jev_cfg = feature_cfg(self.config, FEATURE_JEV)
        if jev_cfg.get("debug_mode"):
            api_key_ok = bool(str(jev_cfg.get("api_key") or "").strip())
            logger.info(
                f"[Jev-DEBUG] 待命 | origin={event.unified_msg_origin} "
                f"blocked={blocked} enabled={jev_cfg.get('enabled', False)} "
                f"api_key={'已配置' if api_key_ok else '未配置'} "
                f"被@注入={jev_cfg.get('inject_on_mention', True)} | "
                f"wake={event.is_at_or_wake_command}"
            )

        if blocked:
            return

        if await self.ovenheart.owned(event):
            return  # The core owns mentions and the single contextual snapshot.

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
        self.jev_scenes.record_history(event, self.config)

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
        if feature_enabled(self.config, FEATURE_MENTION_PARSER) and not await self.ovenheart.owned(
            event
        ):
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

    # ── Handler：分段回复 ────────────────────────────────────────────────

    @filter.on_decorating_result(priority=-200)
    async def segment_reply_decorate(self, event: AstrMessageEvent):
        """在所有装饰钩子之后把长回复切成多段。

        首段替换进原链，随原生装饰（@回复/引用/前缀）由 RespondStage 发送；
        其余各段挂到 extra，由 ``send_pending_segments`` 在消息发出后带延迟
        逐条补发，模拟真人打字节奏。分段失败或只有一段时保持原消息不变。
        """
        if not feature_enabled(self.config, FEATURE_SEGMENT, False):
            return
        result = event.get_result()
        if not result or not result.chain:
            return
        if event.get_platform_name() in SEGMENT_SKIP_PLATFORMS:
            return
        if result.result_content_type in (
            ResultContentType.STREAMING_RESULT,
            ResultContentType.STREAMING_FINISH,
        ):
            return
        # 非 LLM 输出（插件指令、命令返回等）默认不分段
        if (
            not bool(cfg_value(self.config, FEATURE_SEGMENT, "process_non_llm", False))
            and not result.is_llm_result()
        ):
            return
        # Keep native quote/@ on the first segment and trailing stickers after
        # the last. Never reorder interleaved media, forwards or tool results.
        chain = list(result.chain)
        prefix, suffix = [], []
        while chain and isinstance(chain[0], (components.At, components.Reply)):
            prefix.append(chain.pop(0))
        while chain and isinstance(chain[-1], components.Image):
            suffix.insert(0, chain.pop())
        if not chain or not all(isinstance(comp, components.Plain) for comp in chain):
            return
        text = "".join(comp.text for comp in chain).strip()
        if not text:
            return
        seg_cfg = feature_cfg(self.config, FEATURE_SEGMENT)
        if self.segment.should_skip(text, seg_cfg):
            return
        try:
            segments = self.segment.segment(text, seg_cfg)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[烤箱-分段] 分段失败，按原文发送：{e}")
            return
        if len(segments) <= 1:
            return
        event.set_extra("oven_segment_pending", segments)
        event.set_extra("oven_segment_suffix", suffix)
        result.chain[:] = prefix + [components.Plain(segments[0])]
        logger.info(f"[烤箱-分段] 已拆分为 {len(segments)} 段，首段随原生装饰发送")

    @filter.after_message_sent()
    async def send_pending_segments(self, event: AstrMessageEvent):
        """逐段补发剩余分段。首段已由 RespondStage 发出，这里只发后续各段。"""
        segments = event.get_extra("oven_segment_pending")
        if not segments:
            return
        event.set_extra("oven_segment_pending", None)
        suffix = event.get_extra("oven_segment_suffix") or []
        event.set_extra("oven_segment_suffix", None)
        seg_cfg = feature_cfg(self.config, FEATURE_SEGMENT)
        prev = segments[0]
        for index, seg in enumerate(segments[1:], 1):
            try:
                await asyncio.sleep(self.segment.delay(prev, seg, seg_cfg))
                outgoing = event.plain_result(seg)
                if index == len(segments) - 1:
                    outgoing.chain.extend(suffix)
                await event.send(outgoing)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[烤箱-分段] 补发失败，停止后续分段：{e}")
                return
            prev = seg

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
        if not feature_enabled(self.config, FEATURE_MENTION_PARSER) or await self.ovenheart.owned(
            event
        ):
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

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-40000)
    async def jev_autonomous_reply(self, event: AstrMessageEvent):
        await self.jev_active.consider(event)

    @filter.on_llm_request(priority=17)
    async def on_llm_request(self, event: AstrMessageEvent, req):
        from astrbot.core.agent.message import TextPart

        await self.bili_auto.follow(event)
        self.bili_requests.install()
        owned = await self.ovenheart.owned(event)
        await self.bili_requests.native_tools.prepare(event, req)

        # 被@/唤醒触发的一次 Jev 判读注入：重点是意向与情绪（临时内容，不持久化）
        if (
            not owned
            and feature_enabled(self.config, FEATURE_JEV, False)
            and not self._blocked(event)
        ):
            block = await self.jev_scenes.mention_reading(event, self.config)
            if block:
                req.extra_user_content_parts.append(TextPart(text=str(block)).mark_as_temp())

        # 活跃发言人列表注入（@ 功能）
        if not owned and feature_enabled(self.config, FEATURE_MENTION_PARSER):
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
