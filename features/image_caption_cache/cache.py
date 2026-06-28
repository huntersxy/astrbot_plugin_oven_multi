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
图片转述缓存核心 —— TTL 与图片数量双策略内存缓存。

使用方式：

    cache = ImageCaptionCache()
    caption = await cache.get_or_create(
        provider_id="my_provider",
        prompt="请描述图片",
        image_urls=["base64://..."],
        ttl_seconds=300,
        caption_factory=some_async_func,
    )
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

DEFAULT_IMAGE_CAPTION_CACHE_TTL = 600
DEFAULT_IMAGE_CAPTION_CACHE_MAX_IMAGES = 200

CacheHitCallback = Callable[[str, int, str], None]


def resolve_image_caption_cache_ttl(raw: object) -> int:
    """将用户配置的 TTL 值归一化为整数秒。"""
    if isinstance(raw, bool) or raw is None:
        return DEFAULT_IMAGE_CAPTION_CACHE_TTL
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_CAPTION_CACHE_TTL


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheStats:
    """缓存统计快照。"""
    entries: int
    images: int
    locks: int


@dataclass(slots=True)
class _ImageCaptionCacheEntry:
    caption: str
    image_count: int
    expires_at: float | None
    last_accessed_at: float


# ---------------------------------------------------------------------------
# 缓存主类
# ---------------------------------------------------------------------------


