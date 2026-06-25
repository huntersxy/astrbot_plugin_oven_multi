# Copyright (C) 2026 汐兮雨
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""System prompt 重写辅助。

仅剥离已知的平台 LTM（Long-Term Memory）注入并进行去重，
为风格注入提供干净的 system_prompt 基础。

Modified from astrbot_plugin_group_chat_plus (AGPL-3.0) by Him666233
"""

from __future__ import annotations

import re


def clean(system_prompt: str) -> str:
    """剥离已知平台 LTM 并去重，返回干净的 system_prompt。"""
    if not system_prompt:
        return ""

    # 1. 剥离已知 LTM
    cleaned, _ = _strip_known_ltm(system_prompt)

    # 2. 去重
    merged, _ = _compress_duplicate_blocks(cleaned)

    return merged or cleaned


# ──────────────────────────────────────────────
# 以下内部实现来源于 astrbot_plugin_group_chat_plus
# 按 AGPL-3.0 要求修改保留
# ──────────────────────────────────────────────

_KNOWN_LTM_PATTERNS = [
    re.compile(
        (
            r"You are now in a chatroom\. The chat history is as follows:\s*\n?"
            r"(?:\[[^\]]+/\d{2}:\d{2}:\d{2}\]:.*(?:\n(?!---\n).*)*)"
            r"(?:\n---\n\[[^\]]+/\d{2}:\d{2}:\d{2}\]:.*(?:\n(?!---\n).*)*)*"
        ),
        re.IGNORECASE,
    ),
    re.compile(
        (
            r"You are now in a chatroom\. The chat history is as follows:\s*"
            r"[\s\S]*?Now, a new message is coming:\s*`[\s\S]*?`\."
            r"\s*Please react to it\."
        ),
        re.IGNORECASE,
    ),
]


def _strip_known_ltm(text: str) -> tuple[str, bool]:
    if not text:
        return "", False
    cleaned = text
    detected = False
    for pattern in _KNOWN_LTM_PATTERNS:
        new_cleaned, count = pattern.subn("", cleaned)
        if count > 0:
            detected = True
            cleaned = new_cleaned
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, detected


def _normalize_light(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compress_duplicate_blocks(text: str) -> tuple[str, bool]:
    normalized = _normalize_light(text)
    if not normalized:
        return "", False
    parts = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    deduped_parts: list[str] = []
    seen = set()
    duplicate_suspected = False
    for part in parts:
        fingerprint = re.sub(r"\s+", " ", part).strip().lower()
        if fingerprint in seen:
            duplicate_suspected = True
            continue
        seen.add(fingerprint)
        deduped_parts.append(part)
    return "\n\n".join(deduped_parts).strip(), duplicate_suspected
