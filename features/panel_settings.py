# Copyright (C) 2026 汐兮雨
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""原生 Panel 快捷设置：面板是除密钥/选择器外全部配置项的管理入口。

配置双入口分工（1.52.0 起）：
- AstrBot 插件配置页保留 7 个字段（见 ``NATIVE_ONLY_PATHS``）——密钥、
  余额 YAML 编辑器与两个 Provider 选择器及 Jev 连接地址、模型名，其余字段在 schema 中以
  ``invisible: true`` 隐藏（字段必须留在 schema：AstrBot 加载时会剔除
  schema 之外的 key，删掉即丢值）；
- 本模块覆盖其余全部叶子路径，标签/默认值/边界/选项直接取自
  ``_conf_schema.json``，避免两处维护；
- 保存改为「深合并进宿主配置后整体持久化」：原生 ``save_config(补丁)``
  对顶层是浅合并、整节替换，会把同节不在提交里的字段（如 ``jev.api_key``）
  一并抹掉；revision + 可编辑值快照双重防陈旧逻辑不变。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from pathlib import Path

# 面板管理的配置路径（按展示分组）。配置路径支持任意层级（如 file_reader.rag.*）。
GROUP_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "抖音通知",
        (
            "douyin.enabled", "douyin.check_interval", "douyin.request_timeout",
            "douyin.notify_on_live", "douyin.notify_on_offline", "douyin.notify_with_cover",
            "douyin.max_rooms_per_session", "douyin.admin_only",
        ),
    ),
    (
        "黑名单",
        (
            "blacklist_groups",
            "blacklist_users",
        ),
    ),
    (
        "消息与回复",
        (
            "bracket_matching.enabled",
            "bracket_matching.only_first_missing",
            "repetition.enabled",
            "repetition.repeat_threshold",
            "repetition.break_spell_probability",
            "repetition.break_spell_text",
            "remove_blank_lines.enabled",
            "remove_blank_lines.max_consecutive_newlines",
            "iam_thinking.enabled",
            "iam_thinking.thinking_emoji_ids",
            "iam_thinking.done_emoji_ids",
            "iam_thinking.remove_thinking_on_done",
            "iam_thinking.add_done_emoji",
            "mention_parser.enabled",
            "mention_parser.max_speakers",
            "mention_parser.debug_mode",
        ),
    ),
    (
        "分段回复",
        (
            "segment_reply.enabled",
            "segment_reply.process_non_llm",
            "segment_reply.min_length",
            "segment_reply.max_length",
            "segment_reply.allow_exceed_max",
            "segment_reply.hard_max_limit",
            "segment_reply.merge_short_tail",
            "segment_reply.short_tail_threshold",
            "segment_reply.force_split_symbols",
            "segment_reply.split_symbols",
            "segment_reply.keep_symbol",
            "segment_reply.extend_to_trailing_symbols",
            "segment_reply.protect_paired_symbols",
            "segment_reply.exclude_keywords",
            "segment_reply.skip_longer_than",
            "segment_reply.delay_type",
            "segment_reply.fixed_delay_seconds",
            "segment_reply.random_delay_range",
        ),
    ),
    (
        "Jev 判读",
        (
            "jev.enabled",
            "jev.active_reply_enabled",
            "jev.active_reply_threshold",
            "jev.active_interval_sec",
            "jev.inject_on_mention",
            "jev.debug_mode",
            "jev.timeout_sec",
            "jev.retries",
            "jev.history_rounds",
            "jev.max_state_chars",
            "jev.min_message_chars",
            "jev.desensitize",
            "jev.enable_advice",
            "jev.show_confidence",
            "jev.max_injection_chars",
        ),
    ),
    (
        "文件读取",
        (
            "file_reader.enabled",
            "file_reader.preread.enabled",
            "file_reader.preread.notify",
            "file_reader.tool.enabled",
            "file_reader.tool.max_chars",
            "file_reader.rag.enabled",
            "file_reader.rag.injection_type",
            "file_reader.rag.chunk_size",
            "file_reader.rag.chunk_overlap",
            "file_reader.rag.retrieve_top_k",
            "file_reader.rag.fetch_k",
            "file_reader.rag.enable_rerank",
            "file_reader.max_file_size_mb",
            "file_reader.retention_minutes",
            "file_reader.max_rounds",
            "file_reader.cleanup_interval_minutes",
            "file_reader.supported_file_types",
        ),
    ),
    (
        "上下文压缩",
        (
            "bili.enabled",
            "bili.mode",
            "bili.executable",
            "bili.auto_install",
            "bili.host",
            "bili.port",
            "bili.provider_ids",
            "bili.auto_follow",
            "bili.session_header",
            "bili.health_interval_sec",
        ),
    ),
)

