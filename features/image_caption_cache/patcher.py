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
# Derived from astrbot_plugin_image_caption_cache (AGPL-3.0) by Florance.
# Repository: https://github.com/FloranceYeh/astrbot_plugin_image_caption_cache
"""
AstrBot 核心函数运行时补丁：为图片转述流程插入缓存层。

通过 monkey-patch ``astrbot.core.astr_main_agent`` 中的
``_request_img_caption`` 和 ``_process_quote_message`` 函数，
在调用视觉模型之前检查缓存，命中则直接返回缓存结果。
"""

from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import Callable
from types import ModuleType
from typing import Any

from astrbot.api import logger

from .cache import ImageCaptionCache

# (target, attr_name, original_attr, replacement)
PatchRecord = tuple[ModuleType | type, str, str, Any]


class ImageCaptionCachePatcher:
    """管理 AstrBot 核心函数的运行时补丁。

    负责在插件加载时替换目标函数，并在插件卸载时恢复原始函数。
    """

    def __init__(
        self,
        *,
        cache: ImageCaptionCache,
        ttl_resolver: Callable[[object | None], int],
        logger: Any = logger,
    ) -> None:
        self._cache = cache
        self._ttl_resolver = ttl_resolver
        self._logger = logger
        self._patches: list[PatchRecord] = []

    # -- 公开接口 ------------------------------------------------------------

    def apply(
        self,
        *,
        patch_main_agent: bool = True,
        patch_quoted_message: bool = True,
    ) -> list[str]:
        """应用补丁。

        返回成功打补丁的目标名称列表。
        """
        applied: list[str] = []
        if patch_main_agent and self._patch_main_agent_request():
            applied.append("main_agent")
        if patch_quoted_message and self._patch_quoted_message():
            applied.append("quoted_message")
        return applied

    def restore(self) -> None:
        """恢复所有被补丁覆盖的原始函数。"""
        for target, name, original_attr, replacement in reversed(self._patches):
            if (
                                getattr(target, name, None) is replacement
                and hasattr(target, original_attr)
            ):
                setattr(target, name, getattr(target, original_attr))
                delattr(target, original_attr)
        self._patches.clear()

    # -- 补丁：主对话图片转述 ------------------------------------------------

    def _patch_main_agent_request(self) -> bool:
        ama = self._import_module("astrbot.core.astr_main_agent")
        if ama is None or not hasattr(ama, "_request_img_caption"):
            return False

        original = getattr(ama, "_request_img_caption")
        if not self._signature_has_prefix(
            original,
            ["provider_id", "cfg", "image_urls", "plugin_context"],
        ):
            self._logger.warning(
                "Skip image caption cache patch: "
                "unsupported _request_img_caption signature."
            )
            return False

        cache_ref = self._cache
        ttl_resolver = self._ttl_resolver
        log = self._logger

        async def cached_request_img_caption(
            provider_id: str,
            cfg: dict,
            image_urls: list[str],
            plugin_context: Any,
            prompt: str | None = None,
        ) -> str:
            provider = plugin_context.get_provider_by_id(provider_id)
            provider_cls = getattr(ama, "Provider", None)
            if provider is None:
                raise ValueError(
                    "Cannot get image caption because provider "
                    f"`{provider_id}` does not exist."
                )
            if provider_cls is not None and not isinstance(provider, provider_cls):
                raise ValueError(
                    "Cannot get image caption because provider "
                    f"`{provider_id}` is not a valid Provider, "
                    f"it is {type(provider)}."
                )

            caption_prompt = prompt or (cfg or {}).get(
                "image_caption_prompt",
                "Please describe the image.",
            )

            cache_provider_id = _resolve_provider_cache_identity(
                provider,
                configured_provider_id=provider_id,
            )
            ttl = ttl_resolver(cfg)

            async def caption_factory() -> str:
                response = await provider.text_chat(
                    prompt=caption_prompt,
                    image_urls=image_urls,
                )
                return (
                    getattr(response, "completion_text", "")
                    if response
                    else ""
                )

            return await cache_ref.get_or_create(
                provider_id=cache_provider_id,
                prompt=caption_prompt,
                image_urls=list(image_urls),
                ttl_seconds=ttl,
                caption_factory=caption_factory,
            )

        self._replace(
            ama,
            "_request_img_caption",
            cached_request_img_caption,
            "__image_caption_cache_original_request_img_caption",
        )
        return True

    # -- 补丁：引用消息图片转述 ------------------------------------------------

    def _patch_quoted_message(self) -> bool:
        ama = self._import_module("astrbot.core.astr_main_agent")
        if ama is None or not hasattr(ama, "_process_quote_message"):
            return False

        original = getattr(ama, "_process_quote_message")
        if not self._signature_has_prefix(
            original,
            ["event", "req", "img_cap_prov_id", "plugin_context"],
        ):
            self._logger.warning(
                "Skip quoted image caption cache patch: "
                "unsupported _process_quote_message signature."
            )
            return False

        cache_ref = self._cache
        ttl_resolver = self._ttl_resolver
        log = self._logger

        async def cached_process_quote_message(
            event: Any,
            req: Any,
            img_cap_prov_id: str,
            plugin_context: Any,
            quoted_message_settings: Any = None,
            config: Any = None,
            main_provider_supports_image: bool = False,
            skip_quote_image_caption: bool = False,
        ) -> None:
            if quoted_message_settings is None:
                quoted_message_settings = getattr(
                    ama,
                    "DEFAULT_QUOTED_MESSAGE_SETTINGS",
                    None,
                )

            # 查找引用消息
            quote = None
            for comp in event.message_obj.message:
                if isinstance(comp, ama.Reply):
                    quote = comp
                    break
            if not quote:
                return

            # 提取引用文本
            content_parts: list[str] = []
            sender_info = (
                f"({quote.sender_nickname}): " if quote.sender_nickname else ""
            )
            message_str = (
                await ama.extract_quoted_message_text(
                    event,
                    quote,
                    settings=quoted_message_settings,
                )
                or quote.message_str
                or "[Empty Text]"
            )
            content_parts.append(f"{sender_info}{message_str}")

            # 处理引用中的图片
            image_seg = None
            if quote.chain:
                for comp in quote.chain:
                    if isinstance(comp, ama.Image):
                        image_seg = comp
                        break

            if image_seg:
                if skip_quote_image_caption:
                    log.debug(
                        "Skipping quote image captioning because "
                        "image captioning already handled this request."
                    )
                elif main_provider_supports_image:
                    log.debug(
                        "Skipping quote image captioning because "
                        "the main provider supports image input."
                    )
                elif not img_cap_prov_id:
                    log.debug(
                        "No dedicated image caption provider configured. "
                        "Skipping quote image captioning."
                    )
                else:
                    await _append_cached_quoted_image_caption(
                        ama=ama,
                        log=log,
                        cache=cache_ref,
                        ttl_resolver=ttl_resolver,
                        event=event,
                        content_parts=content_parts,
                        image_seg=image_seg,
                        img_cap_prov_id=img_cap_prov_id,
                        plugin_context=plugin_context,
                        config=config,
                    )

            quoted_content = "\n".join(content_parts)
            quoted_text = (
                f"<Quoted Message>\n{quoted_content}\n</Quoted Message>"
            )
            req.extra_user_content_parts.append(ama.TextPart(text=quoted_text))

        self._replace(
            ama,
            "_process_quote_message",
            cached_process_quote_message,
            "__image_caption_cache_original_process_quote_message",
        )
        return True

    # -- 工具方法 ------------------------------------------------------------

    def _replace(
        self,
        target: ModuleType | type,
        name: str,
        replacement: Any,
        original_attr: str,
    ) -> None:
        """在目标对象上保存原始函数并替换为新函数。"""
        if not hasattr(target, original_attr):
            setattr(target, original_attr, getattr(target, name))
        setattr(target, name, replacement)
        self._patches.append((target, name, original_attr, replacement))

    @staticmethod
    def _import_module(module_name: str) -> ModuleType | None:
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            logger.warning(
                f"Skip image caption cache patch for {module_name}: {exc}"
            )
            return None

    @staticmethod
    def _signature_has_prefix(func: Any, names: list[str]) -> bool:
        try:
            params = list(inspect.signature(func).parameters)
        except (TypeError, ValueError):
            return False
        return params[: len(names)] == names


