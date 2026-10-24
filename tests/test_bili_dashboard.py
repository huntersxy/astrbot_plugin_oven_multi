"""Read real HTTP contracts without model calls, chat bodies or credentials."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

spec = importlib.util.spec_from_file_location(
    "oven_dashboard_test", Path(__file__).parents[1] / "features/bili_dashboard.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@pytest.mark.asyncio
async def test_native_overview_paging_detail_projection_and_short_cache():
    seen = []
    session = {
        "id": "group/session", "title": "会话", "protocol": "openai", "live": True,
        "contextBest": {"tokens": 123, "kind": "usage", "at": 1000},
        "inputTokens": 1000, "cachedTokens": 0, "cacheHitPct": 0,
        "tokensSaved": 500, "netSaved": -30, "foldCount": 2,
        "upstreamOrigin": "https://secret-user:secret-key@example.org/private?token=secret",
        "handoffHtml": "PRIVATE CHAT", "apiKey": "SECRET", "lastInputTokens": 140,
    }

    async def handle(request):
        seen.append(request.path_qs)
        if request.path.endswith("overview"):
            return web.json_response({"version": "0.1.206", "inFlight": 0, "overview": {
                "sessions": 1, "hitPct": 0, "netSavedTotal": -30, "hasFoldData": True,
                "byProtocol": [{"protocol": "openai", "sessions": 1, "savedNet": -30}],
                "unknownCredential": "SECRET",
            }, "config": {"apiKey": "SECRET"}})
        if request.path.endswith("detail"):
            return web.json_response(session)
        assert request.query["page"] == "2" and request.query["pageSize"] == "30"
        assert request.query["q"] == "中文 & /"
        return web.json_response({"total": 31, "sessions": [session]})

    app = web.Application()
    app.router.add_get("/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as http:
        proxy = SimpleNamespace(url=f"http://127.0.0.1:{port}", _http=AsyncMock(return_value=http))
        dashboard = module.BiliDashboard(proxy, lambda: {"enabled": True})
        try:
            data = await dashboard.get(page=2, query="中文 & /")
            assert data["available"] and data["page"] == 2 and data["total"] == 31
            assert data["overview"]["hitPct"] == 0
            assert "outputTokens" not in data["overview"]
            assert data["sessions"][0]["upstream"] == "example.org"
            assert data["sessions"][0]["netSaved"] == -30
            assert "SECRET" not in json.dumps(data) and "secret" not in json.dumps(data)
            assert "PRIVATE CHAT" not in json.dumps(data)
            again = await dashboard.get(page=2, query="中文 & /")
            assert again == data and len(seen) == 2
            detail = await dashboard.get(session_id="group/session")
            assert detail["session"]["lastInputTokens"] == 140
            assert detail["session"]["contextBest"]["tokens"] == 123
            assert "PRIVATE CHAT" not in json.dumps(detail)
        finally:
            await runner.cleanup()


@pytest.mark.asyncio
async def test_disabled_empty_incompatible_and_stale_results():
    cfg = {"enabled": False}
    proxy = SimpleNamespace(url="http://unused", _http=AsyncMock())
    dashboard = module.BiliDashboard(proxy, lambda: cfg)
    assert not (await dashboard.get())["available"]
    proxy._http.assert_not_called()
    cfg["enabled"] = True
    dashboard._json = AsyncMock(side_effect=[{"overview": {}}, {"sessions": []}])
    data = await dashboard.get()
    assert data["available"] and data["sessions"] == []
    assert "netSavedTotal" not in data["overview"]
    key = next(iter(dashboard.cache))
    dashboard.cache[key] = (0, data)
    dashboard._json = AsyncMock(side_effect=TimeoutError())
    stale = await dashboard.get()
    assert stale["available"] and stale["stale"] and stale["checked_at"] == data["checked_at"]
    proxy.url = "http://new-host"
    unavailable = await dashboard.get()
    assert not unavailable["available"]
    dashboard._json = AsyncMock(return_value={"unsupported": True})
    assert not (await dashboard.get())["available"]


@pytest.mark.asyncio
async def test_http_errors_do_not_follow_redirects_or_leak_raw_response():
    async def rejected(_):
        return web.Response(status=403, text="PRIVATE KEY")

    app = web.Application()
    app.router.add_get("/{tail:.*}", rejected)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    async with aiohttp.ClientSession() as http:
        proxy = SimpleNamespace(
            url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}",
            _http=AsyncMock(return_value=http),
        )
        try:
            result = await module.BiliDashboard(proxy, lambda: {"enabled": True}).get()
            assert not result["available"] and "403" in result["reason"]
            assert "PRIVATE" not in json.dumps(result)
        finally:
            await runner.cleanup()
