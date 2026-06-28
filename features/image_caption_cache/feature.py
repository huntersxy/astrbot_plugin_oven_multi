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
图片转述缓存 —— 功能模块集成层。

将 ``ImageCaptionCache`` 和 ``ImageCaptionCachePatcher`` 封装为
符合本插件架构的功能模块，负责初始化、日志和生命周期管理。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from .cache import (
    DEFAULT_IMAGE_CAPTION_CACHE_TTL,
    DEFAULT_IMAGE_CAPTION_CACHE_MAX_IMAGES,
    ImageCaptionCache,
    resolve_image_caption_cache_ttl,
)
from .patcher import ImageCaptionCachePatcher

from ...utils.constants import FEATURE_IMAGE_CAPTION_CACHE

# 缓存命中日志去重时间（秒）
_CACHE_HIT_LOG_DEDUP_SECONDS = 1.0


class ImageCaptionCacheFeature:
    """图片转述缓存功能模块。

    管理缓存实例和运行时补丁的完整生命周期。
    """

    def __init__(
        self,
        config_mgr: Any,
    ) -> None:
        self._config_mgr = config_mgr
        self.cache: ImageCaptionCache | None = None
        self._patcher: ImageCaptionCachePatcher | None = None
        self._patched_targets: list[str] = []
        self._recent_cache_hit_logs: dict[str, float] = {}
        self._initialized = False

    # -- 生命周期 ------------------------------------------------------------

    def initialize(self) -> None:
        """初始化缓存和补丁。

        根据配置创建 ``ImageCaptionCache`` 和 ``ImageCaptionCachePatcher``
        实例，并尝试立即应用补丁。
        """
        if not self._is_enabled():
            logger.info(
                "[烤箱-图片转述缓存] 功能未启用，跳过初始化"
            )
            return

        feature_cfg = self._get_feature_cfg()

        self.cache = ImageCaptionCache(
            on_cache_hit=self._log_cache_hit,
            ttl_enabled=self._cfg_bool(
                feature_cfg, "enable_ttl_cache", True
            ),
            image_count_enabled=self._cfg_bool(
                feature_cfg, "enable_image_count_cache", True
            ),
            max_cached_images=self._cfg_int(
                feature_cfg, "max_cached_images", DEFAULT_IMAGE_CAPTION_CACHE_MAX_IMAGES
            ),
            fingerprint_remote_images=self._cfg_bool(
                feature_cfg, "fingerprint_remote_images", True
            ),
            remote_fingerprint_timeout=self._cfg_float(
                feature_cfg, "remote_fingerprint_timeout", 8.0
            ),
            remote_fingerprint_max_bytes=self._cfg_int(
                feature_cfg, "remote_fingerprint_max_bytes", 20 * 1024 * 1024
            ),
        )

        self._patcher = ImageCaptionCachePatcher(
            cache=self.cache,
            ttl_resolver=self._resolve_ttl,
            logger=logger,
        )

        self._apply_patches(reason="init")
        self._initialized = True
        logger.info("[烤箱-图片转述缓存] 初始化完成")

    def apply_patches_on_loaded(self) -> None:
        """在 AstrBot 完全加载后再次尝试应用补丁。

        应在 ``on_astrbot_loaded`` 事件中调用，此时核心模块
        应该已全部就绪。
        """
        if not self._initialized:
            self.initialize()
            return
        if not self._is_enabled():
            return
        self._apply_patches(reason="astrbot_loaded")

    def cleanup(self) -> None:
        """恢复补丁并清空缓存。"""
        if self._patcher:
            self._patcher.restore()
        if self.cache:
            self.cache.clear()
        logger.info("[烤箱-图片转述缓存] 已卸载")

    # -- 命令处理辅助 --------------------------------------------------------

    def clear_cache(self) -> int:
        """清空缓存，返回移除条目数。"""
        if not self.cache:
            return 0
        return self.cache.clear()

    def get_stats_text(self) -> str:
        """返回格式化的缓存状态文本。"""
        if not self.cache:
            return "图片转述缓存未初始化。"

        stats = self.cache.stats()
        feature_cfg = self._get_feature_cfg()
        ttl = self._resolve_ttl(None)

        lines = [
            f"图片转述缓存：{stats.entries} 条，{stats.images} 张图，"
            f"锁 {stats.locks} 个；",
            f"TTL 策略：{self._enabled_text(self._ttl_strategy_enabled())}"
            f"（{ttl} 秒）；",
            "图片数量策略："
            f"{self._enabled_text(self._image_count_strategy_enabled())}"
            f"（上限 {self._max_images()} 张）；",
            f"补丁：{','.join(self._patched_targets) or 'none'}。",
        ]
        return "".join(lines)

    # -- 内部方法 ------------------------------------------------------------

    def _apply_patches(self, *, reason: str) -> None:
        if self._patched_targets:
            logger.debug(
                f"[烤箱-图片转述缓存] 已打过补丁，跳过 "
                f"(reason={reason})"
            )
            return

        feature_cfg = self._get_feature_cfg()
        self._patched_targets = self._patcher.apply(
            patch_main_agent=self._cfg_bool(
                feature_cfg, "patch_main_agent", True
            ),
            patch_quoted_message=self._cfg_bool(
                feature_cfg, "patch_quoted_message", True
            ),
        )

        logger.info(
            f"[烤箱-图片转述缓存] 补丁应用完成. "
            f"reason={reason}, "
            f"patched={','.join(self._patched_targets) or 'none'}"
        )

        if not self._patched_targets:
            logger.warning(
                "[烤箱-图片转述缓存] 未打到任何补丁。"
                "请检查 AstrBot 版本和核心函数签名。"
            )

    def _log_cache_hit(
        self, provider_id: str, image_count: int, cache_key: str
    ) -> None:
        """记录缓存命中日志（带去重）。"""
        now = time.monotonic()
        last_logged_at = self._recent_cache_hit_logs.get(cache_key)
        if (
            last_logged_at is not None
            and now - last_logged_at < _CACHE_HIT_LOG_DEDUP_SECONDS
        ):
            return
        self._recent_cache_hit_logs[cache_key] = now
        self._cleanup_stale_log_entries(now)
        logger.info(
            "[烤箱-图片转述缓存] 缓存命中. "
            f"provider={provider_id or '<default>'}, images={image_count}"
        )

    def _cleanup_stale_log_entries(self, now: float) -> None:
        expired_keys = [
            key
            for key, logged_at in self._recent_cache_hit_logs.items()
            if now - logged_at >= _CACHE_HIT_LOG_DEDUP_SECONDS
        ]
        for key in expired_keys:
            self._recent_cache_hit_logs.pop(key, None)

    # -- 配置辅助 ------------------------------------------------------------

    def _is_enabled(self) -> bool:
        return self._config_mgr.is_feature_enabled(
            FEATURE_IMAGE_CAPTION_CACHE, default=True
        )

    def _get_feature_cfg(self) -> dict[str, Any]:
        return self._config_mgr.get_feature_config(
            FEATURE_IMAGE_CAPTION_CACHE, {}
        ) or {}

    def _resolve_ttl(self, runtime_config: object | None) -> int:
        feature_cfg = self._get_feature_cfg()
        value = feature_cfg.get(
            "image_caption_cache_ttl", DEFAULT_IMAGE_CAPTION_CACHE_TTL
        )
        return resolve_image_caption_cache_ttl(value)

    def _ttl_strategy_enabled(self) -> bool:
        return (
            self._cfg_bool(
                self._get_feature_cfg(), "enable_ttl_cache", True
            )
            and self._resolve_ttl(None) > 0
        )

    def _image_count_strategy_enabled(self) -> bool:
        return (
            self._cfg_bool(
                self._get_feature_cfg(), "enable_image_count_cache", True
            )
            and self._max_images() > 0
        )

    def _max_images(self) -> int:
        return self._cfg_int(
            self._get_feature_cfg(),
            "max_cached_images",
            DEFAULT_IMAGE_CAPTION_CACHE_MAX_IMAGES,
        )

    @staticmethod
    def _enabled_text(enabled: bool) -> str:
        return "开启" if enabled else "关闭"

    @staticmethod
    def _cfg_bool(
        cfg: dict[str, Any], key: str, default: bool
    ) -> bool:
        value = cfg.get(key, default)
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {
            "1", "true", "yes", "on", "enable",
        }

    @staticmethod
    def _cfg_int(cfg: dict[str, Any], key: str, default: int) -> int:
        value = cfg.get(key, default)
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _cfg_float(
        cfg: dict[str, Any], key: str, default: float
    ) -> float:
        value = cfg.get(key, default)
        if isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