class ImageCaptionCache:
    """图片转述结果缓存。

    同时支持 TTL 过期策略和图片数量上限淘汰策略。
    对于相同的 ``(provider_id, prompt, image fingerprints)`` 组合，
    在有效期内直接返回之前的结果，避免重复调用视觉模型。
    """

    def __init__(
        self,
        on_cache_hit: CacheHitCallback | None = None,
        *,
        ttl_enabled: bool = True,
        image_count_enabled: bool = True,
        max_cached_images: int = DEFAULT_IMAGE_CAPTION_CACHE_MAX_IMAGES,
        fingerprint_remote_images: bool = True,
        remote_fingerprint_timeout: float = 8.0,
        remote_fingerprint_max_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._entries: dict[str, _ImageCaptionCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._on_cache_hit = on_cache_hit
        self._ttl_enabled = ttl_enabled
        self._image_count_enabled = image_count_enabled
        self._max_cached_images = max(int(max_cached_images), 0)
        self._fingerprint_remote_images = fingerprint_remote_images
        self._remote_fingerprint_timeout = max(float(remote_fingerprint_timeout), 1.0)
        self._remote_fingerprint_max_bytes = max(int(remote_fingerprint_max_bytes), 1)

    # -- 公开接口 ------------------------------------------------------------

    def clear(self) -> int:
        """清空所有缓存条目，返回被移除的条目数。"""
        removed = len(self._entries)
        self._entries.clear()
        self._locks.clear()
        return removed

    def stats(self) -> CacheStats:
        """返回当前缓存统计（自动清理过期条目）。"""
        self._cleanup_expired_entries()
        return CacheStats(
            entries=len(self._entries),
            images=self._cached_image_count(),
            locks=len(self._locks),
        )

    async def get_or_create(
        self,
        *,
        provider_id: str,
        prompt: str,
        image_urls: list[str],
        ttl_seconds: int,
        caption_factory: Callable[[], Awaitable[str]],
        ttl_enabled: bool | None = None,
        image_count_enabled: bool | None = None,
        max_cached_images: int | None = None,
    ) -> str:
        """获取缓存的转述结果，若不存在则调用 ``caption_factory`` 创建。

        参数
        ----------
        provider_id : str
            服务商标识，用于构建缓存键。
        prompt : str
            转述提示词，用于构建缓存键。
        image_urls : list[str]
            图片 URL / base64 / 本地路径列表。
        ttl_seconds : int
            当前请求的 TTL（秒）；如果全局 TTL 策略关闭则忽略。
        caption_factory : Callable[[], Awaitable[str]]
            当缓存未命中时调用此工厂函数生成结果。
        ttl_enabled : bool | None
            临时覆盖全局 TTL 策略；``None`` 表示使用全局值。
        image_count_enabled : bool | None
            临时覆盖全局图片数量策略；``None`` 表示使用全局值。
        max_cached_images : int | None
            临时覆盖全局图片数量上限；``None`` 表示使用全局值。
        """
        ttl_seconds = max(int(ttl_seconds), 0)
        ttl_active = (self._ttl_enabled if ttl_enabled is None else ttl_enabled) and (
            ttl_seconds > 0
        )
        count_limit = (
            self._max_cached_images
            if max_cached_images is None
            else max(int(max_cached_images), 0)
        )
        image_count_active = (
            self._image_count_enabled
            if image_count_enabled is None
            else image_count_enabled
        ) and count_limit > 0

        # 如果两个策略都关闭，直接跳过缓存
        if not ttl_active and not image_count_active:
            return await caption_factory()

        cache_key = await self._build_cache_key(
            provider_id=provider_id,
            prompt=prompt,
            image_urls=image_urls,
        )

        # 双检锁：先不加锁查一次
        cached_caption = self._get(cache_key)
        if cached_caption is not None:
            self._notify_cache_hit(provider_id, len(image_urls), cache_key)
            return cached_caption

        lock = self._get_lock(cache_key)
        async with lock:
            # 获取锁后再次检查（防止并发重复创建）
            cached_caption = self._get(cache_key)
            if cached_caption is not None:
                self._notify_cache_hit(provider_id, len(image_urls), cache_key)
                return cached_caption

            caption = await caption_factory()
            self._entries[cache_key] = _ImageCaptionCacheEntry(
                caption=caption,
                image_count=max(len(image_urls), 1),
                expires_at=time.monotonic() + ttl_seconds if ttl_active else None,
                last_accessed_at=time.monotonic(),
            )
            self._cleanup_expired_entries()
            if image_count_active:
                self._evict_by_image_count_limit(count_limit)
            return caption

    # -- 内部方法 ------------------------------------------------------------

    def _get(self, cache_key: str) -> str | None:
        entry = self._entries.get(cache_key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= time.monotonic():
            self._entries.pop(cache_key, None)
            self._locks.pop(cache_key, None)
            return None
        entry.last_accessed_at = time.monotonic()
        return entry.caption

    def _notify_cache_hit(
        self,
        provider_id: str,
        image_count: int,
        cache_key: str,
    ) -> None:
        if self._on_cache_hit is None:
            return
        try:
            self._on_cache_hit(provider_id, image_count, cache_key)
        except Exception:
            pass

    def _cleanup_expired_entries(self) -> None:
        now = time.monotonic()
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if entry.expires_at is not None and entry.expires_at <= now
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
            self._locks.pop(key, None)

    def _evict_by_image_count_limit(self, max_cached_images: int) -> None:
        while self._cached_image_count() > max_cached_images and self._entries:
            oldest_key = min(
                self._entries,
                key=lambda key: self._entries[key].last_accessed_at,
            )
            self._entries.pop(oldest_key, None)
            self._locks.pop(oldest_key, None)

    def _cached_image_count(self) -> int:
        return sum(entry.image_count for entry in self._entries.values())

    def _get_lock(self, cache_key: str) -> asyncio.Lock:
        lock = self._locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cache_key] = lock
        return lock

    # -- 缓存键构建 ----------------------------------------------------------

    async def _build_cache_key(
        self,
        *,
        provider_id: str,
        prompt: str,
        image_urls: list[str],
    ) -> str:
        image_fingerprints = []
        for image_url in image_urls:
            image_fingerprints.append(await self._fingerprint_image(image_url))
        joined = "\n".join([provider_id, prompt, *image_fingerprints])
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    # -- 图片指纹 ------------------------------------------------------------

    async def _fingerprint_image(self, image_url: str) -> str:
        if image_url.startswith("base64://"):
            return self._fingerprint_base64_image(image_url)
        if image_url.startswith("data:image"):
            return self._fingerprint_data_uri_image(image_url)
        if image_url.startswith(("http://", "https://")):
            return await self._fingerprint_remote_image(image_url)
        return await self._fingerprint_local_image(image_url)

    def _fingerprint_base64_image(self, image_url: str) -> str:
        raw_base64 = image_url.removeprefix("base64://")
        try:
            image_bytes = base64.b64decode(raw_base64)
        except Exception:
            return self._reference_fingerprint(image_url)
        return self._hash_bytes(image_bytes)

    def _fingerprint_data_uri_image(self, image_url: str) -> str:
        try:
            _, encoded = image_url.split(",", 1)
            image_bytes = base64.b64decode(encoded)
        except Exception:
            return self._reference_fingerprint(image_url)
        return self._hash_bytes(image_bytes)

    async def _fingerprint_remote_image(self, image_url: str) -> str:
        if not self._fingerprint_remote_images:
            return f"url:{image_url}"
        try:
            import aiohttp
        except ImportError:
            return f"url:{image_url}"
        try:
            timeout = aiohttp.ClientTimeout(total=self._remote_fingerprint_timeout)
            async with aiohttp.ClientSession(
                trust_env=True,
                timeout=timeout,
            ) as session:
                async with session.get(image_url) as response:
                    if response.status != 200:
                        return f"url:{image_url}"
                    content_length = response.headers.get("content-length")
                    if (
                        content_length
                        and int(content_length) > self._remote_fingerprint_max_bytes
                    ):
                        return f"url:{image_url}"
                    digest = hashlib.sha256()
                    downloaded = 0
                    async for chunk in response.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        if downloaded > self._remote_fingerprint_max_bytes:
                            return f"url:{image_url}"
                        digest.update(chunk)
                    return f"remote:{digest.hexdigest()}"
        except Exception:
            return f"url:{image_url}"

    async def _fingerprint_local_image(self, image_url: str) -> str:
        local_path = self._to_local_path(image_url)
        if local_path and local_path.is_file():
            image_bytes = await asyncio.to_thread(local_path.read_bytes)
            return self._hash_bytes(image_bytes)
        return self._reference_fingerprint(image_url)

    def _to_local_path(self, image_url: str) -> Path | None:
        if image_url.startswith("file://"):
            parsed = urlparse(image_url)
            parsed_path = unquote(parsed.path)
            if (
                parsed_path.startswith("/")
                and len(parsed_path) >= 3
                and parsed_path[2] == ":"
            ):
                parsed_path = parsed_path[1:]
            return Path(parsed_path)
        if image_url.startswith(("http://", "https://", "base64://", "data:image")):
            return None
        return Path(image_url)

    @staticmethod
    def _hash_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _reference_fingerprint(image_url: str) -> str:
        return f"ref:{image_url}"
