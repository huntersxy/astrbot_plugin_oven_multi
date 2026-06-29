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

# 插件基本信息
PLUGIN_NAME = "astrbot_plugin_oven_multi"
PLUGIN_VERSION = "1.34.0"
PLUGIN_AUTHOR = "汐兮雨"
PLUGIN_DESC = "插座的多功能烤箱"

# 日志前缀
LOG_PREFIX = "烤箱"

# 功能模块名称常量
FEATURE_BRACKET = "bracket_matching"
FEATURE_REPETITION = "repetition"
FEATURE_STYLE = "style_learning"
FEATURE_ACTIVE_REPLY = "active_reply"
FEATURE_REMOVE_BLANK = "remove_blank_lines"
FEATURE_THINKING = "iam_thinking"
FEATURE_IMAGE_CAPTION_CACHE = "image_caption_cache"
FEATURE_MENTION_PARSER = "mention_parser"

# 括号匹配配对表
PAIR_LIST = {
    "(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
    "<": ">", ">": "<",
    "（": "）", "）": "（",
    "［": "］", "］": "［",
    "｛": "｝", "｝": "｛",
    "〈": "〉", "〉": "〈",
    "《": "》", "》": "《",
    "【": "】", "】": "【",
    "〖": "〗", "〗": "〖",
    "〔": "〕", "〕": "〔",
    "「": "」", "」": "「",
    "『": "』", "』": "『",
    "｢": "｣", "｣": "｢",
    "\u201c": "\u201d", "\u201d": "\u201c",
    "\u2018": "\u2019", "\u2019": "\u2018",
    "‚": "‛", "‛": "‚",
    "〘": "〙", "〙": "〘",
    "〚": "〛", "〛": "〚",
}
