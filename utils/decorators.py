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

import functools
from typing import Callable, Any
from astrbot.api import logger


def with_error_handling(feature_name: str):
    """通用错误处理装饰器
    
    Args:
        feature_name: 功能模块名称，用于日志标识
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[烤箱-{feature_name}] {func.__name__} 失败: {e}")
                return None
        return wrapper
    return decorator


def with_timing(feature_name: str):
    """性能计时装饰器
    
    Args:
        feature_name: 功能模块名称，用于日志标识
    """
    import time
    
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            result = await func(*args, **kwargs)
            elapsed = time.time() - start
            if elapsed > 1.0:  # 超过1秒记录警告
                logger.warning(f"[烤箱-{feature_name}] {func.__name__} 耗时过长: {elapsed:.2f}s")
            return result
        return wrapper
    return decorator


def log_call(feature_name: str, level: str = "debug"):
    """函数调用日志装饰器
    
    Args:
        feature_name: 功能模块名称
        level: 日志级别 (debug, info, warning, error)
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            log_func = getattr(logger, level, logger.debug)
            log_func(f"[烤箱-{feature_name}] 调用 {func.__name__}")
            result = await func(*args, **kwargs)
            return result
        return wrapper
    return decorator
