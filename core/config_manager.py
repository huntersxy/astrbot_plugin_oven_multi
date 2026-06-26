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

from typing import Any, Optional
from astrbot.api import logger


class ConfigManager:
    """统一配置管理器
    
    提供统一的配置访问接口，避免各模块重复验证配置格式。
    """
    
    def __init__(self, config: dict[str, Any]):
        self._config = config or {}
    
    def is_feature_enabled(self, feature_name: str, default: bool = True) -> bool:
        """检查功能是否启用
        
        Args:
            feature_name: 功能名称
            default: 默认启用状态
            
        Returns:
            功能是否启用
        """
        cfg = self._config.get(feature_name, {})
        if not isinstance(cfg, dict):
            return default
        return cfg.get("enabled", default)
    
    def get_feature_config(self, feature_name: str, default: Any = None) -> dict[str, Any]:
        """获取功能配置
        
        Args:
            feature_name: 功能名称
            default: 默认配置
            
        Returns:
            功能配置字典
        """
        cfg = self._config.get(feature_name, default)
        if not isinstance(cfg, dict):
            return default or {}
        return cfg
    
    def get_config_value(self, feature_name: str, key: str, default: Any = None) -> Any:
        """获取配置值
        
        Args:
            feature_name: 功能名称
            key: 配置键
            default: 默认值
            
        Returns:
            配置值
        """
        cfg = self.get_feature_config(feature_name, {})
        return cfg.get(key, default)
    
    def get_blacklist(self, list_type: str = "groups") -> list[str]:
        """获取黑名单列表
        
        Args:
            list_type: 列表类型 (groups 或 users)
            
        Returns:
            黑名单列表
        """
        key = f"blacklist_{list_type}"
        return self._config.get(key, [])
    
    def is_blacklisted(self, group_id: str = None, user_id: str = None) -> bool:
        """检查是否在黑名单中
        
        Args:
            group_id: 群组ID
            user_id: 用户ID
            
        Returns:
            是否在黑名单中
        """
        if group_id and group_id in self.get_blacklist("groups"):
            return True
        if user_id and user_id in self.get_blacklist("users"):
            return True
        return False
    
    def update_config(self, feature_name: str, key: str, value: Any) -> None:
        """更新配置值
        
        Args:
            feature_name: 功能名称
            key: 配置键
            value: 配置值
        """
        if feature_name not in self._config:
            self._config[feature_name] = {}
        if not isinstance(self._config[feature_name], dict):
            self._config[feature_name] = {}
        self._config[feature_name][key] = value
    
    def get_raw_config(self) -> dict[str, Any]:
        """获取原始配置"""
        return self._config.copy()
