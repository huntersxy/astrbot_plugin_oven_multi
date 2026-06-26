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

from abc import ABC, abstractmethod
from typing import Any
from astrbot.api import logger
from .config_manager import ConfigManager


class BaseFeature(ABC):
    """功能模块基类
    
    所有功能模块都应继承此类，实现统一的接口。
    """
    
    def __init__(self, config_manager: ConfigManager, feature_name: str):
        self.config = config_manager
        self.feature_name = feature_name
        self._initialized = False
    
    @abstractmethod
    async def initialize(self) -> bool:
        """初始化功能模块
        
        Returns:
            初始化是否成功
        """
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """清理功能模块资源"""
        pass
    
    def is_enabled(self) -> bool:
        """检查功能是否启用"""
        return self.config.is_feature_enabled(self.feature_name)
    
    def get_config(self, key: str = None, default: Any = None) -> Any:
        """获取功能配置
        
        Args:
            key: 配置键，为None时返回整个配置
            default: 默认值
            
        Returns:
            配置值
        """
        if key is None:
            return self.config.get_feature_config(self.feature_name, {})
        return self.config.get_config_value(self.feature_name, key, default)
    
    def log_info(self, message: str) -> None:
        """记录信息日志"""
        logger.info(f"[烤箱-{self.feature_name}] {message}")
    
    def log_debug(self, message: str) -> None:
        """记录调试日志"""
        logger.debug(f"[烤箱-{self.feature_name}] {message}")
    
    def log_warning(self, message: str) -> None:
        """记录警告日志"""
        logger.warning(f"[烤箱-{self.feature_name}] {message}")
    
    def log_error(self, message: str) -> None:
        """记录错误日志"""
        logger.error(f"[烤箱-{self.feature_name}] {message}")
