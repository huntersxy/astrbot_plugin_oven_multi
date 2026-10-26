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
"""抖音直播通知：沿用原独立插件的客户端、订阅格式和命令。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiohttp
from yarl import URL

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

LEGACY_PLUGIN = "astrbot_plugin_douyin_live_monitor"

DOUYIN_ORIGIN = "https://live.douyin.com"
ENTER_URL = f"{DOUYIN_ORIGIN}/webcast/room/web/enter/"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 房间状态查询所需的固定参数（与网页版保持一致）
ENTER_PARAMS: dict[str, str] = {
    "aid": "6383",
    "app_name": "douyin_web",
    "live_id": "1",
    "device_platform": "web",
    "language": "zh-CN",
    "enter_from": "web_live",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "122.0.0.0",
}

# 用户主页 -> 抖音号(unique_id) 解析接口。
# 注意：web_rid 就是账号的「抖音号」(unique_id)，两者可互换；而 uid / sec_uid 都
# 不是 web_rid（实测 enter 接口对 uid 返回 4001038）。用户主页链接里只有 sec_uid，
# 因此需要先经此接口把 sec_uid 换成 unique_id。
# 该接口同样需要 ttwid，未带 ttwid 时返回 HTTP 200 + 空响应体（与 enter 接口一致）。
PROFILE_URL = "https://www.douyin.com/aweme/v1/web/user/profile/other/"

PROFILE_PARAMS: dict[str, str] = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "publish_video_strategy_type": "2",
    "source": "channel_pc_web",
    "personal_center_strategy": "1",
    "version_code": "170400",
    "version_name": "17.4.0",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "122.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "platform": "PC",
}

TTWID_TTL = 1800  # ttwid 缓存有效期（秒）
LIVE_STATUS_VALUE = 2  # 房间 status == 2 表示正在直播
MAX_RETRY = 2  # 单次查询最多尝试次数（含刷新 ttwid 后的重试）

# 从用户输入中提取直播间 ID（web_rid）
#
# 注意：web_rid 并不总是纯数字。抖音账号的「抖音号」(unique_id) 允许字母 / 数字 /
# 下划线，且绝大多数账号的 web_rid 就等于它自己的抖音号（例如 xqdm88886666666）。
# 旧版本只匹配 \d{5,}，遇到这类地址会解析失败，所以这里放宽为字母数字下划线。
_RID_PATTERNS = (
    re.compile(r"live\.douyin\.com/(?!user/)([A-Za-z0-9_]{1,64})", re.I),
    re.compile(r"web_rid=([A-Za-z0-9_]{1,64})", re.I),
)

# 裸 ID（不带链接直接输入）：整体是一个 token，形如抖音号
_BARE_RID_RE = re.compile(r"^[A-Za-z0-9_]{2,64}$")

# 抖音用户主页链接里的 sec_uid（用于「只知道主页链接、不知道直播间号」的场景）
_SEC_UID_PATTERNS = (
    re.compile(r"douyin\.com/user/([A-Za-z0-9_\-]{10,128})", re.I),
    re.compile(r"sec_user_id=([A-Za-z0-9_\-]{10,128})", re.I),
    re.compile(r"sec_uid=([A-Za-z0-9_\-]{10,128})", re.I),
)

HELP_TEXT = """📺 抖音直播监控

/抖音订阅 <直播间链接/抖音号/用户主页> [备注名]
    订阅一个抖音账号的开播提醒，以下写法都支持：
    /抖音订阅 https://live.douyin.com/151135503018 老王
    /抖音订阅 https://live.douyin.com/xqdm88886666666
    /抖音订阅 xqdm88886666666 泉在摸鱼
    /抖音订阅 https://www.douyin.com/user/MS4wLjABAAAA... （自动解析主页）

/抖音取关 <直播间链接/抖音号/用户主页>  取消订阅
/抖音列表                    查看本会话的订阅与当前状态
/抖音检查                    立即检查一次（不等下一轮轮询）
/抖音Cookie <Cookie串>      配置登录态 Cookie（清空请输入 clear）
/抖音CookieFile            从插件目录 douyin_cookie.txt 读 Cookie（超长串推荐）
/抖音登录                    查看登录态状态
/抖音监控                    显示本帮助

