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
# Modified from astrbot_plugin_custome_segment_reply (MIT) by LinJohn8 —
# 分段引擎与延迟模型精简改写：去掉主动消息分支、对话历史改写与「正在输入」
# 状态（历史由 AstrBot 在装饰前已按完整原文持久化，重写会重复入库）。

import random
import re
import unicodedata

# 断句符号默认优先级：越靠前停顿越强，优先按它切
DEFAULT_SPLIT_SYMBOLS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "……",
    ".",
    "!",
    "?",
    ";",
    "\u201d",
    "、",
    "，",
    ",",
]
DEFAULT_FORCE_SPLIT_SYMBOLS = ["\n\n", "\n"]
DEFAULT_EXCLUDE_KEYWORDS = [
    "模型列表",
    "帮助",
    "help",
    "指令列表",
    "菜单",
    "功能列表",
    "已加载的插件",
]


class SegmentReply:
    """本地规则分段引擎：把长文本按强制分隔符与字数规则切成多条消息。

    与延迟计算均为纯函数（配置每次从插件配置节读取），不持有状态。
    """

    # 成对符号：顺延连续符号时遇到开括号即停，避免吞掉下一句的开头
    PAIR_SYMBOLS = {
        "(": ")",
        "[": "]",
        "{": "}",
        "（": "）",
        "【": "】",
        "《": "》",
        "〈": "〉",
        "「": "」",
        "『": "』",
        "\u201c": "\u201d",
        "\u2018": "\u2019",
        "<": ">",
    }

    # ── 配置读取 ─────────────────────────────────────────────────────────

    @staticmethod
    def _int(cfg: dict, key: str, default: int) -> int:
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(cfg: dict, key: str, default: float) -> float:
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool(cfg: dict, key: str, default: bool) -> bool:
        value = cfg.get(key, default)
        return default if value is None else bool(value)

    @classmethod
    def _symbol_list(cls, raw, default: list[str]) -> list[str]:
        """清洗符号列表：处理 ``\\n`` 转义、去空；非法输入回退默认。"""
        if not isinstance(raw, list) or not raw:
            raw = default
        result = []
        for item in raw:
            if isinstance(item, str):
                cleaned = item.replace("\\n", "\n").strip("\r")
                if cleaned:
                    result.append(cleaned)
        return result or list(default)

    def length_range(self, cfg: dict) -> tuple[int, int]:
        min_length = max(0, self._int(cfg, "min_length", 20))
        max_length = max(1, self._int(cfg, "max_length", 50))
        return min(min_length, max_length), max_length

    # ── 分段入口 ─────────────────────────────────────────────────────────

    def segment(self, text: str, cfg: dict) -> list[str]:
        """按强制分隔符预切 → 字数规则细分 → 短尾合并。"""
        pieces = self._split_by_force_symbols(text.strip(), cfg)
        segments: list[str] = []
        for piece in pieces:
            segments.extend(self._segment_by_length(piece, cfg))

        if self._bool(cfg, "merge_short_tail", True) and len(segments) >= 2:
            threshold = max(0, self._int(cfg, "short_tail_threshold", 8))
            if self._logical_len(segments[-1]) <= threshold:
                tail = segments.pop()
                segments[-1] += tail
        return segments

    def should_skip(self, text: str, cfg: dict) -> bool:
        """包含排除关键词或超过超长阈值时整段跳过。"""
        skip_longer = self._int(cfg, "skip_longer_than", 0)
        if skip_longer > 0 and len(text) > skip_longer:
            return True
        keywords = cfg.get("exclude_keywords")
        keywords = keywords if isinstance(keywords, list) else DEFAULT_EXCLUDE_KEYWORDS
        if not keywords:
            return False
        text_lower = text.lower()
        return any(
            isinstance(kw, str) and kw and kw.lower() in text_lower for kw in keywords
        )

    # ── 强制分隔符 ───────────────────────────────────────────────────────

    def _split_by_force_symbols(self, text: str, cfg: dict) -> list[str]:
        """按强制分隔符预分段；连续重复的同一符号视为一个整体。"""
        symbols = self._symbol_list(
            cfg.get("force_split_symbols"), DEFAULT_FORCE_SPLIT_SYMBOLS
        )
        keep_symbol = self._bool(cfg, "keep_symbol", True)
        pieces = [text]
        # 长符号优先，保证 "\n\n" 不被 "\n" 抢先切碎
        for symbol in sorted(set(symbols), key=len, reverse=True):
            pattern = re.compile(f"({re.escape(symbol)}+)")
            new_pieces: list[str] = []
            for piece in pieces:
                parts = pattern.split(piece)
                for i in range(0, len(parts) - 1, 2):
                    head = parts[i].strip()
                    if head:
                        new_pieces.append(head + parts[i + 1] if keep_symbol else head)
                tail = parts[-1].strip()
                if tail:
                    new_pieces.append(tail)
            pieces = new_pieces
        return pieces

    # ── 字数规则分段 ─────────────────────────────────────────────────────

    def _segment_by_length(self, text: str, cfg: dict) -> list[str]:
        """对单段文本按字数范围与断句符号迭代切分。"""
        segments: list[str] = []
        remaining = text.strip()
        keep_symbol = self._bool(cfg, "keep_symbol", True)
        protect = self._bool(cfg, "protect_paired_symbols", False)

        while remaining:
            _, max_length = self.length_range(cfg)
            if len(remaining) <= max_length:
                segments.append(remaining)
                break

            protected = self._build_protected_ranges(remaining) if protect else []
            split_idx, char_len = self._find_split_point(remaining, protected, cfg)

            if keep_symbol:
                cut = split_idx + char_len
                seg = remaining[:cut].strip()
                remaining = remaining[cut:].strip()
            else:
                seg = remaining[:split_idx].strip()
                remaining = remaining[split_idx + char_len :].strip()

            if seg:
                segments.append(seg)
        return segments

    def _find_split_point(self, text: str, protected: list[tuple], cfg: dict) -> tuple[int, int]:
        """在 text 中寻找最佳分段点，返回 (index, symbol_length)。"""
        min_length, max_length = self.length_range(cfg)

        # 1) 优先在 [min_length, max_length) 内反向找
        found = self._rfind_symbol(text, min_length, max_length, protected, cfg)
        if found:
            return found

        # 2) 允许超出时，在 [max_length, hard_max_limit) 内正向找；实在没有就硬切
        if self._bool(cfg, "allow_exceed_max", True):
            hard_limit = self._int(cfg, "hard_max_limit", 100)
            if hard_limit < max_length:
                hard_limit = max_length + 20
            search_end = min(len(text), hard_limit)
            found = self._find_symbol_forward(text, max_length, search_end, protected, cfg)
            if found:
                return found
            return search_end, 0

        # 3) 不允许超出时，在 [0, min_length) 内反向找
        found = self._rfind_symbol(text, 0, min_length, protected, cfg)
        if found:
            return found
        return max_length, 0

    def _split_symbols(self, cfg: dict) -> list[str]:
        return self._symbol_list(cfg.get("split_symbols"), DEFAULT_SPLIT_SYMBOLS)

    def _rfind_symbol(
        self, text: str, start: int, end: int, protected: list[tuple], cfg: dict
    ) -> tuple[int, int] | None:
        """在 text[start:end] 内按优先级反向查找分段符号。"""
        for symbol in self._split_symbols(cfg):
            idx = text.rfind(symbol, start, end)
            if idx != -1 and not self._in_protected_range(idx, protected):
                return idx, self._split_char_len(text, idx, symbol, cfg)
        return None

    def _find_symbol_forward(
        self, text: str, start: int, end: int, protected: list[tuple], cfg: dict
    ) -> tuple[int, int] | None:
        """在 text[start:end] 内按位置正向查找第一个分段符号。"""
        symbols = self._split_symbols(cfg)
        keep_symbol = self._bool(cfg, "keep_symbol", True)
        extend = self._bool(cfg, "extend_to_trailing_symbols", True)
        for i in range(start, end):
            for symbol in symbols:
                if text.startswith(symbol, i) and not self._in_protected_range(i, protected):
                    char_len = len(symbol) if not extend else self._split_char_len(
                        text, i, symbol, cfg
                    )
                    # 丢弃标点模式下硬切点不得越过符号本身
                    if not keep_symbol:
                        char_len = max(char_len, len(symbol))
                    return i, char_len
        return None

    # ── 符号处理 ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_symbol_char(ch: str) -> bool:
        """标点(P)或符号(S)类 Unicode 字符。"""
        return unicodedata.category(ch)[0] in ("P", "S")

    @classmethod
    def _logical_len(cls, text: str) -> int:
        """逻辑长度：连续重复的同一符号字符计为 1。"""
        length = 0
        i = 0
        while i < len(text):
            ch = text[i]
            if cls._is_symbol_char(ch):
                while i + 1 < len(text) and text[i + 1] == ch:
                    i += 1
            length += 1
            i += 1
        return length

    def _split_char_len(self, text: str, idx: int, symbol: str, cfg: dict) -> int:
        """分隔符应吞掉的总长度（含顺延的连续符号）。"""
        total = len(symbol)
        if not self._bool(cfg, "extend_to_trailing_symbols", True):
            return total
        # 空白类分隔符不顺延，避免误吞下一段开头
        if any(ch.isspace() for ch in symbol):
            return total
        while idx + total < len(text) and self._is_symbol_char(text[idx + total]):
            if text[idx + total] in self.PAIR_SYMBOLS:
                break  # 开括号属于下一句（如「！（/抱着终端）」）
            total += 1
        return total

    @classmethod
    def _build_protected_ranges(cls, text: str) -> list[tuple]:
        """成对符号的保护区间（开→闭之间不分段）。"""
        ranges, stack = [], []
        for idx, ch in enumerate(text):
            if ch in cls.PAIR_SYMBOLS:
                stack.append((idx, cls.PAIR_SYMBOLS[ch]))
            elif stack and ch == stack[-1][1]:
                open_idx, _ = stack.pop()
                ranges.append((open_idx, idx))
        return ranges

    @staticmethod
    def _in_protected_range(split_idx: int, protected: list[tuple]) -> bool:
        return any(start < split_idx < end for start, end in protected)

    # ── 延迟计算 ─────────────────────────────────────────────────────────

    @staticmethod
    def _gauss_clamped(min_v: float, max_v: float) -> float:
        """正态随机，均值取区间中点，结果钳制在区间内。"""
        if min_v >= max_v:
            return min_v
        mean = (min_v + max_v) / 2
        std = max((max_v - min_v) / 6, 1e-6)
        return max(min_v, min(max_v, random.gauss(mean, std)))

    def delay(self, prev_seg: str, curr_seg: str, cfg: dict) -> float:
        """当前分段发送前的等待秒数。"""
        mode = str(cfg.get("delay_type", "random") or "random").strip().lower()
        if mode == "fixed":
            return max(0.0, self._float(cfg, "fixed_delay_seconds", 1.0))
        if mode == "smart":
            return self._smart_delay(prev_seg, curr_seg)

        range_ = cfg.get("random_delay_range")
        range_ = range_ if isinstance(range_, list) and len(range_) >= 2 else [1, 3]
        min_v = self._safe_float(range_[0], 1.0)
        max_v = self._safe_float(range_[1], 3.0)
        if min_v > max_v:
            min_v, max_v = max_v, min_v
        return self._gauss_clamped(min_v, max_v)

    @staticmethod
    def _safe_float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _smart_delay(self, prev_seg: str, curr_seg: str) -> float:
        """智能延迟：按字符数的打字间隔，叠加符号修正。"""
        per_char_ms = max(10.0, random.gauss(80.0, 30.0))
        base = per_char_ms * len(curr_seg) / 1000.0

        # 感叹号加速：语气急促
        exclam_count = curr_seg.count("!") + curr_seg.count("！")
        exclam_reduce = sum(self._gauss_clamped(0.1, 0.3) for _ in range(exclam_count))

        prev_tail = prev_seg.rstrip()[-1] if prev_seg.rstrip() else ""
        # 上一句是疑问句：模拟等待回应的停顿
        question_add = self._gauss_clamped(0.2, 0.5) if prev_tail in "?？" else 0.0
        # 上一句断在逗号：同一句话的延续，加快衔接
        comma_reduce = self._gauss_clamped(0.2, 0.5) if prev_tail in ",，、" else 0.0

        # 省略号/破折号：句尾停顿或欲言又止，减速
        pause_pattern = r"(?:\.{3,}|。{3,}|…{2,}|-{2,}|—{2,}|－{2,})"
        prev_tail_pauses = len(re.findall(pause_pattern + r"\s*$", prev_seg))
        all_curr_pauses = re.findall(pause_pattern, curr_seg)
        curr_tail_pauses = len(re.findall(pause_pattern + r"\s*$", curr_seg))
        pause_count = prev_tail_pauses + len(all_curr_pauses) - curr_tail_pauses
        pause_add = sum(self._gauss_clamped(0.4, 0.8) for _ in range(pause_count))

        return max(0.0, base - exclam_reduce - comma_reduce + question_add + pause_add)
