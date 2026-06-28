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
# Image caption cache integration. The cache and patcher core logic is
# derived from astrbot_plugin_image_caption_cache (AGPL-3.0) by Florance.
# Repository: https://github.com/FloranceYeh/astrbot_plugin_image_caption_cache

from .cache import ImageCaptionCache, CacheStats, resolve_image_caption_cache_ttl
from .patcher import ImageCaptionCachePatcher
from .feature import ImageCaptionCacheFeature

__all__ = [
    "ImageCaptionCache",
    "CacheStats",
    "resolve_image_caption_cache_ttl",
    "ImageCaptionCachePatcher",
    "ImageCaptionCacheFeature",
]