提示：直播间地址里的 ID 可能是纯数字，也可能是带字母的「抖音号」；
      只知道用户主页链接时，插件会自动解析出对应的直播间。
      开播 / 下播时会自动推送通知，无需手动查询。Cookie 失效会自动降级为匿名监控。"""


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------


class LiveState(str, Enum):
    """房间直播状态"""

    UNKNOWN = "unknown"  # 未能判定（网络异常 / 风控）
    OFFLINE = "offline"  # 未开播
    LIVE = "live"  # 直播中
    INVALID = "invalid"  # 房间或账号无效

    @property
    def label(self) -> str:
        return {
            LiveState.UNKNOWN: "未知",
            LiveState.OFFLINE: "未开播",
            LiveState.LIVE: "直播中",
            LiveState.INVALID: "无效房间",
        }[self]

    @property
    def emoji(self) -> str:
        return {
            LiveState.UNKNOWN: "❔",
            LiveState.OFFLINE: "⚪",
            LiveState.LIVE: "🔴",
            LiveState.INVALID: "⚠️",
        }[self]


@dataclass
class RoomInfo:
    """一次房间探测的结果"""

    web_rid: str
    state: LiveState = LiveState.UNKNOWN
    title: str = ""
    nickname: str = ""
    user_count: str = ""
    cover_url: str = ""
    cover_fallback_url: str = ""
    status_code: int = 0
    error: str = ""

    def best_name(self) -> str:
        return self.nickname or self.title or f"房间 {self.web_rid}"


# --------------------------------------------------------------------------
# 抖音客户端
# --------------------------------------------------------------------------


class DouyinLiveClient:
    """负责 ttwid 生命周期管理与房间状态查询"""

    def __init__(
        self,
        timeout: float = 10.0,
        cookie_store: DouyinCookieStore | None = None,
        file_logger: FileLogger | None = None,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=max(3.0, float(timeout)))
        self._session: aiohttp.ClientSession | None = None
        self._ttwid = ""
        self._ttwid_at = 0.0
        self._lock = asyncio.Lock()
        self._cookie = cookie_store
        self._flog = file_logger
        # 运行时记录：最近一次请求是否用了登录态 Cookie（用于健康自检）
        self.used_cookie = False
        self._cookie_rejected = 0  # 连续判定"登录态失效"的次数
        self._risk_recent = False  # 最近一次请求是否被抖音风控/拒绝

    def _diag_log(self, msg: str) -> None:
        """写入插件专属文件日志（容器不输出，防止刷屏）。"""
        if self._flog is not None:
            self._flog.log(msg)

    # ---- 连接与 Cookie ----------------------------------------------------

    def _browser_headers(self) -> dict[str, str]:
        """模拟真实浏览器的完整请求头集合"""
        return {
            "User-Agent": DEFAULT_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = self._browser_headers()
            if self._cookie is not None and self._cookie.has_cookie:
                headers["Cookie"] = self._cookie.header
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers=headers,
            )
            # 手动注入解析后的 Cookie，确保 aiohttp 也能按域名回传并持续更新
            if self._cookie is not None and self._cookie.has_cookie:
                for key, value in self._cookie.cookie_dict().items():
                    self._session.cookie_jar.update_cookies(
                        {key: value}, response_url=URL(DOUYIN_ORIGIN)
                    )
        return self._session

    async def close(self) -> None:
        """释放连接池"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def reload_cookie(self) -> None:
        """Cookie 变更后重建会话，使新 Cookie 立即生效。"""
        await self.close()
        self._ttwid = ""
        self._ttwid_at = 0.0

    async def _fetch_cover_bytes(self, url: str) -> bytes | None:
        """尽力下载直播间封面，返回原始图片字节；任何失败都返回 None，绝不上抛。

        抖音封面是带签名的 CDN 地址（p3-webcast-sign.douyinpic.com），链接会过期或
        被风控，必须带浏览器 Referer / UA 请求；即使这样也可能 403，调用方要容忍
        None（跳过封面、保留文字通知）。
        """
        if not url:
            return None
        headers = {
            "User-Agent": DEFAULT_UA,
            "Referer": f"{DOUYIN_ORIGIN}/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, headers=headers
            ) as session, session.get(url) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"[抖音监控] 封面下载失败（HTTP {resp.status}），跳过封面"
                    )
                    return None
                data = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:  # noqa: BLE001
            logger.debug(f"[抖音监控] 封面下载异常，跳过封面：{exc}")
            return None
        if not data:
            return None
        return data

    async def _refresh_ttwid(self) -> str:
        """访问直播主页，获取并缓存 ttwid（Cookie 由 session 自动持有）"""
        session = await self._http()
        async with session.get(f"{DOUYIN_ORIGIN}/") as resp:
            await resp.read()

        ttwid = ""
        try:
            for cookie in session.cookie_jar:
                if cookie.key == "ttwid":
                    ttwid = cookie.value
                    break
        except Exception:  # noqa: BLE001 - 取不到 cookie 不致命
            ttwid = ""

        self._ttwid = ttwid
        self._ttwid_at = time.time()
        if ttwid:
            logger.debug("[抖音监控] ttwid 已刷新")
        else:
            logger.warning("[抖音监控] 未能获取 ttwid，房间查询可能失败")
        return ttwid

    async def ensure_ttwid(self, force: bool = False) -> str:
        """确保持有可用的 ttwid（未过期则直接复用）"""
        async with self._lock:
            if not force and self._ttwid and (time.time() - self._ttwid_at) < TTWID_TTL:
                return self._ttwid
            return await self._refresh_ttwid()

    # ---- 房间查询 --------------------------------------------------------

    async def _request_room(self, web_rid: str) -> dict[str, Any] | None:
        """请求房间接口；返回 None 表示空响应（疑似风控）"""
        session = await self._http()
        params = dict(ENTER_PARAMS)
        params["web_rid"] = web_rid
        async with session.get(
            ENTER_URL,
            params=params,
            headers={"Referer": f"{DOUYIN_ORIGIN}/{web_rid}"},
        ) as resp:
            resp.raise_for_status()
            body = await resp.read()

        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8", "ignore"))
        except ValueError:
            # 风控期间可能每轮出现，进文件避免刷屏容器
            self._diag_log("[抖音监控] 接口返回了非 JSON 内容")
            return None

    async def _request_profile(self, sec_uid: str) -> dict[str, Any] | None:
        """请求用户主页接口；返回 None 表示空响应（疑似风控）"""
        session = await self._http()
        params = dict(PROFILE_PARAMS)
        params["sec_user_id"] = sec_uid
        async with session.get(
            PROFILE_URL,
            params=params,
            headers={"Referer": "https://www.douyin.com/"},
        ) as resp:
            resp.raise_for_status()
            body = await resp.read()

        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8", "ignore"))
        except ValueError:
            self._diag_log("[抖音监控] 主页接口返回了非 JSON 内容")
            return None

    async def resolve_web_rid(self, sec_uid: str) -> tuple[str, str]:
        """把用户主页的 sec_uid 解析成直播间 web_rid（= 账号抖音号 unique_id）。

        返回 (web_rid, 错误信息)：成功时错误信息为空串，失败时 web_rid 为空串，
        错误信息可直接展示给用户。

        为什么需要这一步：直播间地址里的 ID 是 web_rid，它等于账号的「抖音号」
        (unique_id)；而用户主页链接里只有 sec_uid，两者不通用（实测 enter 接口
        对 uid 返回 4001038）。所以要先经 profile 接口把 sec_uid 换成 unique_id。
        该接口与 enter 接口一样需要 ttwid，空响应同样按风控处理并重试。
        """
        try:
            await self.ensure_ttwid()
        except Exception as exc:  # noqa: BLE001
            return "", f"初始化会话失败：{exc}"

        last_error = "解析失败"
        for attempt in range(1, MAX_RETRY + 1):
            try:
                payload = await self._request_profile(sec_uid)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                return "", f"网络异常：{exc}"
            except Exception as exc:  # noqa: BLE001 - 兜底，避免命令中断
                return "", f"请求异常：{exc}"

            if payload is not None:
                msg = str(payload.get("status_msg") or "").strip()
                if msg:
                    self._risk_recent = True
                    return "", f"接口异常：{msg}"
                code = payload.get("status_code", 0)
                if code != 0:
                    return "", f"接口返回 status_code={code}"
                user = (
                    payload.get("user")
                    if isinstance(payload.get("user"), dict)
                    else {}
                )
                if not user:
                    return "", "接口未返回用户信息，可能是链接有误或已注销"
                unique_id = str(user.get("unique_id") or "").strip()
                if not unique_id or unique_id == "0":
                    nickname = str(user.get("nickname") or "").strip()
                    who = f"「{nickname}」" if nickname else "该账号"
                    return "", (
                        f"{who}没有设置抖音号，无法定位直播间。"
                        "请改用直播间链接（https://live.douyin.com/<ID>）订阅。"
                    )
                return normalize_rid(unique_id), ""

            # 空响应：可能 ttwid 失效或风控
            self._risk_recent = True
            last_error = "空响应（疑似风控）"
            if attempt < MAX_RETRY:
                logger.debug("[抖音监控] 主页接口空响应，刷新 ttwid 后重试")
                try:
                    await self.ensure_ttwid(force=True)
                except Exception as exc:  # noqa: BLE001
                    return "", f"刷新 ttwid 失败：{exc}"

        return "", last_error

    @staticmethod
    def _parse(web_rid: str, payload: dict[str, Any]) -> RoomInfo:
        """解析接口返回体"""
        code = payload.get("status_code", 0)
        if code != 0:
            reason = ""
            data = payload.get("data")
            if isinstance(data, dict):
                reason = str(data.get("prompts") or "")
            return RoomInfo(
                web_rid=web_rid,
                state=LiveState.INVALID,
                status_code=code,
                error=reason or f"status_code={code}",
            )

        # code==0 但带错误提示（如 "url doesn't match"）：这是服务器侧请求被拒/风控，
        # 不是"房间没开播"。不能误判 OFFLINE（会污染上/下播状态机），应保持 UNKNOWN。
        msg = str(payload.get("status_msg") or "")
        if msg.strip():
            return RoomInfo(
                web_rid=web_rid,
                state=LiveState.UNKNOWN,
                status_code=code,
                error=f"接口异常：{msg}",
            )

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rooms = data.get("data") or []
        if not isinstance(rooms, list) or not rooms:
            # 房间信息为空：账号存在但当前没有直播
            user = data.get("user") if isinstance(data.get("user"), dict) else {}
            return RoomInfo(
                web_rid=web_rid,
                state=LiveState.OFFLINE,
                nickname=str(user.get("nickname") or ""),
                status_code=code,
            )

        room = rooms[0] if isinstance(rooms[0], dict) else {}
        user = data.get("user") if isinstance(data.get("user"), dict) else {}

        cover_url = ""
        cover_fallback_url = ""
        cover = room.get("cover")
        if isinstance(cover, dict):
            urls = cover.get("url_list") or []
            # 优先取带完整鉴权签名(x-signature / biz_tag)的地址；服务器匿名请求偶尔
            # 会返回缺少签名参数的裸地址，那种下载必然 403。
            signed = next(
                (u for u in urls if "x-signature=" in u or "biz_tag=" in u), None
            )
            cover_url = str(signed or (urls[0] if urls else ""))

        # 兜底封面：公开主播头像（无鉴权签名，长期有效），在封面 403 时使用
        avatar = user.get("avatar_thumb") if isinstance(user, dict) else None
        if isinstance(avatar, dict):
            avatar_urls = avatar.get("url_list") or []
            if avatar_urls:
                cover_fallback_url = str(avatar_urls[0])

        status = room.get("status")
        state = LiveState.LIVE if status == LIVE_STATUS_VALUE else LiveState.OFFLINE

        return RoomInfo(
            web_rid=web_rid,
            state=state,
            title=str(room.get("title") or ""),
            nickname=str(user.get("nickname") or ""),
            user_count=str(room.get("user_count_str") or ""),
            cover_url=cover_url,
            cover_fallback_url=cover_fallback_url,
            status_code=code,
        )

    async def fetch_room(self, web_rid: str) -> RoomInfo:
        """查询单个房间的直播状态（含 ttwid 失效自动重试）"""
        try:
            await self.ensure_ttwid()
        except Exception as exc:  # noqa: BLE001
            return RoomInfo(web_rid, LiveState.UNKNOWN, error=f"初始化会话失败：{exc}")

        last_error = ""
        for attempt in range(1, MAX_RETRY + 1):
            try:
                payload = await self._request_room(web_rid)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                return RoomInfo(web_rid, LiveState.UNKNOWN, error=f"网络异常：{exc}")
            except Exception as exc:  # noqa: BLE001 - 兜底，避免后台任务中断
                return RoomInfo(web_rid, LiveState.UNKNOWN, error=f"请求异常：{exc}")

            if payload is not None:
                # 判定"服务器侧被拒/风控"（cookie 或匿名都可能遇到）：计入失效计数，
                # 供插件用它做登录态健康自检与降级。
                msg = str((payload or {}).get("status_msg") or "")
                if msg.strip():
                    self._risk_recent = True
                return self._parse(web_rid, payload)

            # 空响应：可能 ttwid 失效或风控
            self._risk_recent = True
            last_error = "空响应（疑似风控）"
            if attempt < MAX_RETRY:
                logger.debug("[抖音监控] 收到空响应，刷新 ttwid 后重试")
                try:
                    await self.ensure_ttwid(force=True)
                except Exception as exc:  # noqa: BLE001
                    return RoomInfo(
                        web_rid, LiveState.UNKNOWN, error=f"刷新 ttwid 失败：{exc}"
                    )

        return RoomInfo(web_rid, LiveState.UNKNOWN, error=last_error)

    def reset_risk_flag(self) -> None:
        """每个轮询周期开始时调用，清空本轮的风险标记"""
        self._risk_recent = False
        self._cookie_rejected = 0

    def login_summary(self) -> str:
        """登录态概览（供 /抖音登录 命令展示）"""
        if self._cookie is None or not self._cookie.raw_present:
            return "未配置登录态 Cookie（当前为匿名 ttwid 模式）"
        if not self._cookie.has_cookie:
            # 配置了但被判定失效并抑制
            return (
                "已配置登录态 Cookie，但已被判定失效，当前降级为匿名模式；\n"
                "请用 /抖音Cookie 重新填入最新 Cookie 恢复。"
            )
        parts = [f"已配置登录态 Cookie（{len(self._cookie.cookie_dict())} 项）"]
        if self._cookie.set_at:
            with contextlib.suppress(OverflowError, OSError, ValueError):
                ts = datetime.fromtimestamp(self._cookie.set_at)
                parts.append(f"设置于 {ts:%Y-%m-%d %H:%M:%S}")
        return "；".join(parts)