# 仅由 AstrBot 插件配置页维护、面板不接管的字段（密钥 / 原生选择器 / YAML 编辑器）。
NATIVE_ONLY_PATHS: frozenset[str] = frozenset(
    {
        "jev.api_key",
        "jev.base_url",
        "jev.model",
        "balance.config_content",
        "balance.config_mode",
        "file_reader.rag.embedding_provider_id",
        "file_reader.rag.rerank_provider_id",
    }
)

# 分组顶部的一行指引（指向留在插件配置页的字段）。
GROUP_NOTES: dict[str, str] = {
    "抖音通知": (
        "使用「抖音订阅 / 抖音取关 / 抖音列表 / 抖音检查」管理当前会话。"
        "首次读取旧独立插件的订阅和登录态；请停用独立插件后启用这里，避免重复通知。"
        "登录态由管理员使用「抖音Cookie / 抖音CookieFile」更新，不在面板返回 Cookie 原文。"
    ),
    "Jev 判读": (
        "API Key、TypeSafe 接口地址和模型名在 AstrBot 原生插件配置页填写；"
        "主动回复与意图／情绪两个开关独立。"
    ),
    "文件读取": "Embedding / Rerank Provider 请在 AstrBot 插件配置页选择。",
    "上下文压缩": (
        "开启并保存后自动安装、启动代理并接管当前聊天模型。"
        "关闭自动安装时仍可点击「安装／重试」；外部模式由你自行运行代理。"
        "安装需要 AstrBot 所在环境提供 Node ≥20 和 npm，进度与失败原因见上方状态卡。"
    ),
}

# 覆盖元数据：schema 未表达或需要面板特殊化的字段（渲染类型与边界）。
FIELD_OVERRIDES: dict[str, dict] = {
    "segment_reply.random_delay_range": {
        "render": "pair",
        "min": 0,
        "max": 60,
        "step": 0.5,
        "hint": "随机间隔范围（秒），格式 [最小值, 最大值]，正态分布并限制在区间内。",
    },
    "blacklist_groups": {
        "hint": "不启用插件的群号码列表，一行一个。",
    },
    "blacklist_users": {
        "hint": "不启用插件的 QQ 用户号码列表，一行一个。",
    },
    "iam_thinking.thinking_emoji_ids": {
        "item_type": "int",
        "hint": "等待 LLM 响应时贴的表情 ID，一行一个。",
    },
    "iam_thinking.done_emoji_ids": {
        "item_type": "int",
        "hint": "LLM 响应完成后贴的表情 ID，一行一个。",
    },
    "jev.base_url": {
        "max_chars": 200,
    },
    "bili.executable": {
        "max_chars": 200,
    },
    "bili.session_header": {
        "max_chars": 100,
    },
    "bili.provider_ids": {
        "hint": "一行一个对话 provider ID；自动跟随开启时作为初始列表，后续切换的模型会自动补充。"
        "关闭自动跟随后固定已有接管范围。",
    },
}

_STRING_MAX_CHARS = 50

_MISSING = object()


class StaleSettingsError(ValueError):
    """表单基于过期配置渲染，拒绝保存（提示刷新）。"""


def _load_schema() -> dict:
    """读取插件配置 schema；缺失或损坏时返回空表（面板退化为不可编辑）。"""
    path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_SCHEMA = _load_schema()


def _schema_meta(path: str) -> dict:
    """按点分路径取叶子配置的 schema 元数据（支持任意层级嵌套）。"""
    node: object = _SCHEMA
    for segment in path.split("."):
        if isinstance(node, dict) and isinstance(node.get("items"), dict):
            node = node["items"]
        if not isinstance(node, dict) or not isinstance(node.get(segment), dict):
            return {}
        node = node[segment]
    return node if isinstance(node, dict) else {}


def _config_value(config: dict, path: str) -> object:
    """按点分路径取配置叶子值；任一层缺失或非 dict 返回 ``_MISSING``。"""
    node: object = config
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return _MISSING
        node = node[segment]
    return node


