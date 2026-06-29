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
# Modified from astrbot_plugin_astrbot_enhance_mode (AGPL-3.0) by 阿汐
#   — mention 标签解析与活跃发言人追踪

import datetime
import json
import re
import time
from collections import OrderedDict
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger

MENTION_RE = re.compile(
    r"""<mention\s+id\s*=\s*['"]([^'"]+)['"]\s*/?>""",
    re.IGNORECASE,
)
MENTION_CLOSE_RE = re.compile(r"</mention\s*>", re.IGNORECASE)

# ── 活跃发言人追踪 ──


class ActiveSpeakersTracker:
    """追踪每个会话中最近发言的用户，供 LLM 选择 @ 谁。"""

    def __init__(self, max_speakers: int = 50, data_dir: Path | str | None = None):
        self.max_speakers = max_speakers
        self._speakers: dict[str, OrderedDict[str, dict]] = {}
        self._data_file = Path(data_dir) / "speakers.json" if data_dir else None
        self._dirty = False
        self._last_save = 0.0
        self._load()

    def _load(self):
        """从磁盘加载发言人数据。"""
        if not self._data_file or not self._data_file.exists():
            return
        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            for origin, users in raw.items():
                od = OrderedDict()
                for uid, info in users.items():
                    od[uid] = info
                self._speakers[origin] = od
            logger.debug(
                f"[烤箱-@功能] 已加载发言人数据 | 来源数: {len(self._speakers)}"
            )
        except Exception as e:
            logger.warning(f"[烤箱-@功能] 加载发言人数据失败: {e}")

    def _save(self, force: bool = False):
        """保存发言人数据到磁盘，5 秒内最多写一次。"""
        if not self._dirty or not self._data_file:
            return
        now = time.time()
        if not force and now - self._last_save < 5:
            return
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for origin, users in self._speakers.items():
                data[origin] = dict(users)
            self._data_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._dirty = False
            self._last_save = now
        except Exception as e:
            logger.warning(f"[烤箱-@功能] 保存发言人数据失败: {e}")

    def _get_or_create(self, origin: str) -> OrderedDict:
        if origin not in self._speakers:
            self._speakers[origin] = OrderedDict()
        return self._speakers[origin]

    def record(self, origin: str, user_id: str, nickname: str) -> None:
        """记录一条发言，将发言人移到最近位置。"""
        speakers = self._get_or_create(origin)
        if user_id in speakers:
            speakers.move_to_end(user_id)
        else:
            if len(speakers) >= self.max_speakers:
                speakers.popitem(last=False)
        speakers[user_id] = {
            "nickname": nickname,
            "last_active": datetime.datetime.now().strftime("%H:%M:%S"),
        }
        self._dirty = True
        self._save()

    def build_speakers_prompt(self, origin: str) -> str:
        """构建活跃发言人列表文本，追加到 LLM prompt 中。"""
        speakers = self._speakers.get(origin)
        if not speakers:
            return ""

        lines = []
        for uid, info in speakers.items():
            lines.append(
                f"  - {info['nickname']} (ID: {uid}) [last: {info['last_active']}]"
            )

        return (
            "\n\n## Active Speakers\n"
            "Below are the recently active users in this group. "
            'If you want to @mention someone in your reply, use <mention id="ID"/>.\n'
            "For example: <mention id=\"123456\"> Hello!\n"
            "You can mention multiple users. Do NOT output </mention>.\n"
            + "\n".join(lines)
        )

    def cleanup(self, origin: str) -> None:
        self._speakers.pop(origin, None)


# ── Mention 标签解析 ──


def transform_mention_in_chain(chain: list) -> list | None:
    """将 LLM 输出中的 <mention> 标签转换为 At 组件。

    Returns:
        转换后的新 chain，若无 mention 标签则返回 None。
    """
    has_mention = any(
        isinstance(comp, Comp.Plain) and MENTION_RE.search(comp.text)
        for comp in chain
    )
    if not has_mention:
        return None

    new_chain = []
    for comp in chain:
        if not isinstance(comp, Comp.Plain):
            new_chain.append(comp)
            continue

        text = comp.text
        if MENTION_RE.search(text):
            parts = MENTION_RE.split(text)
            for idx, part in enumerate(parts):
                if idx % 2 == 0:
                    cleaned = MENTION_CLOSE_RE.sub("", part)
                    if cleaned.strip():
                        new_chain.append(Comp.Plain(text=cleaned))
                else:
                    new_chain.append(Comp.At(qq=part))
        else:
            cleaned = MENTION_CLOSE_RE.sub("", text)
            if cleaned.strip():
                new_chain.append(Comp.Plain(text=cleaned))

    return new_chain