# --------------------------------------------------------------------------
# 订阅存储
# --------------------------------------------------------------------------


@dataclass
class RoomSub:
    """某个会话对某个房间的订阅"""

    web_rid: str
    name: str = ""  # 用户自定义备注
    state: str = LiveState.UNKNOWN.value
    updated_at: float = 0.0

    def label(self, info: RoomInfo | None = None) -> str:
        if self.name:
            return self.name
        if info is not None:
            return info.nickname or info.title or f"房间 {self.web_rid}"
        return f"房间 {self.web_rid}"


class SubscriptionStore:
    """按会话隔离的订阅持久化（JSON 落盘）"""

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sessions: dict[str, dict[str, RoomSub]] = {}
        self._dirty = False

    # ---- 读写 ------------------------------------------------------------

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[抖音监控] 订阅文件读取失败，将重新开始：{exc}")
            return

        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, dict):
            return

        for umo, payload in sessions.items():
            rooms = payload.get("rooms") if isinstance(payload, dict) else None
            if not isinstance(rooms, dict):
                continue
            bucket: dict[str, RoomSub] = {}
            for rid, item in rooms.items():
                if not isinstance(item, dict):
                    continue
                bucket[str(rid)] = RoomSub(
                    web_rid=str(item.get("web_rid") or rid),
                    name=str(item.get("name") or ""),
                    state=str(item.get("state") or LiveState.UNKNOWN.value),
                    updated_at=float(item.get("updated_at") or 0.0),
                )
            if bucket:
                self._sessions[str(umo)] = bucket
        logger.info(f"[抖音监控] 已载入 {len(self._sessions)} 个会话的订阅")

    def save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        payload = {
            "version": self.VERSION,
            "sessions": {
                umo: {
                    "rooms": {
                        rid: {
                            "web_rid": sub.web_rid,
                            "name": sub.name,
                            "state": sub.state,
                            "updated_at": sub.updated_at,
                        }
                        for rid, sub in rooms.items()
                    }
                }
                for umo, rooms in self._sessions.items()
            },
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[抖音监控] 订阅文件保存失败：{exc}")

    # ---- 查询 ------------------------------------------------------------

    def rooms_of(self, umo: str) -> dict[str, RoomSub]:
        return self._sessions.get(umo, {})

    def get(self, umo: str, rid: str) -> RoomSub | None:
        return self._sessions.get(umo, {}).get(rid)

    def count(self, umo: str) -> int:
        return len(self._sessions.get(umo, {}))

    @property
    def session_count(self) -> int:
        """已登记的会话数量"""
        return len(self._sessions)

    def all_pairs(self) -> dict[str, list[str]]:
        """汇总所有订阅：{房间ID: [会话, ...]}，用于去重查询"""
        pairs: dict[str, list[str]] = {}
        for umo, rooms in self._sessions.items():
            for rid in rooms:
                pairs.setdefault(rid, []).append(umo)
        return pairs

    # ---- 变更 ------------------------------------------------------------

    def add(
        self, umo: str, rid: str, name: str = "", state: LiveState | None = None
    ) -> bool:
        """新增订阅；返回 False 表示已存在"""
        bucket = self._sessions.setdefault(umo, {})
        if rid in bucket:
            if name:
                bucket[rid].name = name
                self._dirty = True
            return False
        bucket[rid] = RoomSub(
            web_rid=rid,
            name=name,
            state=(state or LiveState.UNKNOWN).value,
            updated_at=time.time(),
        )
        self._dirty = True
        return True

    def remove(self, umo: str, rid: str) -> bool:
        bucket = self._sessions.get(umo)
        if not bucket or rid not in bucket:
            return False
        del bucket[rid]
        if not bucket:
            self._sessions.pop(umo, None)
        self._dirty = True
        return True

    def set_state(self, umo: str, rid: str, info: RoomInfo) -> None:
        sub = self.get(umo, rid)
        if sub is None:
            return
        sub.state = info.state.value
        sub.updated_at = time.time()
        self._dirty = True


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------


class DouyinCookieStore:
    """抖音登录态 Cookie 的持久化与解析。

    存储一份完整的 Cookie 串（如浏览器 DevTools 里 `document.cookie` 的形式），
    供 `DouyinLiveClient` 注入请求头。Cookie 有效时优先使用真实登录态，失效时
    自动降级到匿名 ttwid 流程且不中断监控。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._raw = ""
        self._parsed: dict[str, str] = {}
        self._set_at = 0.0
        self._dead_warned = False  # 已通知过"登录态失效"，避免反复轰炸
        self._suppressed = False  # 已判定失效，暂不注入 Cookie（匿名降级）

    # ---- 读写 ------------------------------------------------------------

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[抖音监控] Cookie 文件读取失败，将视为未登录：{exc}")
            return
        if isinstance(raw, dict) and raw.get("cookie"):
            self.set_raw(str(raw["cookie"]))
            # 重启后沿用上次的失效抑制状态，避免重新注入已失效 Cookie
            if raw.get("suppressed"):
                self._suppressed = True
                self._dead_warned = True

    def save(self) -> None:
        payload = {
            "cookie": self._raw,
            "set_at": self._set_at,
            "suppressed": self._suppressed,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[抖音监控] Cookie 保存失败：{exc}")

    def clear(self) -> None:
        self._raw = ""
        self._parsed = {}
        self._set_at = 0.0
        self._dead_warned = False
        self._suppressed = False
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)

    # ---- 解析与查询 ------------------------------------------------------

    def import_from_file(self, path: Path) -> str:
        """从纯文本文件读取 Cookie 串并应用。

        文件第一行（去除首尾空白）作为 Cookie 串。返回状态说明；失败返回错误描述。
        """
        if not path.exists():
            return "文件不存在"
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as exc:  # noqa: BLE001
            return f"读取失败：{exc}"
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return "文件为空"
        if not self.set_raw(lines[0]):
            return "未能从首行解析出 Cookie（需为 `key=value` 形式）"
        self.save()
        return "ok"

    def set_raw(self, raw: str) -> bool:
        """写入并解析 Cookie 串；返回是否解析出至少一个条目。"""
        raw = (raw or "").strip()
        parsed: dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip()
                if k:
                    parsed[k] = v
        if not parsed:
            return False
        self._raw = raw
        self._parsed = parsed
        self._set_at = time.time()
        self._dead_warned = False
        self._suppressed = False  # 新 Cookie 解除抑制
        return True

    @property
    def raw_present(self) -> bool:
        """是否已配置过 Cookie（即使当前被抑制，也如实返回）。"""
        return bool(self._raw)

    @property
    def has_cookie(self) -> bool:
        # 已判定失效并被抑制时，视为无 Cookie（匿名降级）
        return bool(self._raw) and not self._suppressed

    def suppress_dead(self) -> None:
        """确认登录态失效：标记为已通知并抑制后续注入（匿名降级）。"""
        self._dead_warned = True
        self._suppressed = True

    @property
    def set_at(self) -> float:
        return self._set_at

    @property
    def header(self) -> str:
        return self._raw

    @property
    def dead_warned(self) -> bool:
        return self._dead_warned

    def cookie_dict(self) -> dict[str, str]:
        """返回解析后的 Cookie 字典（供注入 aiohttp cookie_jar）。"""
        return dict(self._parsed)


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------


def normalize_rid(rid: str) -> str:
    """规范化 web_rid。

    抖音号（unique_id）大小写不敏感，统一小写既便于去重，也不影响
    https://live.douyin.com/<id> 的可用性。
    """
    return (rid or "").strip().lower()


def extract_rid(text: str) -> str:
    """从链接或纯 ID 中提取直播间 web_rid，失败返回空串。

    web_rid 可能是纯数字（早期账号），也可能是带字母 / 下划线的「抖音号」。
    两种形式 enter 接口都接受。
    """
    text = (text or "").strip()
    if not text:
        return ""
    for pattern in _RID_PATTERNS:
        match = pattern.search(text)
        if match:
            return normalize_rid(match.group(1))
    # 裸 ID：逐个 token 尝试，支持「ID 备注名」这种写法
    for token in re.split(r"[\s,，、]+", text):
        token = token.strip(" \t-—|:：\"'")
        if _BARE_RID_RE.match(token):
            return normalize_rid(token)
    return ""


def extract_sec_uid(text: str) -> str:
    """从抖音用户主页链接中提取 sec_uid，失败返回空串。

    用户主页链接里只有 sec_uid，需要用 profile 接口再换成抖音号(unique_id)。
    """
    text = (text or "").strip()
    if not text:
        return ""
    for pattern in _SEC_UID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def split_args(event: AstrMessageEvent, *commands: str) -> str:
    """取出命令后的参数部分（兼容带 / 前缀的写法）"""
    text = (event.message_str or "").strip()
    for cmd in commands:
        for prefix in ("/", "／", "!", "！", ""):
            token = prefix + cmd
            if text.startswith(token):
                return text[len(token):].strip()
    return text


class FileLogger:
    """插件专属文件日志：写入 plugin.log，按大小滚动，避免刷屏容器 stdout。

    高频诊断信息（如每轮状态判定）只进本文件；容器日志仅保留启动/停止、
    推送失败等低频关键事件。日志写入失败绝不影响插件主流程。
    """

    MAX_BYTES = 2 * 1024 * 1024  # 单文件 2MB
    BACKUP_COUNT = 3  # 保留 plugin.log.1 ~ .3

    def __init__(self, path: Path) -> None:
        self._path = path

    def _backup(self, index: int) -> Path:
        return self._path.with_name(f"{self._path.name}.{index}")

    def log(self, msg: str) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists() and self._path.stat().st_size >= self.MAX_BYTES:
                # 滚动：删 .N，其余依次升号，主文件 -> .1
                oldest = self._backup(self.BACKUP_COUNT)
                if oldest.exists():
                    oldest.unlink()
                for i in range(self.BACKUP_COUNT - 1, 0, -1):
                    src = self._backup(i)
                    if src.exists():
                        src.replace(self._backup(i + 1))
                self._path.replace(self._backup(1))
            line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:  # noqa: BLE001 - 日志失败不影响主流程
            return


# --------------------------------------------------------------------------
# 插件主体
# --------------------------------------------------------------------------


class DouyinLiveMonitor:
    def __init__(self, plugin):
        self.plugin = plugin
        self.context = plugin.context
        self._data_dir = plugin.data_dir / "douyin"
        self._import_legacy()
        self._flog = FileLogger(self._data_dir / "plugin.log")
        self.cookies = DouyinCookieStore(self._data_dir / "cookies.json")
        self.cookies.load()
        self.client = DouyinLiveClient(cookie_store=self.cookies, file_logger=self._flog)
        self.store = SubscriptionStore(self._data_dir / "subscriptions.json")
        self.store.load()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._busy = asyncio.Lock()
        self.last_check = 0.0
        self.last_error = ""

    @property
    def config(self) -> dict:
        return self.plugin.config.get("douyin", {})

    @property
    def legacy_active(self) -> bool:
        lookup = getattr(self.context, "get_registered_star", None)
        meta = lookup(LEGACY_PLUGIN) if callable(lookup) else None
        return bool(meta and meta.activated)

    def _import_legacy(self) -> None:
        marker = self._data_dir / "legacy-import.json"
        legacy = self.plugin.data_dir.parent / LEGACY_PLUGIN
        if marker.exists() or not legacy.is_dir():
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in ("subscriptions.json", "cookies.json", "douyin_cookie.txt"):
            target = self._data_dir / name
            if (legacy / name).is_file() and not target.exists():
                shutil.copy2(legacy / name, target)
                if name != "subscriptions.json":
                    target.chmod(0o600)
                copied.append(name)
        marker.write_text(json.dumps({"source": LEGACY_PLUGIN, "files": copied}), encoding="utf-8")

    async def configure(self) -> None:
        await self.stop()
        if not self.config.get("enabled", False):
            return
        self._stopping = False
        self.client = DouyinLiveClient(
            timeout=self.config.get("request_timeout", 10),
            cookie_store=self.cookies,
            file_logger=self._flog,
        )
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.client.close()
        self.store.save()

    def status(self) -> dict:
        enabled = bool(self.config.get("enabled", False))
        blocked = enabled and self.legacy_active
        running = bool(self._task and not self._task.done()) and not blocked
        return {
            "enabled": enabled, "running": running, "legacy_active": blocked,
            "sessions": self.store.session_count, "rooms": len(self.store.all_pairs()),
            "subscriptions": sum(len(rooms) for rooms in self.store._sessions.values()),
            "interval": self.interval, "last_check": self.last_check,
            "last_error": self.last_error,
            "cookie_configured": self.cookies.raw_present,
            "cookie_active": self.cookies.has_cookie,
        }

    async def dispatch(self, name: str, event: AstrMessageEvent):
        if name != "cmd_help" and not self.config.get("enabled", False):
            yield event.plain_result("抖音通知未启用，请在烤箱面板「设置 → 抖音通知」开启。")
            return
        if self.legacy_active:
            # The standalone handlers still serve commands while it is active.
            return
        if event.get_group_id() in self.plugin.config.get("blacklist_groups", []):
            return
        if event.get_sender_id() in self.plugin.config.get("blacklist_users", []):
            return
        async for result in getattr(self, name)(event):
            yield result

    # ---- 配置快捷读取 ----------------------------------------------------

    @property
    def interval(self) -> int:
        try:
            return max(30, int(self.config.get("check_interval", 60)))
        except (TypeError, ValueError):
            return 60

    @property
    def max_rooms(self) -> int:
        try:
            return max(1, int(self.config.get("max_rooms_per_session", 10)))
        except (TypeError, ValueError):
            return 10

    def _flag(self, key: str, default: bool = True) -> bool:
        value = self.config.get(key, default)
        return bool(default if value is None else value)

    # ---- 后台轮询 --------------------------------------------------------

    async def _monitor_loop(self) -> None:
        calibrated = False
        while not self._stopping:
            if not self.legacy_active:
                try:
                    self.client.reset_risk_flag()
                    await self.check_all(notify=calibrated)
                    calibrated = True
                    await self._check_login_health()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"轮询失败：{type(exc).__name__}"
                    logger.error(f"[烤箱-抖音] {self.last_error}")
            await asyncio.sleep(self.interval + random.uniform(0, 5))  # noqa: S311 - polling jitter

    async def check_all(self, notify: bool = True) -> list[RoomInfo]:
        """检查所有订阅房间；notify=True 时对状态变化推送通知"""
        async with self._busy:
            pairs = self.store.all_pairs()
            if not pairs:
                return []

            results: list[RoomInfo] = []
            for rid, umos in pairs.items():
                if self._stopping:
                    break
                info = await self.client.fetch_room(rid)
                results.append(info)
                if notify:
                    await self._apply_state(umos, info)
                else:
                    for umo in umos:
                        if info.state is not LiveState.UNKNOWN:
                            self.store.set_state(umo, rid, info)
                # 房间之间留一点间隔，降低触发风控的概率
                await asyncio.sleep(random.uniform(0.8, 2.0))  # noqa: S311 - polling jitter  # noqa: S311 - polling jitter

            self.store.save()
            self.last_check = time.time()
            self.last_error = next((info.error for info in results if info.error), "")[:200]
            return results

    async def _check_login_health(self) -> None:
        """检测登录态 Cookie 是否失效；失效则提醒并降级到匿名模式。

        判据：已配置 Cookie 的前提下，本轮所有房间探测都被抖音风控/拒绝（返回
        status_msg 或空响应）。此时 Cookie 大概率已失效，且继续硬碰会加重风控，
        故：① 记录一次性的失效提醒（在订阅会话发一条）；② 让客户端本轮不计入。
        注意：不删除 Cookie，保留给用户续期后继续用。
        """
        if self.client is None or self.client._cookie is None:
            return
        if not self.client._cookie.raw_present:
            return  # 从未配置 Cookie，无登录态可检
        if not self.client._risk_recent:
            return  # 本轮请求正常，登录态健康
        # 连续一轮都被拒，判定登录态可能失效；已提醒过则不再重复
        if self.client._cookie.dead_warned:
            return
        self.client._cookie.suppress_dead()
        self.client._cookie.save()
        await self.client.reload_cookie()
        logger.warning("[抖音监控] 检测到登录态 Cookie 可能失效，已降级为匿名模式")
        msg = (
            "⚠️ 抖音监控：检测到登录态 Cookie 可能已失效，已自动降级为匿名模式。\n"
            "监控不会中断，但可能触发风控导致状态不准。\n"
            "请用 /抖音Cookie 重新填入最新 Cookie 恢复登录态。"
        )
        umos = {umo for subscribers in self.store.all_pairs().values() for umo in subscribers}
        for umo in umos:
            await self._push(umo, MessageChain([Plain(msg)]))


    async def _apply_state(self, umos: list[str], info: RoomInfo) -> None:
        """更新状态并在必要时推送通知"""
        # 探测失败时保持原状态，避免把网络抖动误报成“下播”
        if info.state is LiveState.UNKNOWN:
            # 高频诊断只进文件日志，避免刷屏容器 stdout
            self._flog.log(
                f"[抖音监控] 状态判定[{info.web_rid}] UNKNOWN，保持原状态，本次不判定"
                + (f"（{info.error}）" if info.error else "")
            )
            return

        for umo in umos:
            sub = self.store.get(umo, info.web_rid)
            if sub is None:
                continue
            prev = sub.state
            now = info.state.value
            label = sub.label(info)
            self.store.set_state(umo, info.web_rid, info)

            if info.state is LiveState.LIVE and prev != LiveState.LIVE.value:
                if self._flag("notify_on_live"):
                    self._flog.log(
                        f"[抖音监控] 状态判定 [{info.web_rid}@{umo}] {prev}->{now} 触发开播推送"
                    )
                    await self._push(umo, await self._live_chain(label, info))
                else:
                    self._flog.log(
                        f"[抖音监控] 状态判定 [{info.web_rid}@{umo}] {prev}->{now} 开播推送已关闭"
                    )
            elif info.state is LiveState.OFFLINE and prev == LiveState.LIVE.value:
                if self._flag("notify_on_offline"):
                    self._flog.log(
                        f"[抖音监控] 状态判定 [{info.web_rid}@{umo}] {prev}->{now} 触发下播推送"
                    )
                    await self._push(umo, self._offline_chain(label, info))
                else:
                    self._flog.log(
                        f"[抖音监控] 状态判定 [{info.web_rid}@{umo}] {prev}->{now} 下播推送已关闭"
                    )
            else:
                self._flog.log(
                    f"[抖音监控] 状态判定 [{info.web_rid}@{umo}] {prev}->{now} 未改变，无推送"
                )

    # ---- 消息构造与推送 --------------------------------------------------

    async def _live_chain(self, label: str, info: RoomInfo) -> MessageChain:
        lines = [f"🔴 开播啦｜{label}"]
        if info.title:
            lines.append(f"📺 {info.title}")
        if info.user_count:
            lines.append(f"👥 在线 {info.user_count}")
        lines.append(f"🔗 {DOUYIN_ORIGIN}/{info.web_rid}")

        items: list[Any] = [Plain("\n".join(lines))]
        if self._flag("notify_with_cover"):
            data = None
            # 依次尝试：签名封面 -> 兜底头像。任一成功即带上图片，全部失败则跳过。
            for url in (info.cover_url, info.cover_fallback_url):
                if not url:
                    continue
                data = await self.client._fetch_cover_bytes(url)
                if data:
                    break
            if data:
                with contextlib.suppress(Exception):
                    items.append(Image.fromBytes(data))
        return MessageChain(items)

    def _offline_chain(self, label: str, info: RoomInfo) -> MessageChain:
        lines = [f"⚪ 已下播｜{label}"]
        if info.title:
            lines.append(f"📺 本场：{info.title}")
        lines.append(f"🔗 {DOUYIN_ORIGIN}/{info.web_rid}")
        return MessageChain([Plain("\n".join(lines))])

    async def _push(self, umo: str, chain: MessageChain) -> None:
        try:
            await self.context.send_message(umo, chain)
            self._flog.log(f"[抖音监控] 已推送通知 -> {umo}")
            logger.info(f"[抖音监控] 已推送通知 -> {umo}")
        except Exception as exc:  # noqa: BLE001
            self._flog.log(f"[抖音监控] 推送失败（{umo}）：{exc}")
            logger.error(f"[抖音监控] 推送失败（{umo}）：{exc}")

    # ---- 命令 ------------------------------------------------------------

    async def cmd_help(self, event: AstrMessageEvent):
        """查看插件帮助"""
        yield event.plain_result(HELP_TEXT)

    @staticmethod
    def _extract_name(arg: str, rid: str, sec_uid: str) -> str:
        """从参数里剥掉链接 / ID，剩下的部分当作备注名"""
        name = arg or ""
        # 先整体吃掉 key=value 形式的参数（否则会连后面的备注名一起吞掉）
        name = re.sub(r"(?:web_rid|sec_user_id|sec_uid)\s*=\s*\S+", "", name, flags=re.I)
        if rid:
            name = re.sub(re.escape(rid), "", name, flags=re.I)
        if sec_uid:
            name = name.replace(sec_uid, "")
        name = re.sub(r"https?://\S+", "", name)
        return name.strip(" \t-—|:：\"'")

    async def _resolve_target(self, arg: str) -> tuple[str, str, str]:
        """把用户输入解析成 (web_rid, 备注名, 错误信息)。

        支持三种写法：
        1. 直播间链接 https://live.douyin.com/<ID>（ID 可为数字或抖音号）
        2. 裸 ID：数字 web_rid 或带字母的抖音号
        3. 用户主页链接 https://www.douyin.com/user/<sec_uid>
           —— 主页链接里没有直播间 ID，需要调 profile 接口把 sec_uid 换成抖音号
        """
        arg = (arg or "").strip()
        if not arg:
            return "", "", ""

        rid = extract_rid(arg)
        sec_uid = ""
        if not rid:
            sec_uid = extract_sec_uid(arg)
            if sec_uid:
                rid, err = await self.client.resolve_web_rid(sec_uid)
                if not rid:
                    return "", "", err

        if not rid:
            return "", "", (
                "❌ 没能从输入中识别出直播间 ID。\n"
                "支持这些写法：\n"
                "· 直播间链接 https://live.douyin.com/xqdm88886666666\n"
                "· 抖音号（数字或带字母都行）\n"
                "· 用户主页链接 https://www.douyin.com/user/MS4wLjABAAAA..."
            )

        return rid, self._extract_name(arg, rid, sec_uid), ""

    async def cmd_subscribe(self, event: AstrMessageEvent):
        """订阅一个抖音账号的开播提醒"""
        if not self._check_admin(event):
            yield event.plain_result("⚠️ 只有管理员可以管理订阅。")
            return

        arg = split_args(event, "抖音订阅")
        if not arg:
            yield event.plain_result(
                "用法：/抖音订阅 <直播间链接/抖音号/用户主页> [备注名]\n"
                "例如：/抖音订阅 https://live.douyin.com/xqdm88886666666 泉在摸鱼"
            )
            return

        # 主页链接需要先联网解析成抖音号，这里会稍慢一点
        if not extract_rid(arg) and extract_sec_uid(arg):
            yield event.plain_result("🔍 检测到用户主页链接，正在解析抖音号…")

        rid, name, err = await self._resolve_target(arg)
        if not rid:
            yield event.plain_result(err)
            return

        umo = event.unified_msg_origin
        if self.store.count(umo) >= self.max_rooms:
            yield event.plain_result(
                f"⚠️ 本会话订阅数已达上限（{self.max_rooms} 个），请先取关一些账号。"
            )
            return

        existed = self.store.get(umo, rid) is not None
        self.store.add(umo, rid, name)

        # 订阅时立即探测一次，并把结果告诉用户
        info = await self.client.fetch_room(rid)
        if info.state is not LiveState.UNKNOWN:
            self.store.set_state(umo, rid, info)
        self.store.save()

        if info.state is LiveState.INVALID:
            detail = info.error or "房间不存在"
            if not existed:
                self.store.remove(umo, rid)
                self.store.save()
            yield event.plain_result(f"❌ 订阅失败：{detail}\n请确认直播间链接是否正确。")
            return

        display = name or info.best_name()
        head = "已更新备注" if existed else "✅ 订阅成功"
        note = ""
        if info.state is LiveState.UNKNOWN:
            note = f"\n（本次探测未成功：{info.error}，将在下一轮轮询重试）"
        yield event.plain_result(
            f"{head}：{display}\n"
            f"{info.state.emoji} 当前状态：{info.state.label}{note}\n"
            f"🔗 {DOUYIN_ORIGIN}/{rid}"
        )

    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        """取消订阅"""
        if not self._check_admin(event):
            yield event.plain_result("⚠️ 只有管理员可以管理订阅。")
            return

        arg = split_args(event, "抖音取关")
        if not arg:
            yield event.plain_result("用法：/抖音取关 <直播间链接/抖音号/用户主页>")
            return

        if not extract_rid(arg) and extract_sec_uid(arg):
            yield event.plain_result("🔍 检测到用户主页链接，正在解析抖音号…")

        rid, _name, err = await self._resolve_target(arg)
        if not rid:
            yield event.plain_result(err)
            return

        umo = event.unified_msg_origin
        if self.store.remove(umo, rid):
            self.store.save()
            yield event.plain_result(f"✅ 已取消订阅：{rid}")
        else:
            yield event.plain_result(f"⚠️ 本会话没有订阅 {rid}")

    async def cmd_list(self, event: AstrMessageEvent):
        """查看本会话的订阅"""
        umo = event.unified_msg_origin
        rooms = self.store.rooms_of(umo)
        if not rooms:
            yield event.plain_result(
                "本会话还没有任何订阅。\n用 /抖音订阅 <直播间链接/抖音号> 添加。"
            )
            return

        lines = [f"📋 本会话订阅（{len(rooms)} 个）", ""]
        for rid, sub in rooms.items():
            try:
                state = LiveState(sub.state)
            except ValueError:
                state = LiveState.UNKNOWN
            name = sub.name or f"房间 {rid}"
            lines.append(f"{state.emoji} {name}｜{state.label}")
            lines.append(f"    {DOUYIN_ORIGIN}/{rid}")
        lines.append("")
        lines.append("用 /抖音检查 立即刷新状态")
        yield event.plain_result("\n".join(lines))

    async def cmd_check(self, event: AstrMessageEvent):
        """立即检查一次本会话的订阅"""
        umo = event.unified_msg_origin
        rooms = self.store.rooms_of(umo)
        if not rooms:
            yield event.plain_result("本会话还没有任何订阅。")
            return

        if self._busy.locked():
            yield event.plain_result("⏳ 正在检查中，请稍候…")
            return

        async with self._busy:
            results = []
            for rid, sub in rooms.items():
                info = await self.client.fetch_room(rid)
                label = sub.label(info)
                if info.state is not LiveState.UNKNOWN:
                    self.store.set_state(umo, rid, info)
                results.append(f"{info.state.emoji} {label}｜{info.state.label}")
                if info.state is LiveState.LIVE and info.title:
                    results.append(f"    📺 {info.title}")
                if info.state is LiveState.UNKNOWN and info.error:
                    results.append(f"    ⚠️ {info.error}")
                await asyncio.sleep(random.uniform(0.8, 2.0))  # noqa: S311 - polling jitter  # noqa: S311 - polling jitter
            self.store.save()

        yield event.plain_result("🔍 检查完成\n\n" + "\n".join(results))

    async def cmd_set_cookie(self, event: AstrMessageEvent):
        """配置或清除登录态 Cookie"""
        if not event.is_admin():
            yield event.plain_result("⚠️ 只有管理员可以管理登录态。")
            return

        arg = split_args(event, "抖音Cookie")
        arg = (arg or "").strip()

        if arg.lower() in ("clear", "删除", "清除", "-c", "--clear", "del"):
            self.cookies.clear()
            await self.client.reload_cookie()
            yield event.plain_result("✅ 已清除登录态 Cookie，当前为匿名 ttwid 模式。")
            return

        if not arg or "=" not in arg:
            yield event.plain_result(
                "用法：/抖音Cookie <完整Cookie串>\n"
                "或   ：/抖音Cookie clear （清除）\n\n"
                "如何获取：登录抖音网页版 → F12 开发者工具 → Console 输入 "
                "`document.cookie` 复制返回值 → 粘贴到这里。"
            )
            return

        ok = self.cookies.set_raw(arg)
        if not ok:
            yield event.plain_result(
                "❌ 未能从内容中解析出 Cookie（至少需要一个 `key=value`）。请检查粘贴内容。"
            )
            return
        self.cookies.save()
        await self.client.reload_cookie()
        logger.info(f"[抖音监控] 已更新登录态 Cookie（{len(self.cookies.cookie_dict())} 项）")
        yield event.plain_result(
            f"✅ 已保存登录态 Cookie（{len(self.cookies.cookie_dict())} 项）。\n"
            "请用 /抖音检查 或等待下一轮轮询验证是否生效。"
        )

    async def cmd_cookie_from_file(self, event: AstrMessageEvent):
        """从插件数据目录的 douyin_cookie.txt 读取 Cookie（绕开 QQ 长度限制）"""
        if not event.is_admin():
            yield event.plain_result("⚠️ 只有管理员可以管理登录态。")
            return

        path = self._data_dir / "douyin_cookie.txt"
        result = self.cookies.import_from_file(path)
        if result != "ok":
            yield event.plain_result(
                f"❌ 未能从 Cookie 文件导入：{result}\n\n"
                "请把完整的 Cookie 串写入（覆盖）文件：\n"
                f"{path}\n\n"
                "用法：将 document.cookie 的内容保存成该文件后重发本命令。"
            )
            return
        await self.client.reload_cookie()
        logger.info(
            f"[抖音监控] 已从文件导入登录态 Cookie（{len(self.cookies.cookie_dict())} 项）"
        )
        yield event.plain_result(
            f"✅ 已从文件导入登录态 Cookie（{len(self.cookies.cookie_dict())} 项）。\n"
            "请用 /抖音检查 或等待下一轮轮询验证是否生效。"
        )

    async def cmd_login_status(self, event: AstrMessageEvent):
        """查看登录态状态"""
        yield event.plain_result(self.client.login_summary())

    # ---- 权限 ------------------------------------------------------------

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        """按配置校验操作权限"""
        if not self._flag("admin_only", False):
            return True
        return event.is_admin()