# -- 模块级辅助函数（与缓存补丁共享） ----------------------------------------


def _resolve_provider_cache_identity(
    provider: Any,
    *,
    configured_provider_id: str,
) -> str:
    """为 Provider 生成稳定的缓存标识。

    优先使用用户在配置中填写的 ``configured_provider_id``，
    否则从 Provider 对象中提取类名、配置类型和模型名拼接。
    """
    if configured_provider_id:
        return configured_provider_id

    provider_config = (
        provider.provider_config
        if isinstance(provider.provider_config, dict)
        else {}
    )
    provider_id = provider_config.get("id", "")
    if isinstance(provider_id, str) and provider_id:
        return provider_id

    provider_type = provider_config.get("type", "")
    get_model = getattr(provider, "get_model", None)
    model = get_model() if callable(get_model) else ""
    return ":".join(
        [
            provider.__class__.__module__,
            provider.__class__.__qualname__,
            "" if provider_type is None else str(provider_type),
            "" if model is None else str(model),
        ]
    )


async def _append_cached_quoted_image_caption(
    *,
    ama: ModuleType,
    log: Any,
    cache: ImageCaptionCache,
    ttl_resolver: Callable[[object | None], int],
    event: Any,
    content_parts: list[str],
    image_seg: Any,
    img_cap_prov_id: str,
    plugin_context: Any,
    config: Any,
) -> None:
    """获取引用图片的转述（带缓存），并将结果追加到 content_parts。"""
    provider = None
    path = None
    compress_path = None
    try:
        provider = plugin_context.get_provider_by_id(img_cap_prov_id)
        if provider is None:
            provider = plugin_context.get_using_provider(
                event.unified_msg_origin
            )

        provider_cls = getattr(ama, "Provider", None)
        if not provider or (
            provider_cls is not None
            and not isinstance(provider, provider_cls)
        ):
            log.warning(
                "No provider found for image captioning in quote."
            )
            return

        path = await image_seg.convert_to_file_path()
        provider_settings = getattr(config, "provider_settings", None)
        compress_path = await ama._compress_image_for_provider(
            path,
            provider_settings,
        )

        if path and ama._is_generated_compressed_image_path(
            path, compress_path
        ):
            event.track_temporary_local_file(compress_path)

        caption_prompt = "Please describe the image content."

        cache_provider_id = _resolve_provider_cache_identity(
            provider,
            configured_provider_id=img_cap_prov_id,
        )
        ttl = ttl_resolver(provider_settings)

        async def caption_factory() -> str:
            response = await provider.text_chat(
                prompt=caption_prompt,
                image_urls=[compress_path],
            )
            return (
                getattr(response, "completion_text", "")
                if response
                else ""
            )

        caption = await cache.get_or_create(
            provider_id=cache_provider_id,
            prompt=caption_prompt,
            image_urls=[compress_path],
            ttl_seconds=ttl,
            caption_factory=caption_factory,
        )

        if caption:
            content_parts.append(
                f"[Image Caption in quoted message]: {caption}"
            )

    except Exception as exc:
        log.error(f"处理引用图片失败: {exc}")
    finally:
        if compress_path and compress_path != path and os.path.exists(
            compress_path
        ):
            try:
                os.remove(compress_path)
            except Exception as exc:
                log.warning(
                    f"Fail to remove temporary compressed image: {exc}"
                )
