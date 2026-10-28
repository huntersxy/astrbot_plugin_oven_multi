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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
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
ST_STARTING = "starting"
ST_SPAWN_FAILED = "spawn_failed"  # 拉起后健康检查超时
ST_STOPPED = "stopped"  # 已停止（未启用/卸载）

_NPM_PACKAGE = "billion-context"
_MIN_NODE_MAJOR = 20
_HEALTH_TIMEOUT_SEC = 2.0
_STARTUP_WAIT_SEC = 20.0
_MAX_CONSECUTIVE_FAILURES = 3


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
        return {
            "state": self.state,
            "healthy": self.healthy,
            "url": self.url,
            "detail": self.detail,
            "busy": self._acquiring,
            "mode": str(self._cfg_get().get("mode") or "managed"),
            "session_header_state": self.session_header_state,
            "session_header_detail": self.session_header_detail,
        }

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
                return True, ""
            return (
                False,
                f"bili 配置的 sessionHeader={current!r} 与预期 {expected!r} 不一致，"
                "请在面板「上下文压缩」的会话头名称与 bili 配置之间统一。",
            )
        seed = {"sessionHeader": expected, "providers": {}}
        self.bili_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.bili_config_path.write_text(
            json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True, "已播种 bili 配置。"

    # ── 懒人获取链 ───────────────────────────────────────────────────────

    def _find_executable(self) -> str | None:
        cfg = self._cfg_get()
        name = str(cfg.get("executable") or "bili").strip() or "bili"
        if name == "bili":
            entry = self.runtime_dir / "node_modules" / _NPM_PACKAGE / "dist" / "index.js"
            if entry.is_file():
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

    async def _npm_install(self) -> tuple[bool, str]:
        npm = _which("npm")
        if not npm:
            return False, "未找到 npm，无法自动安装 billion-context。"
        proc = None
        try:
            proc = await _exec_async(
                [
                    npm, "install", "--prefix", str(self.runtime_dir),
                    "--no-audit", "--no-fund", "--no-package-lock", _NPM_PACKAGE,
                ],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode == 0:
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
            env={**os.environ, "ACP_SESSION_HEADER": self._expected_session_header},
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
        await self.verify_session_header()
        self._start_monitor()
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