def _describe_field(path: str) -> dict:
    """渲染字段描述：类型、边界、选项与文案，全部以 schema 为准。"""
    meta = _schema_meta(path)
    override = FIELD_OVERRIDES.get(path, {})
    options = meta.get("options")
    render = override.get("render")
    if render is None:
        if options:
            render = "enum"
        elif meta.get("type") == "bool":
            render = "bool"
        elif meta.get("type") == "int":
            render = "int"
        elif meta.get("type") == "float":
            render = "float"
        elif meta.get("type") == "list":
            render = "list"
        else:
            render = "string"
    slider = meta.get("slider") if isinstance(meta.get("slider"), dict) else {}
    field = {
        "key": path,
        "label": str(meta.get("description") or path),
        "type": render,
        "value": meta.get("default"),
    }
    hint = override.get("hint", meta.get("hint"))
    if hint:
        field["hint"] = str(hint)
    for bound in ("min", "max", "step"):
        if bound in override:
            field[bound] = override[bound]
        elif bound in slider:
            field[bound] = slider[bound]
    if options:
        field["options"] = list(options)
    if render == "string":
        field["max_chars"] = override.get("max_chars", _STRING_MAX_CHARS)
    if render == "list":
        field["item_type"] = override.get("item_type", "str")
    return field


def describe_groups(config: dict) -> list[dict]:
    """按分组返回字段描述与当前实际值（缺失时回退 schema 默认值）。"""
    groups = []
    for name, paths in GROUP_PATHS:
        fields = []
        for path in paths:
            field = _describe_field(path)
            current = _config_value(config, path)
            field["value"] = (
                json.loads(json.dumps(current)) if current is not _MISSING else field["value"]
            )
            fields.append(field)
        group = {"name": name, "fields": fields}
        if name in GROUP_NOTES:
            group["note"] = GROUP_NOTES[name]
        groups.append(group)
    return groups


def collect_values(config: dict) -> dict:
    """收集全部可编辑路径的当前值（含回退默认），用于陈旧检测。"""
    values = {}
    for _, paths in GROUP_PATHS:
        for path in paths:
            current = _config_value(config, path)
            values[path] = (
                json.loads(json.dumps(current))
                if current is not _MISSING
                else _describe_field(path)["value"]
            )
    return values


def schema_leaf_paths() -> set[str]:
    """枚举 schema 全部叶子配置路径（供测试核对双入口覆盖完整性）。"""
    leaves: set[str] = set()

    def walk(node: dict, prefix: str) -> None:
        items = node.get("items")
        if not isinstance(items, dict):
            if prefix:
                leaves.add(prefix)
            return
        for key, child in items.items():
            if isinstance(child, dict):
                walk(child, f"{prefix}.{key}" if prefix else key)

    for section, meta in _SCHEMA.items():
        if isinstance(meta, dict):
            walk(meta, section)
    return leaves


def _reject(path: str, reason: str):
    raise ValueError(f"「{_describe_field(path)['label']}」{reason}")


def _validate_number(path: str, value, field: dict, integer: bool) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(path, "必须是整数。" if integer else "必须是数字。")
    if integer and not isinstance(value, int):
        _reject(path, "必须是整数。")
    low, high = field.get("min"), field.get("max")
    if low is not None and value < low or high is not None and value > high:
        _reject(path, f"需要在 {low} ~ {high} 之间。")
    return value


def _clean_list_items(path: str, items: list, field: dict) -> list:
    """清洗列表项：str 列表去空白拒空项，int 列表把数字文本转成整数。"""
    item_type = field.get("item_type", "str")
    cleaned = []
    for item in items:
        if isinstance(item, bool):
            _reject(path, "列表项不能是开关值。")
        if item_type == "int":
            if isinstance(item, int):
                cleaned.append(item)
                continue
            try:
                cleaned.append(int(str(item).strip()))
            except ValueError:
                _reject(path, "列表项必须是整数。")
        else:
            text = item.strip() if isinstance(item, str) else str(item)
            if not text:
                _reject(path, "列表项不能为空。")
            cleaned.append(text)
    return cleaned


def _set_nested(nested: dict, segments: list[str], value) -> None:
    node = nested
    for segment in segments[:-1]:
        node = node.setdefault(segment, {})
    node[segments[-1]] = value


# 分组内的联动校验（扁平路径对）：提交里同时出现时才检查。
_CROSS_CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "segment_reply.min_length",
        "segment_reply.max_length",
        "「单段最小字数」不能大于「常规最大字数」。",
    ),
)


