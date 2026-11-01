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
"""bili 代理生命周期：懒人获取链（探活复用→查找→自动安装→拉起）、健康守护与配置播种。

不导入 astrbot 模块（离线可测）；宿主配置经 ``cfg_get`` 回调每次现场读取，
子进程与可执行查找走模块级函数（``_exec_async`` / ``_which``），便于测试替换。
托管安装目录的版本检查与闲置升级也在此完成（关闭 bili 自更新，避免误写全局）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

# 状态机（面板/命令展示）
ST_DISABLED = "disabled"  # 功能未启用
ST_REUSED = "reused"  # managed：端口上已有健康 bili，直接复用
ST_EXTERNAL_OK = "external_ok"  # external：外部代理健康
ST_EXTERNAL_DOWN = "external_down"  # external：外部代理不可达
ST_SPAWNED = "spawned"  # managed：已拉起自有进程且健康
ST_DEGRADED = "degraded"  # managed：守护重启连续失败
ST_NO_NODE = "no_node"  # 宿主机缺 Node 或版本 < 20
ST_INSTALL_FAILED = "install_failed"  # 自动安装失败
ST_INSTALLING = "installing"
ST_UPDATING = "updating"
ST_STARTING = "starting"
ST_SPAWN_FAILED = "spawn_failed"  # 拉起后健康检查超时
ST_STOPPED = "stopped"  # 已停止（未启用/卸载）

_NPM_PACKAGE = "billion-context"
_NPM_REGISTRY_LATEST = f"https://registry.npmjs.org/{_NPM_PACKAGE}/latest"
_MIN_NODE_MAJOR = 20
_HEALTH_TIMEOUT_SEC = 2.0
_STARTUP_WAIT_SEC = 20.0
_MAX_CONSECUTIVE_FAILURES = 3
_DEFAULT_UPDATE_CHECK_SEC = 6 * 3600
_PROMPT_CACHE_ROUTING = frozenset({"auto", "enabled", "disabled"})
_IDLE_POLL_SEC = 2.0
_MANUAL_IDLE_WAIT_SEC = 180.0


def _which(name: str) -> str | None:
    """PATH 上解析可执行文件；模块级函数便于测试替换。"""
    return shutil.which(name)


async def _exec_async(args: list[str], **kwargs) -> asyncio.subprocess.Process:
    """跨平台执行（Windows 的 .cmd/.bat 经 cmd /c）；模块级函数便于测试替换。"""
    argv = list(args)
    if sys.platform == "win32" and argv[0].lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c", *argv]
    return await asyncio.create_subprocess_exec(*argv, **kwargs)  # noqa: S603


def _parse_node_version(raw: str) -> int:
    """解析 `v22.11.0` 形式的版本串为主版本号；非法返回 0。"""
    digits = raw.strip().lstrip("vV").split(".")[0]
    try:
        return int(digits)
    except ValueError:
        return 0


def _parse_semver(raw: str) -> tuple[tuple[int, int, int], str]:
    """Parse ``1.2.3`` / ``v1.2.3-pr.1`` into ((major, minor, patch), prerelease)."""
    text = str(raw or "").strip().lstrip("vV")
    core, _, pre = text.partition("-")
    parts: list[int] = []
    for bit in core.split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2]), pre


def is_version_newer(candidate: str, current: str) -> bool:
    """Return True when ``candidate`` is strictly newer than ``current``."""
    if not candidate or not current:
        return bool(candidate) and not current
    c_core, c_pre = _parse_semver(candidate)
    u_core, u_pre = _parse_semver(current)
    if c_core != u_core:
        return c_core > u_core
    if not c_pre and u_pre:
        return True
    if c_pre and not u_pre:
        return False
    return c_pre > u_pre


class BiliProxyManager:
    """懒人模式代理管家：确保 ``http://host:port/__bili/health`` 可用并保持存活。"""

    def __init__(self, cfg_get: Callable[[], dict], data_dir: Path):
        self._cfg_get = cfg_get
        self.data_dir = Path(data_dir)
        self.bili_config_path = self.data_dir / "billion-context.json"
        self.runtime_dir = self.data_dir / "billion-runtime"
        self.state = ST_STOPPED
        self.detail = ""
        # 会话标识一致性：ok=已验证一致；mismatch=明确不一致；unknown=无法确认
        # （自动接管的硬前提，未知同样阻断——按 AstrBot 会话隔离压缩状态不可靠）
        self.session_header_state = "unknown"
        self.session_header_detail = ""
        self._proc: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None
        self._session = None
        self._failures = 0
        self._acquiring = False
        self._installed_version = ""
        self._latest_version = ""
        self._update_available = False
        self._update_checking = False
        self._update_pending_restart = False
        self._last_update_check = 0.0
        self._last_auto_update_attempt = 0.0
        self._last_update_message = ""
        self._refresh_installed_version()

    # ── 基本信息 ─────────────────────────────────────────────────────────

    @property
    def url(self) -> str:
        cfg = self._cfg_get()
        host = str(cfg.get("host") or "127.0.0.1")
        port = int(cfg.get("port") or 8787)
        return f"http://{host}:{port}"

    @property
    def healthy(self) -> bool:
        return self.state in (ST_REUSED, ST_EXTERNAL_OK, ST_SPAWNED)

    @property
    def owned_process(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def status(self) -> dict:
        cfg = self._cfg_get()
        return {
            "state": self.state,
            "healthy": self.healthy,
            "url": self.url,
            "detail": self.detail,
            "busy": self._acquiring,
            "mode": str(cfg.get("mode") or "managed"),
            "session_header_state": self.session_header_state,
            "session_header_detail": self.session_header_detail,
            "update": self.update_status(),
        }

    def update_status(self) -> dict:
        """Panel-facing update projection for the managed runtime package."""
        cfg = self._cfg_get()
        return {
            "supported": self._update_supported(),
            "installed": self._installed_version,
            "latest": self._latest_version,
            "available": self._update_available,
            "checking": self._update_checking,
            "updating": self.state == ST_UPDATING,
            "pending_restart": self._update_pending_restart,
            "last_check_at": self._last_update_check or None,
            "last_message": self._last_update_message,
            "auto_update": bool(cfg.get("auto_update", True)),
        }

    def _package_dir(self) -> Path:
        return self.runtime_dir / "node_modules" / _NPM_PACKAGE

    def _runtime_entry(self) -> Path | None:
        entry = self._package_dir() / "dist" / "index.js"
        return entry if entry.is_file() else None

    def _update_supported(self) -> bool:
        cfg = self._cfg_get()
        if str(cfg.get("mode") or "managed") != "managed":
            return False
        name = str(cfg.get("executable") or "bili").strip() or "bili"
        return name == "bili" and self._runtime_entry() is not None

    def _refresh_installed_version(self) -> str:
        path = self._package_dir() / "package.json"
        version = ""
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    version = str(data.get("version") or "").strip()
            except (OSError, ValueError):
                version = ""
        self._installed_version = version
        if self._latest_version:
            self._update_available = is_version_newer(
                self._latest_version, self._installed_version
            )
        return version

    # ── 健康探测 ─────────────────────────────────────────────────────────

    async def _http(self):
        if self._session is None or self._session.closed:
            import aiohttp

            self._session = aiohttp.ClientSession()
        return self._session

    async def probe_health(self) -> tuple[bool, str]:
        """GET /__bili/health；返回 (是否健康, 说明)。"""
        try:
            import aiohttp

            session = await self._http()
            async with session.get(
                f"{self.url}/__bili/health",
                timeout=aiohttp.ClientTimeout(total=_HEALTH_TIMEOUT_SEC),
            ) as resp:
                if resp.status == 200 and (await resp.json()).get("ok") is True:
                    return True, ""
                return False, f"健康端点返回 {resp.status}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}"

    # ── 会话标识验证（自动接管的硬前提）──────────────────────────────────

    @property
    def _expected_session_header(self) -> str:
        return str(self._cfg_get().get("session_header") or "x-astrbot-conversation-id")

    async def _fetch_admin_config(self) -> dict | None:
        """GET /__bili/config，返回解析后的顶层 dict；不可用/无字段返回 None。"""
        try:
            import aiohttp

            session = await self._http()
            async with session.get(
                f"{self.url}/__bili/config",
                timeout=aiohttp.ClientTimeout(total=_HEALTH_TIMEOUT_SEC),
            ) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(await resp.text())
                return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    async def verify_session_header(self) -> None:
        """验证实际运行实例的会话标识：管理端点优先，文件兜底仅限本进程拉起。

        端点返回运行实例的配置视图（不同版本字段可能增减，防御式解析顶层
        sessionHeader）。复用/外部实例不读本插件的播种文件，端点读不到时
        一律视为未知并阻断自动接管；仅自拉实例（``--config`` 指向播种文件）
        允许以文件为准。
        """
        expected = self._expected_session_header
        if expected.lower() != "x-astrbot-conversation-id":
            self.session_header_state = "mismatch"
            self.session_header_detail = (
                "AstrBot 使用 x-astrbot-conversation-id，请将会话头恢复为该名称。"
            )
            return
        data = await self._fetch_admin_config()
        # The upstream management API exposes file contents in `raw`, not top-level options.
        if data is not None and "sessionHeader" not in data and isinstance(data.get("raw"), str):
            try:
                raw_config = json.loads(data["raw"])
                if isinstance(raw_config, dict):
                    data = raw_config
            except ValueError:
                data = None
        if data is not None and "sessionHeader" in data:
            current = data.get("sessionHeader")
            if current == expected:
                self.session_header_state = "ok"
                self.session_header_detail = ""
            else:
                self.session_header_state = "mismatch"
                self.session_header_detail = (
                    f"运行实例 sessionHeader={current!r} 与预期 {expected!r} 不一致。"
                )
            return
        if self.owned_process:
            # 自拉实例读的就是播种文件
            header_ok, detail = self.seed_or_validate_config()
            self.session_header_state = "ok" if header_ok else "mismatch"
            self.session_header_detail = "" if header_ok else detail
            return
        self.session_header_state = "unknown"
        self.session_header_detail = (
            "无法确认运行实例的 sessionHeader（管理端点不可用），"
            "请在该 bili 实例配置中设置后重试，或手动接管。"
        )

    # ── bili 配置播种 ────────────────────────────────────────────────────

    def resolved_prompt_cache_routing(self) -> str:
        """Normalize the panel setting for billion-context promptCache.routing."""
        raw = str(self._cfg_get().get("prompt_cache_routing") or "auto").strip().lower()
        return raw if raw in _PROMPT_CACHE_ROUTING else "auto"

    def managed_quick_config(self) -> dict:
        """Panel quick-config values mapped onto billion-context.json keys."""
        cfg = self._cfg_get()
        absorb = cfg.get("absorb", False)
        return {
            "debug": bool(cfg.get("debug", False)),
            "passthrough": bool(cfg.get("passthrough", False)),
            "imageRescale": bool(cfg.get("image_rescale", True)),
            "promptCache": {"routing": self.resolved_prompt_cache_routing()},
            "compress": {"absorb": {"enabled": bool(absorb)}},
        }

    @staticmethod
    def _nested_set(data: dict, path: tuple[str, ...], value) -> bool:
        """Set a nested value; return True when the stored value changed."""
        cursor = data
        for key in path[:-1]:
            child = cursor.get(key)
            if not isinstance(child, dict):
                child = {}
                cursor[key] = child
            cursor = child
        leaf = path[-1]
        if cursor.get(leaf) == value:
            return False
        cursor[leaf] = value
        return True

    def sync_managed_config(self) -> tuple[bool, str]:
        """Merge panel quick-config into managed ``billion-context.json``.

        External proxies keep their own config; managed installs own this file and
        bili hot-reads it. Only the quick-config keys are touched; ``providers``
        and unrelated blocks are preserved. Missing files get a minimal seed.
        """
        if str(self._cfg_get().get("mode") or "managed") != "managed":
            return True, ""
        path = self.bili_config_path
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                return False, "无法解析 bili 配置文件以同步快速配置。"
            if not isinstance(loaded, dict):
                return False, "bili 配置文件不是对象，无法同步快速配置。"
            data = loaded
        else:
            data = {
                "sessionHeader": self._expected_session_header,
                "providers": {},
                "autoUpdate": False,
            }
        wanted = self.managed_quick_config()
        changed = False
        for key in ("debug", "passthrough", "imageRescale"):
            if self._nested_set(data, (key,), wanted[key]):
                changed = True
        if self._nested_set(
            data, ("promptCache", "routing"), wanted["promptCache"]["routing"]
        ):
            changed = True
        if self._nested_set(
            data,
            ("compress", "absorb", "enabled"),
            wanted["compress"]["absorb"]["enabled"],
        ):
            changed = True
        if not changed:
            return True, ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return False, "写入 bili 快速配置失败，请检查插件数据目录权限。"
        return True, "已同步 bili 快速配置"

    def sync_prompt_cache_routing(self) -> tuple[bool, str]:
        """Backward-compatible alias for ``sync_managed_config``."""
        return self.sync_managed_config()

    def seed_or_validate_config(self) -> tuple[bool, str]:
        """托管配置文件播种：缺失则写入最小配置，绝不覆盖已存在文件。

        Returns:
            (sessionHeader 是否符合预期, 说明)。
        """
        expected = str(self._cfg_get().get("session_header") or "x-astrbot-conversation-id")
        if self.bili_config_path.exists():
            try:
                data = json.loads(self.bili_config_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                return False, "bili 配置文件无法解析，请检查后删除重建或手改。"
            if not isinstance(data, dict):
                return False, "bili 配置文件不是对象，请检查。"
            current = data.get("sessionHeader")
            if current == expected:
                ok, detail = self.sync_managed_config()
                return ok, detail
            return (
                False,
                f"bili 配置的 sessionHeader={current!r} 与预期 {expected!r} 不一致，"
                "请在面板「上下文压缩」的会话头名称与 bili 配置之间统一。",
            )
        # Managed installs own npm updates; keep bili's global self-updater off.
        seed = {
            "sessionHeader": expected,
            "providers": {},
            "autoUpdate": False,
            **self.managed_quick_config(),
        }
        self.bili_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.bili_config_path.write_text(
            json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True, "已播种 bili 配置。"

    # ── 懒人获取链 ───────────────────────────────────────────────────────

    def _find_executable(self) -> str | None:
        cfg = self._cfg_get()
        name = str(cfg.get("executable") or "bili").strip() or "bili"
        if name == "bili":
            entry = self._runtime_entry()
            if entry is not None:
                return str(entry)
        return _which(name) or (name if Path(name).exists() else None)

    async def _node_major(self) -> int:
        node = _which("node")
        if not node:
            return 0
        try:
            proc = await _exec_async([node, "--version"], stdout=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return _parse_node_version(out.decode(errors="ignore"))
        except Exception:  # noqa: BLE001
            return 0

    async def _npm_install(self, *, package: str | None = None) -> tuple[bool, str]:
        npm = _which("npm")
        if not npm:
            return False, "未找到 npm，无法自动安装 billion-context。"
        spec = package or _NPM_PACKAGE
        proc = None
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            proc = await _exec_async(
                [
                    npm, "install", "--prefix", str(self.runtime_dir),
                    "--no-audit", "--no-fund", "--no-package-lock", spec,
                ],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0:
                self._refresh_installed_version()
                return True, ""
            output = out.decode(errors="ignore")
            for code, reason in (
                ("EACCES", "插件数据目录没有写入权限"),
                ("ENOSPC", "磁盘空间不足"),
                ("ENOTFOUND", "无法解析 npm 仓库域名"),
                ("ETIMEDOUT", "连接 npm 仓库超时"),
                ("ECONNRESET", "连接 npm 仓库被重置"),
            ):
                if code in output:
                    return False, f"npm 安装失败：{reason}（{code}），修复后点击安装／重试。"
            return False, (
                f"npm 安装失败（退出码 {proc.returncode}），请检查 npm 仓库连接和目录权限。"
            )
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        except Exception as exc:  # noqa: BLE001
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            if isinstance(exc, asyncio.TimeoutError):
                return False, "npm 安装超过 10 分钟，请检查 npm 仓库连接后重试。"
            return False, f"npm 安装异常：{type(exc).__name__}"

    async def _spawn(self, exe: str) -> None:
        cfg = self._cfg_get()
        host = str(cfg.get("host") or "127.0.0.1")
        port = str(int(cfg.get("port") or 8787))
        command = [exe]
        if Path(exe).suffix == ".js":
            command = [_which("node") or "node", exe]
        # Own Node itself on Windows, rather than a cmd.exe wrapper that leaves Node running.
        entry = Path(exe).parent / "node_modules" / _NPM_PACKAGE / "dist" / "index.js"
        if sys.platform == "win32" and exe.lower().endswith((".cmd", ".bat")) and entry.is_file():
            command = [_which("node") or "node", str(entry)]
        self._proc = await _exec_async(
            [
                *command,
                "start",
                "--config",
                str(self.bili_config_path),
                "--host",
                host,
                "--port",
                port,
            ],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                **os.environ,
                "ACP_SESSION_HEADER": self._expected_session_header,
                # Prefix installs are owned by Oven Multi; disable bili's global updater.
                "ACP_AUTO_UPDATE": "0",
            },
        )

    async def ensure_started(self, *, install: bool = False) -> dict:
        """懒人获取链入口：目标端口先探活，managed 缺失时查找/安装/拉起。"""
        if self._acquiring:  # 避免守护与面板重评估并发重入
            return self.status()
        self._acquiring = True
        try:
            await self._ensure_started_inner(install=install)
        finally:
            self._acquiring = False
        return self.status()

    async def _mark_healthy(self, state: str) -> dict:
        """健康路径统一收口：清计数、验会话标识、启动守护（复用/外部也持续探活）。"""
        self.state = state
        self.detail = ""
        self._failures = 0
        # Panel quick-config changes must reach a reused managed proxy without respawn.
        if str(self._cfg_get().get("mode") or "managed") == "managed":
            ok, detail = self.sync_managed_config()
            if not ok and detail:
                self.detail = detail
        await self.verify_session_header()
        self._refresh_installed_version()
        self._start_monitor()
        if self._update_supported() and not self._latest_version:
            asyncio.get_event_loop().create_task(self.check_for_update(force=True))
        return self.status()

    async def _ensure_started_inner(self, *, install: bool = False) -> dict:
        cfg = self._cfg_get()
        mode = str(cfg.get("mode") or "managed")

        healthy, detail = await self.probe_health()
        if healthy:
            return await self._mark_healthy(
                ST_EXTERNAL_OK
                if mode == "external"
                else ST_SPAWNED
                if self.owned_process
                else ST_REUSED
            )
        if mode == "external":
            self.state = ST_EXTERNAL_DOWN
            self.detail = f"外部代理不可达（{detail}），请确认 bili 已启动。"
            return self.status()

        exe = self._find_executable()
        if not exe:
            if not install and not bool(cfg.get("auto_install", True)):
                self.state = ST_NO_NODE if not _which("node") else ST_INSTALL_FAILED
                self.detail = (
                    "未找到 bili，自动安装已关闭；可点击安装／重试，或开启自动安装并保存。"
                )
                return self.status()
            major = await self._node_major()
            if major == 0:
                self.state = ST_NO_NODE
                self.detail = "宿主机缺少 Node.js（需 ≥20）：请安装 Node 后重试，插件不会代装。"
                return self.status()
            if major < _MIN_NODE_MAJOR:
                self.state = ST_NO_NODE
                self.detail = f"Node 版本过低（v{major}，需 ≥{_MIN_NODE_MAJOR}），请升级。"
                return self.status()
            self.state = ST_INSTALLING
            self.detail = "正在下载 billion-context 到插件数据目录，首次安装可能需要几分钟。"
            ok, install_detail = await self._npm_install()
            if not ok:
                self.state = ST_INSTALL_FAILED
                self.detail = install_detail
                return self.status()
            exe = self._find_executable()
            if not exe:
                self.state = ST_INSTALL_FAILED
                self.detail = "npm 安装完成但缺少程序入口；请检查包完整性或配置的自定义 bili 路径。"
                return self.status()

        self.seed_or_validate_config()  # 播种缺失文件；不一致在 verify 阶段判定

        self.state = ST_STARTING
        self.detail = "正在启动 billion-context 并检查健康端点。"
        try:
            await self._spawn(exe)
        except OSError as exc:
            self.state = ST_SPAWN_FAILED
            self.detail = f"无法启动 bili（{type(exc).__name__}），请检查 Node 和程序路径。"
            return self.status()
        deadline = asyncio.get_event_loop().time() + _STARTUP_WAIT_SEC
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            healthy, detail = await self.probe_health()
            if healthy:
                return await self._mark_healthy(ST_SPAWNED)
        self.state = ST_SPAWN_FAILED
        self.detail = f"bili 启动后健康检查超时（{detail}），请查看 bili 日志。"
        await self._kill_proc()
        return self.status()

    # ── 版本检查与闲置升级 ─────────────────────────────────────────────

    async def _fetch_latest_version(self) -> tuple[str, str]:
        """Query npm registry for the latest package version."""
        try:
            import aiohttp

            session = await self._http()
            async with session.get(
                _NPM_REGISTRY_LATEST,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return "", f"npm 仓库返回 {resp.status}"
                data = await resp.json(content_type=None)
                version = str((data or {}).get("version") or "").strip()
                if not version:
                    return "", "npm 仓库未返回版本号"
                return version, ""
        except Exception as exc:  # noqa: BLE001
            return "", f"{type(exc).__name__}"

    async def _fetch_inflight(self) -> int | None:
        """Read inFlight from /__bili/status; None when the endpoint is unavailable."""
        try:
            import aiohttp

            session = await self._http()
            async with session.get(
                f"{self.url}/__bili/status",
                timeout=aiohttp.ClientTimeout(total=_HEALTH_TIMEOUT_SEC),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                if not isinstance(data, dict) or "inFlight" not in data:
                    return None
                value = data.get("inFlight")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                return max(int(value), 0)
        except Exception:  # noqa: BLE001
            return None

    async def check_for_update(self, *, force: bool = False) -> dict:
        """Refresh installed/latest versions; returns ``update_status()``."""
        if not self._update_supported() and self._runtime_entry() is None:
            self._refresh_installed_version()
            if not self._update_supported():
                self._last_update_message = "当前模式或自定义路径不支持面板升级。"
                return self.update_status()
        cfg = self._cfg_get()
        interval = max(int(cfg.get("auto_update_check_sec") or _DEFAULT_UPDATE_CHECK_SEC), 300)
        now = time.monotonic()
        if (
            not force
            and self._latest_version
            and now - self._last_update_check < interval
        ):
            return self.update_status()
        self._update_checking = True
        try:
            self._refresh_installed_version()
            latest, error = await self._fetch_latest_version()
            self._last_update_check = time.monotonic()
            if error:
                self._last_update_message = f"检查更新失败：{error}"
                return self.update_status()
            self._latest_version = latest
            self._update_available = is_version_newer(latest, self._installed_version)
            if self._update_available:
                self._last_update_message = (
                    f"发现新版本 {self._installed_version or '未知'} → {latest}"
                )
            else:
                self._last_update_message = f"已是最新（{self._installed_version or latest}）"
            return self.update_status()
        finally:
            self._update_checking = False

    async def _wait_until_idle(self, timeout_sec: float) -> tuple[bool, str]:
        """Wait until the proxy reports zero in-flight requests."""
        if not self.healthy and not self.owned_process:
            return True, ""
        deadline = time.monotonic() + max(timeout_sec, 0)
        while True:
            inflight = await self._fetch_inflight()
            if inflight is None:
                # Older builds may lack the field; allow restart if health is up.
                healthy, _ = await self.probe_health()
                if healthy:
                    return True, ""
                return False, "代理不可达，无法确认是否闲置。"
            if inflight == 0:
                return True, ""
            if time.monotonic() >= deadline:
                return False, f"仍有 {inflight} 个进行中的请求，请稍后再试。"
            self.detail = f"等待闲置后更新（进行中 {inflight}）…"
            await asyncio.sleep(_IDLE_POLL_SEC)

    async def update_package(self, *, wait_idle: bool = True) -> dict:
        """Upgrade the managed runtime package and restart an owned proxy when idle."""
        if self._acquiring:
            self._last_update_message = "安装或更新仍在进行。"
            return self.status()
        if not self._update_supported():
            self._last_update_message = "仅托管模式且使用默认 billion-runtime 时可面板升级。"
            return self.status()
        self._acquiring = True
        previous = self.state
        try:
            info = await self.check_for_update(force=True)
            if not info.get("available"):
                return self.status()
            target = self._latest_version or "latest"
            if wait_idle:
                idle, reason = await self._wait_until_idle(_MANUAL_IDLE_WAIT_SEC)
                if not idle:
                    self.state = previous if previous != ST_UPDATING else ST_SPAWNED
                    self.detail = reason
                    self._last_update_message = reason
                    return self.status()
            self.state = ST_UPDATING
            self.detail = f"正在更新 billion-context 到 {target}…"
            self._last_update_message = self.detail
            owned = self.owned_process
            if owned:
                await self._kill_proc()
            ok, install_detail = await self._npm_install(
                package=f"{_NPM_PACKAGE}@{target}"
            )
            if not ok:
                self.state = ST_INSTALL_FAILED
                self.detail = install_detail
                self._last_update_message = install_detail
                if owned:
                    await self._ensure_started_inner(install=False)
                return self.status()
            self._update_available = False
            self._update_pending_restart = not owned
            if owned or not self.healthy:
                self.state = ST_STARTING
                self.detail = "更新完成，正在重启代理…"
                exe = self._find_executable()
                if not exe:
                    self.state = ST_INSTALL_FAILED
                    self.detail = "更新完成但缺少程序入口。"
                    self._last_update_message = self.detail
                    return self.status()
                self.seed_or_validate_config()
                try:
                    await self._spawn(exe)
                except OSError as exc:
                    self.state = ST_SPAWN_FAILED
                    self.detail = f"更新后无法启动（{type(exc).__name__}）。"
                    self._last_update_message = self.detail
                    return self.status()
                deadline = asyncio.get_event_loop().time() + _STARTUP_WAIT_SEC
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.5)
                    healthy, detail = await self.probe_health()
                    if healthy:
                        self._update_pending_restart = False
                        self._last_update_message = (
                            f"已更新到 {self._installed_version or target}"
                        )
                        return await self._mark_healthy(ST_SPAWNED)
                self.state = ST_SPAWN_FAILED
                self.detail = f"更新后健康检查超时（{detail}）。"
                self._last_update_message = self.detail
                await self._kill_proc()
                return self.status()
            self.state = ST_REUSED if previous == ST_REUSED else previous
            self.detail = (
                f"已下载 {self._installed_version or target}；"
                "当前端口上的代理非本插件拉起，请重启该实例后生效。"
            )
            self._last_update_message = self.detail
            return self.status()
        finally:
            self._acquiring = False

    async def _maybe_auto_update(self) -> None:
        """Idle-time auto upgrade for managed runtime installs."""
        cfg = self._cfg_get()
        if not bool(cfg.get("auto_update", True)):
            return
        if not self._update_supported() or self._acquiring:
            return
        # Avoid hammering npm / restart loops when idle checks keep failing.
        if time.monotonic() - self._last_auto_update_attempt < 1800:
            return
        info = await self.check_for_update(force=False)
        if not info.get("available"):
            return
        if self.owned_process:
            idle, _ = await self._wait_until_idle(0)
            if not idle:
                self._last_update_message = "已发现新版本，等待闲置后自动更新。"
                return
        self._last_auto_update_attempt = time.monotonic()
        await self.update_package(wait_idle=True)

    # ── 守护 ─────────────────────────────────────────────────────────────

    def _start_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.get_event_loop().create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        while self.state not in (ST_STOPPED, ST_DISABLED):
            await asyncio.sleep(max(int(self._cfg_get().get("health_interval_sec") or 60), 5))
            try:
                await self._monitor_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self._failures += 1

    async def _monitor_once(self) -> None:
        """单轮守护：owned 存活探活（阈值重启）/ 非自有则重走获取链。"""
        if self.state in (ST_STOPPED, ST_DISABLED) or self._acquiring:
            return
        with contextlib.suppress(Exception):
            await self._maybe_auto_update()
        if self._acquiring:
            return
        if self.owned_process:
            healthy, detail = await self.probe_health()
            if healthy:
                self._failures = 0
                if self.state == ST_DEGRADED:
                    self.state, self.detail = ST_SPAWNED, ""
                return
            # 进程存活但服务失效：达到阈值后终止并重新拉起
            self._failures += 1
            if self._failures >= _MAX_CONSECUTIVE_FAILURES:
                self.detail = f"连续 {self._failures} 次健康检查失败，重启代理（{detail}）。"
                await self._kill_proc()
                status = await self.ensure_started()
                if status["healthy"]:
                    self._failures = 0
                return
            if self.state != ST_DEGRADED:
                self.state = ST_DEGRADED
                self.detail = f"代理健康检查失败（{detail}），守护中。"
        else:
            # 自有进程已死、或此前复用/外部的实例：重新走获取链（探活→按需拉起）
            status = await self.ensure_started()
            if status["healthy"]:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= _MAX_CONSECUTIVE_FAILURES:
                self.state = ST_DEGRADED

    async def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                proc.kill()

    async def stop(self, *, keep_process: bool = False) -> None:
        """Stop monitoring; retain an owned process only while a live SDK still needs it."""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._monitor_task
            self._monitor_task = None
        if not keep_process:
            await self._kill_proc()
        if self._session is not None and not self._session.closed:
            with contextlib.suppress(Exception):
                await self._session.close()
        self.state = ST_STOPPED
        self.detail = ""
