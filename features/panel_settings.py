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
"""原生 Panel 快捷设置：白名单字段、校验与写回宿主插件配置。

参照烤箱之心的工坊模式（参考 astrbot_plugin_ovenheart page_api.py）：
- 可编辑字段是白名单子集，标签/默认值/边界/选项直接取自 ``_conf_schema.json``，
  避免两处维护；
- 保存走 AstrBot 原生 ``AstrBotConfig.save_config()`` 原子持久化（无宿主对象时
  回退普通字典更新），失败回滚内存中的节；
- revision + 可编辑值快照双重防陈旧：其他面板保存、或用户在插件配置页改动过
  可编辑字段后，旧表单保存会被拒绝并提示刷新。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

# 白名单：面板可编辑的配置路径（按展示分组）。符号/关键词等列表与密钥类字段
# 刻意不在列，请使用 AstrBot 插件配置页维护。
GROUP_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
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
            "mention_parser.enabled",
            "mention_parser.max_speakers",
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
            "segment_reply.skip_longer_than",
            "segment_reply.delay_type",
            "segment_reply.fixed_delay_seconds",
            "segment_reply.random_delay_range",
            "segment_reply.keep_symbol",
            "segment_reply.extend_to_trailing_symbols",
            "segment_reply.protect_paired_symbols",
        ),
    ),
    (
        "判读与文件",
        (
            "jev.enabled",
            "jev.inject_on_mention",
            "jev.debug_mode",
            "file_reader.enabled",
        ),
    ),
)

# 覆盖元数据：schema 未表达或需要面板特殊化的字段（渲染类型与边界）。
FIELD_OVERRIDES: dict[str, dict] = {
    "segment_reply.random_delay_range": {
        "render": "pair",
        "min": 0,
        "max": 60,
        "step": 0.5,
        "hint": "随机间隔范围（秒），格式 [最小值, 最大值]，正态分布并限制在区间内。",
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
    """取某配置路径的 schema 元数据（description/hint/default/options/slider）。"""
    section, _, key = path.partition(".")
    items = _SCHEMA.get(section)
    if isinstance(items, dict) and isinstance(items.get("items"), dict):
        meta = items["items"].get(key)
        if isinstance(meta, dict):
            return meta
    return {}


def _section_of(config: dict, section: str) -> dict:
    value = config.get(section) if hasattr(config, "get") else None
    return value if isinstance(value, dict) else {}


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
        field["max_chars"] = _STRING_MAX_CHARS
    return field


def describe_groups(config: dict) -> list[dict]:
    """按分组返回字段描述与当前实际值（缺失时回退 schema 默认值）。"""
    groups = []
    for name, paths in GROUP_PATHS:
        fields = []
        for path in paths:
            field = _describe_field(path)
            section, _, key = path.partition(".")
            current = _section_of(config, section).get(key, _MISSING)
            field["value"] = (
                json.loads(json.dumps(current)) if current is not _MISSING else field["value"]
            )
            fields.append(field)
        groups.append({"name": name, "fields": fields})
    return groups


def collect_values(config: dict) -> dict:
    """收集全部可编辑路径的当前值（含回退默认），用于陈旧检测。"""
    values = {}
    for _, paths in GROUP_PATHS:
        for path in paths:
            section, _, key = path.partition(".")
            current = _section_of(config, section).get(key, _MISSING)
            values[path] = (
                json.loads(json.dumps(current))
                if current is not _MISSING
                else _describe_field(path)["value"]
            )
    return values


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


def validate_patch(values: dict) -> dict:
    """校验面板提交的扁平键值，返回按节嵌套的补丁；非法输入抛 ValueError。"""
    if not isinstance(values, dict):
        raise ValueError("设置内容必须为对象。")
    known = {path for _, paths in GROUP_PATHS for path in paths}
    unknown = set(values) - known
    if unknown:
        raise ValueError("设置包含不支持的字段，请刷新设置页后重试。")

    fields = {path: _describe_field(path) for path in values}
    nested: dict[str, dict] = {}
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
        else:  # string
            if not isinstance(value, str) or not value.strip():
                _reject(path, "不能为空。")
            elif len(value) > field.get("max_chars", _STRING_MAX_CHARS):
                _reject(path, f"长度不能超过 {field.get('max_chars', _STRING_MAX_CHARS)} 字。")
        section, _, key = path.partition(".")
        nested.setdefault(section, {})[key] = value

    seg = nested.get("segment_reply", {})
    if "min_length" in seg and "max_length" in seg and seg["min_length"] > seg["max_length"]:
        raise ValueError("「单段最小字数」不能大于「常规最大字数」。")
    return nested


def apply_values(config: dict, nested: dict) -> None:
    """把嵌套补丁写入宿主配置并持久化；写盘失败时回滚内存中的节。"""
    previous = {key: config.get(key, _MISSING) for key in nested}
    try:
        if callable(getattr(config, "save_config", None)):
            config.save_config(nested)
        else:
            config.update(nested)
    except Exception:
        for key, value in previous.items():
            if value is _MISSING:
                config.pop(key, None)
            else:
                config[key] = value
        raise


class PanelSettingsManager:
    """面板设置会话状态：revision 防陈旧 + 校验后写回。"""

    def __init__(self):
        self.revision = uuid.uuid4().hex
        self._snapshot: dict | None = None
        self._lock = asyncio.Lock()

    def describe(self, config: dict) -> dict:
        """返回分组字段与当前值；首次读取时记录快照。"""
        if self._snapshot is None:
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
