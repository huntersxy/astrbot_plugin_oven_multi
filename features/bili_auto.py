"""Follow actual chat selections before native runner reset, without closing SDKs."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from openai import AsyncOpenAI

from astrbot.api import logger

from .bili_wire import INFLATED_MAX_CONTEXT_TOKENS


class BiliAutoTakeover:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()
        self.message = ""
        self.paused = False

    def enabled(self):
        cfg = self.plugin.config.get("bili") or {}
        return (
            not self.plugin.closing
            and not self.paused
            and cfg.get("enabled", False)
            and cfg.get("auto_follow", True)
        )

    def ready(self):
        proxy = self.plugin.bili_proxy
        return self.enabled() and proxy.healthy and proxy.session_header_state == "ok"

    @staticmethod
    def supported(provider):
        sdk = getattr(provider, "client", None)
        return (
            isinstance(sdk, AsyncOpenAI)
            and not sdk.is_closed()
            and urlsplit(str(sdk.base_url)).scheme in ("http", "https")
        )

    def sync_runtime(self, provider):
        """Use the public SDK URL setter; already-built/in-flight Requests stay intact.

        Original addresses live in the recovery record. On disable/unload the
        existing restore lifecycle still keeps the proxy alive for live SDKs.
        """
        try:
            self._sync_runtime(provider)
        except Exception as exc:
            self.warn(f"运行中模型自动接管失败（{type(exc).__name__}），保留原请求流程。")

    def _sync_runtime(self, provider):
        if not self.ready() or not self.supported(provider):
            return
        config = getattr(provider, "provider_config", {})
        pid = config.get("id")
        record = self.plugin.bili_wire.load_record() or {}
        fields = record.get("providers", {}).get(pid)
        if not fields:
            return
        _, saved = self.plugin.bili_wire._find_entry(
            self.plugin.bili_wire._provider_entries(self.plugin._host_config()), pid
        )
        if saved is None or any(saved.get(key) != spec["new"] for key, spec in fields.items()):
            return
        target = fields["api_base"]["new"]
        current = str(provider.client.base_url).rstrip("/")
        if current not in {str(fields["api_base"]["old"]).rstrip("/"), target.rstrip("/")}:
            self.warn("运行中模型地址与接管记录不符，保留当前地址，请核对 Provider 设置。")
            return
        if (
            current == target.rstrip("/")
            and config.get("api_base") == target
            and config.get("max_context_tokens") == INFLATED_MAX_CONTEXT_TOKENS
        ):
            return
        provider.client.base_url = target
        provider.provider_config = config | {
            "api_base": target,
            "max_context_tokens": INFLATED_MAX_CONTEXT_TOKENS,
        }

    def warn(self, message):
        if message != self.message:
            logger.warning("[烤箱-压缩] %s", message)
        self.message = message

    async def follow(self, event=None, *, initial=False):
        try:
            await self._follow(event, initial=initial)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.warn(f"自动接管异常（{type(exc).__name__}），保留原请求流程，请核对压缩状态。")

    async def _follow(self, event=None, *, initial=False):
        if not self.ready():
            return
        async with self.lock:
            if not self.ready():
                return
            umo = getattr(event, "unified_msg_origin", None)
            primary = event.get_extra("selected_provider") if event is not None else None
            if not isinstance(primary, str) or not primary:
                primary = await self.plugin._using_chat_provider_id(umo)
            if not primary:
                primary = self.plugin._sync_using_provider_id()
            if not primary:
                self.warn("无法确认实际聊天模型，本轮未新增接管。")
                return
            effective = self.plugin.context.get_config(umo) if umo else self.plugin._host_config()
            fallback = (
                (((effective or {}).get("agent_runner") or {}).get("config") or {})
                .get("model", {})
                .get("fallback_provider_ids", [])
            )
            fallback = fallback if isinstance(fallback, list) else []
            seed = (self.plugin.config.get("bili") or {}).get("provider_ids", []) if initial else []
            ids = list(dict.fromkeys([primary, *fallback, *seed]))
            manager = getattr(self.plugin.context, "provider_manager", None)
            instances = {
                getattr(p, "provider_config", {}).get("id"): p
                for p in (getattr(manager, "provider_insts", None) or [])
            }
            selected = [pid for pid in ids if pid in instances and self.supported(instances[pid])]
            unsupported = [pid for pid in ids if pid not in selected]
            if not selected:
                self.warn("当前聊天模型不支持自动接管；请使用 OpenAI 兼容聊天接口。")
                return
            record = self.plugin.bili_wire.load_record() or {}
            if any(pid not in record.get("providers", {}) for pid in selected):
                healthy, _ = await self.plugin.bili_proxy.probe_health()
                if not healthy:
                    self.warn("代理健康检查未通过，本轮未新增接管。")
                    return
                await self.plugin.bili_proxy.verify_session_header()
            if not self.ready():
                return
            host, port = self.plugin._bili_endpoint()
            result = self.plugin.bili_wire.extend(selected, host, port, self.ready)
            if not result["ok"]:
                self.warn(result["message"])
                return
            self.message = ""
            for pid in selected:
                self.sync_runtime(instances[pid])
            self.plugin.bili_requests.install()
            if result.get("added"):
                logger.info("[烤箱-压缩] 已跟随聊天模型，新增接管 %s 项", len(result["added"]))
            if unsupported:
                self.warn("部分聊天或备用模型不支持自动接管，保留其原始路由。")