def validate_patch(values: dict) -> dict:
    """校验面板提交的扁平键值，返回按 schema 层级嵌套的补丁；非法输入抛 ValueError。"""
    if not isinstance(values, dict):
        raise ValueError("设置内容必须为对象。")
    known = {path for _, paths in GROUP_PATHS for path in paths}
    unknown = set(values) - known
    if unknown:
        raise ValueError("设置包含不支持的字段，请刷新设置页后重试。")

    fields = {path: _describe_field(path) for path in values}
    flat: dict = {}
    for path, value in values.items():
        field = fields[path]
        render = field["type"]
        if render == "bool":
            if not isinstance(value, bool):
                _reject(path, "必须是开关值。")
        elif render == "int":
            value = _validate_number(path, value, field, integer=True)
        elif render == "float":
            value = _validate_number(path, value, field, integer=False)
        elif render == "enum":
            if value not in field.get("options", []):
                _reject(path, "不是有效选项。")
        elif render == "pair":
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)
            ):
                _reject(path, "必须是两个数字构成的范围。")
            low, high = field.get("min"), field.get("max")
            if (low is not None and min(value) < low) or (high is not None and max(value) > high):
                _reject(path, f"每一项都需要在 {low} ~ {high} 之内。")
            if value[0] > value[1]:
                _reject(path, "最小值不能大于最大值。")
        elif render == "list":
            if not isinstance(value, list):
                _reject(path, "必须是列表。")
            value = _clean_list_items(path, value, field)
        else:  # string
            if not isinstance(value, str) or not value.strip():
                _reject(path, "不能为空。")
            elif len(value) > field.get("max_chars", _STRING_MAX_CHARS):
                _reject(path, f"长度不能超过 {field.get('max_chars', _STRING_MAX_CHARS)} 字。")
        flat[path] = value

    for first, second, message in _CROSS_CHECKS:
        if first in flat and second in flat and flat[first] > flat[second]:
            raise ValueError(message)

    nested: dict[str, dict] = {}
    for path, value in flat.items():
        _set_nested(nested, path.split("."), value)
    return nested


def _merge_patch(node: dict, patch: dict) -> None:
    """把嵌套补丁逐叶合并进 node（沿途缺失或损坏的中间层重建为 dict）。"""
    for key, value in patch.items():
        if isinstance(value, dict):
            child = node.get(key)
            if not isinstance(child, dict):
                child = {}
                node[key] = child
            _merge_patch(child, value)
        else:
            node[key] = value


def apply_values(config: dict, nested: dict) -> None:
    """把嵌套补丁合并进宿主配置并整体持久化；写盘失败时回滚内存中的节。

    不走 ``save_config(补丁)``：原生 ``save_config`` 对顶层做浅合并、整节
    替换，会把同节未提交的字段（如 ``jev.api_key``）一并抹掉。
    """
    previous = {
        section: (deepcopy(config[section]) if section in config else _MISSING)
        for section in nested
    }
    try:
        # Merge from the root: top-level lists are leaves, not object sections.
        # Include merging in the rollback guard as well as persistence.
        _merge_patch(config, nested)
        if callable(getattr(config, "save_config", None)):
            config.save_config()
        # 纯 dict 宿主：值已就地合并，无需额外持久化
    except Exception:
        for section, value in previous.items():
            if value is _MISSING:
                config.pop(section, None)
            else:
                config[section] = value
        raise


class PanelSettingsManager:
    """面板设置会话状态：revision 防陈旧 + 校验后写回。"""

    def __init__(self):
        self.revision = uuid.uuid4().hex
        self._snapshot: dict | None = None
        self._lock = asyncio.Lock()

    def describe(self, config: dict) -> dict:
        """返回分组字段与当前值；每次读取都重置防陈旧快照。

        快照必须随读取重置：报错指引是「刷新设置页后重新编辑」，若只在
        首次读取时记录，一旦配置被外部修改，刷新也无法恢复，保存将永远
        被拒。未刷新就保存（表单基于旧值渲染）仍会被快照对比拦截；并发
        编辑由 revision 机制兜底。
        """
        self._snapshot = collect_values(config)
        return {
            "revision": self.revision,
            "groups": describe_groups(config),
        }

    async def save(self, config: dict, values: dict, expected_revision: str | None = None) -> str:
        """校验并保存补丁，返回新 revision。

        Raises:
            StaleSettingsError: 表单基于过期配置渲染（其他页面已保存或修改）。
            ValueError: 字段校验失败或持久化失败。
        """
        async with self._lock:
            if expected_revision is not None and expected_revision != self.revision:
                raise StaleSettingsError("设置已在其他页面保存过，请刷新设置页后重新编辑。")
            if self._snapshot is not None and collect_values(config) != self._snapshot:
                raise StaleSettingsError(
                    "配置已在其他页面（如 AstrBot 插件配置页）修改，请刷新设置页后重新编辑。"
                )
            nested = validate_patch(values)
            apply_values(config, nested)
            self._snapshot = collect_values(config)
            self.revision = uuid.uuid4().hex
            return self.revision
