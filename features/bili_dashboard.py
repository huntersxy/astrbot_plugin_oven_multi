"""Read-only projection of billion-context's native dashboard (no model calls).

Uses the public /__bili/overview and /sessions JSON contracts. Numeric meanings
come from upstream; missing measurements stay missing, rather than becoming 0.
"""

from __future__ import annotations

import asyncio
import math
import time
from urllib.parse import quote, urlencode, urlsplit

import aiohttp

SESSION_NUMBERS = (
    "requests",
    "contextTokens",
    "contextWindow",
    "tokensSaved",
    "inputTokens",
    "cachedTokens",
    "outputTokens",
    "cacheHitPct",
    "blocks",
    "foldCount",
    "netSaved",
    "grossSaved",
    "repayTokens",
    "summaryCost",
    "newContent",
    "compRepay",
    "ttlRepay",
    "missDropNew",
    "missDropComp",
    "missDropTtl",
    "modelSwitches",
    "switchMissedTokens",
    "keySwitches",
    "keySwitchMissedTokens",
    "promptSwitches",
    "promptSwitchMissedTokens",
    "projSwitches",
    "projSwitchMissedTokens",
    "coverageLostFolds",
    "coverageLostFrozenTokens",
)
OVERVIEW_NUMBERS = (
    "sessions",
    "live",
    "requests",
    "inputTokens",
    "cachedTokens",
    "outputTokens",
    "tokensSaved",
    "savedEstimated",
    "grossSavedTotal",
    "netSavedTotal",
    "repayTotal",
    "summaryCostTotal",
    "coverageLostFoldTotal",
    "coverageLostFrozenTotal",
    "missNewTotal",
    "missCompTotal",
    "missTtlTotal",
    "missInputTotal",
    "hitPct",
    "blocks",
)


def numbers(source, keys):
    return {
        key: source[key]
        for key in keys
        if isinstance(source.get(key), (int, float))
        and not isinstance(source[key], bool)
        and math.isfinite(source[key])
    }


def text(value, limit=512):
    return value[:limit] if isinstance(value, str) else ""


def session_view(source):
    """Never expose arbitrary upstream fields, credentials or handoff bodies."""
    result = numbers(source, SESSION_NUMBERS)
    result.update(
        {
            key: text(source.get(key))
            for key in (
                "id",
                "title",
                "protocol",
                "clientHint",
                "lastSeen",
                "contextTokensSource",
            )
        }
    )
    result.update({key: source.get(key) is True for key in ("live", "restored", "hasLedger")})
    try:
        result["upstream"] = urlsplit(text(source.get("upstreamOrigin"))).hostname or ""
    except ValueError:
        result["upstream"] = ""
    best = source.get("contextBest")
    if isinstance(best, dict):
        result["contextBest"] = numbers(best, ("tokens", "at")) | {"kind": text(best.get("kind"))}
    return result


class BiliDashboard:
    """Bounded requests, short result caching and explicit stale fallback."""

    def __init__(self, proxy, cfg_get):
        self.proxy = proxy
        self.cfg_get = cfg_get
        self.cache = {}
        self.lock = asyncio.Lock()

    async def _json(self, path):
        session = await self.proxy._http()
        async with session.get(
            self.proxy.url + "/__bili/" + path,
            timeout=aiohttp.ClientTimeout(total=8),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ValueError("压缩统计接口返回 HTTP " + str(response.status))
            # Detail can contain raw bodies. Bound transport and project only metadata.
            raw = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                raw.extend(chunk)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("压缩统计响应过大，请缩小会话范围或更新压缩代理。")
            import json

            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("压缩统计格式不兼容，请检查代理版本。")
            return data

    async def get(self, *, page=1, query="", session_id=""):
        if not self.cfg_get().get("enabled", False):
            return {"available": False, "reason": "上下文压缩尚未开启，请在设置中启用。"}
        page = max(1, min(int(page), 10000))
        query = text(query, 128).strip()
        session_id = text(session_id, 512).strip()
        key = (self.proxy.url, page, query, session_id)
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 10:
                return cached[1]
            try:
                if session_id:
                    if any(ord(c) < 32 for c in session_id):
                        raise ValueError("无效的会话标识。")
                    raw = await self._json("sessions/" + quote(session_id, safe="") + "/detail")
                    result = {
                        "session": session_view(raw)
                        | numbers(
                            raw,
                            (
                                "lastInputTokens",
                                "systemPromptTokens",
                                "contextUpperTokens",
                                "compressCreditTokens",
                                "retrieveCalls",
                                "retrieveHits",
                                "retrieveMisses",
                                "storedBytes",
                                "storeBytesSaved",
                            ),
                        )
                    }
                else:
                    overview, sessions = await asyncio.gather(
                        self._json("overview"),
                        self._json(
                            "sessions?"
                            + urlencode(
                                {
                                    "page": page,
                                    "pageSize": 30,
                                    "q": query,
                                }
                            )
                        ),
                    )
                    source = overview.get("overview")
                    if not isinstance(source, dict) or not isinstance(
                        sessions.get("sessions"), list
                    ):
                        raise ValueError("代理版本未提供完整总览，请更新 billion-context 后重试。")
                    protocols = []
                    for row in source.get("byProtocol", [])[:20]:
                        if isinstance(row, dict):
                            protocols.append(
                                numbers(
                                    row,
                                    (
                                        "sessions",
                                        "requests",
                                        "inputTokens",
                                        "cachedTokens",
                                        "hitPct",
                                        "savedNet",
                                        "folds",
                                        "missDropNew",
                                        "missDropComp",
                                        "missDropTtl",
                                    ),
                                )
                                | {"protocol": text(row.get("protocol"))}
                            )
                    result = {
                        "overview": numbers(source, OVERVIEW_NUMBERS)
                        | {
                            "hasFoldData": source.get("hasFoldData") is True,
                            "byProtocol": protocols,
                        },
                        "sessions": [
                            session_view(s)
                            for s in sessions["sessions"][:30]
                            if isinstance(s, dict)
                        ],
                        "total": numbers(sessions, ("total",)).get(
                            "total", len(sessions["sessions"])
                        ),
                        "page": page,
                        "page_size": 30,
                        "runtime": {
                            k: text(overview.get(k)) for k in ("version", "commit", "diskVersion")
                        }
                        | numbers(overview, ("inFlight",))
                        | {
                            "stale": overview.get("stale") is True,
                            "blindTunnels": numbers(
                                overview.get("blindTunnels") or {}, ("total",)
                            ).get("total"),
                        },
                    }
                result.update(available=True, stale=False, checked_at=time.time())
                if len(self.cache) >= 32:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = (time.monotonic(), result)
                return result
            except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "无法读取压缩统计，请检查代理连接后重试。"
                )
                if cached:
                    return cached[1] | {"stale": True, "reason": reason}
                return {"available": False, "reason": reason}
