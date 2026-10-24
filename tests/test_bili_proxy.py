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
"""离线测试：bili 代理懒人获取链（复用/安装/拉起/降级）、配置播种与版本解析。

模块级函数（_which/_exec_async）与状态常量均可替换，进程全为假对象。
运行：
    python -m pytest tests/test_bili_proxy.py -q
    python tests/test_bili_proxy.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = Path(_TESTS_DIR).parent
sys.path.insert(0, _PLUGIN_DIR.parent)

_SPEC = importlib.util.spec_from_file_location(
    "oven_bili_proxy_standalone", _PLUGIN_DIR / "features" / "bili_proxy.py"
)
bp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bp)


class FakeProcess:
    def __init__(self, final_code=0, stdout=b""):
        self.returncode = None
        self._final = final_code
        self._stdout = stdout
        self.terminated = False

    async def communicate(self):
        self.returncode = self._final
        return self._stdout, b""

    def terminate(self):
        self.terminated = True
        self.returncode = self._final

    async def wait(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def make_manager(
    tmp: Path, cfg: dict, exec_log: list, procs: dict, which_map: dict, monitor: bool = False
):
    """构造管理器并替换可执行查找/子进程/健康探测（originals 记录在实例上）。

    默认屏蔽守护任务与端点访问（离线安全）；monitor=True 保留真实
    ``_start_monitor`` 供守护专项测试。
    """
    manager = bp.BiliProxyManager(lambda: cfg, tmp)
    originals = (bp._which, bp._exec_async)

    def fake_which(name):
        return which_map.get(name)

    async def fake_exec(args, **kwargs):
        exec_log.append(list(args))
        if "--version" in args:
            ver = which_map.get("__version_out__", "v22.11.0\n")
            return FakeProcess(stdout=ver.encode() if isinstance(ver, str) else ver)
        if "install" in args:
            return FakeProcess()
        return procs.setdefault("spawn", FakeProcess())

    probe_results = procs.setdefault("__probe__", [])

    async def fake_probe():
        if probe_results:
            return tuple(probe_results.pop(0))
        return (False, "exhausted")

    async def fake_admin_config():
        # 默认端点返回与配置一致的 sessionHeader，会话标识判定为 ok
        return {"sessionHeader": cfg.get("session_header") or "x-astrbot-conversation-id"}

    bp._which = fake_which
    bp._exec_async = fake_exec
    manager.probe_health = fake_probe
    manager._fetch_admin_config = fake_admin_config
    if not monitor:
        manager._start_monitor = lambda: None
    manager._restore = originals
    return manager


def restore_module(manager):
    original_which, original_exec = manager._restore
    bp._which = original_which
    bp._exec_async = original_exec


def run(coro):
    return asyncio.run(coro)


def test_parse_node_version():
    assert bp._parse_node_version("v22.11.0") == 22
    assert bp._parse_node_version("20.1.2") == 20
    assert bp._parse_node_version("not-a-version") == 0
    assert bp._parse_node_version("") == 0
    print("node 版本解析 ok")


def test_seed_config_create_and_validate():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager = bp.BiliProxyManager(lambda: {"session_header": "x-astrbot-conversation-id"}, tmp)
        ok, detail = manager.seed_or_validate_config()
        assert ok is True and "播种" in detail
        data = json.loads(manager.bili_config_path.read_text(encoding="utf-8"))
        assert data == {"sessionHeader": "x-astrbot-conversation-id", "providers": {}}
        # 已存在且一致
        ok, detail = manager.seed_or_validate_config()
        assert ok is True and detail == ""
        # 已存在但不一致：不覆盖、给出指引
        data["sessionHeader"] = "x-other"
        manager.bili_config_path.write_text(json.dumps(data), encoding="utf-8")
        ok, detail = manager.seed_or_validate_config()
        assert ok is False and "不一致" in detail
        assert (
            json.loads(manager.bili_config_path.read_text(encoding="utf-8"))["sessionHeader"]
            == "x-other"
        )
        print("播种与校验 ok")


def test_chain_reuse_and_external():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {"mode": "managed", "host": "127.0.0.1", "port": 8787}
        exec_log: list = []
        manager = make_manager(tmp, cfg, exec_log, {}, {})
        manager.probe_health = lambda: _async_value((True, ""))
        status = run(manager.ensure_started())
        assert status["state"] == bp.ST_REUSED and not exec_log  # 复用不拉进程
        restore_module(manager)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {"mode": "external", "host": "10.0.0.5", "port": 8787}
        manager = make_manager(tmp, cfg, [], {}, {})
        manager.probe_health = lambda: _async_value((False, "TimeoutError"))
        status = run(manager.ensure_started())
        assert status["state"] == bp.ST_EXTERNAL_DOWN and "外部代理不可达" in status["detail"]
        restore_module(manager)
    print("复用与外部模式 ok")


def _async_value(value):
    async def stub():
        return value

    return stub()


def test_chain_managed_spawn_and_health():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {
            "mode": "managed",
            "host": "127.0.0.1",
            "port": 8787,
            "executable": "bili",
            "session_header": "x-astrbot-conversation-id",
        }
        exec_log: list = []
        probes = [(False, "ConnectError"), (False, "ConnectError"), (True, "")]
        procs = {"__probe__": probes}
        manager = make_manager(tmp, cfg, exec_log, procs, {"bili": "/usr/bin/bili"})
        status = run(manager.ensure_started())
        assert status["state"] == bp.ST_SPAWNED
        assert exec_log == [
            [
                "/usr/bin/bili",
                "start",
                "--config",
                str(manager.bili_config_path),
                "--host",
                "127.0.0.1",
                "--port",
                "8787",
            ]
        ]
        assert manager.bili_config_path.exists()  # 播种先行
        run(manager.stop())  # 清理守护任务
        restore_module(manager)
    print("托管拉起 ok")


def test_chain_spawn_timeout_kills_process():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {"mode": "managed", "host": "127.0.0.1", "port": 8787}
        exec_log: list = []
        procs: dict = {"__probe__": [(False, "down")] * 10}
        manager = make_manager(tmp, cfg, exec_log, procs, {"bili": "/usr/bin/bili"})
        original_wait = bp._STARTUP_WAIT_SEC
        bp._STARTUP_WAIT_SEC = 0.3
        try:
            status = run(manager.ensure_started())
        finally:
            bp._STARTUP_WAIT_SEC = original_wait
        assert status["state"] == bp.ST_SPAWN_FAILED
        assert procs["spawn"].returncode is not None  # 超时后杀掉自有进程
        restore_module(manager)
    print("启动超时降级 ok")


def test_chain_auto_install_and_node_gate():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # bili 缺失 + 自动安装：node v22 + npm 成功 → 安装后拉起
        cfg = {"mode": "managed", "host": "127.0.0.1", "port": 8787, "auto_install": True}
        exec_log: list = []
        which_map = {"node": "/usr/bin/node", "npm": "/usr/bin/npm", "__version_out__": "v22.11.0"}
        installed = {"done": False}

        def late_which(name):
            if name == "bili":
                return "/usr/local/bin/bili" if installed["done"] else None
            return which_map.get(name)

        procs = {"__probe__": [(False, "down"), (True, "")]}

        async def fake_exec(args, **kwargs):
            exec_log.append(list(args))
            if "install" in args:
                installed["done"] = True
                return FakeProcess()
            if "--version" in args:
                return FakeProcess(stdout=b"v22.11.0\n")
            return FakeProcess()

        manager = bp.BiliProxyManager(lambda: cfg, tmp)
        original = (bp._which, bp._exec_async)
        bp._which, bp._exec_async = late_which, fake_exec
        manager.probe_health = lambda: _async_value(procs["__probe__"].pop(0))
        try:
            status = run(manager.ensure_started())
            assert status["state"] == bp.ST_SPAWNED
            assert any("install" in args and "billion-context" in args for args in exec_log)
            run(manager.stop())
        finally:
            bp._which, bp._exec_async = original

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # node 缺失：不猜不装，明确指路
        cfg = {"mode": "managed", "host": "127.0.0.1", "port": 8787, "auto_install": True}
        manager = bp.BiliProxyManager(lambda: cfg, tmp)
        original = (bp._which, bp._exec_async)
        bp._which = lambda name: None
        manager.probe_health = lambda: _async_value((False, "down"))
        try:
            status = run(manager.ensure_started())
            assert status["state"] == bp.ST_NO_NODE and "Node" in status["detail"]
        finally:
            bp._which, bp._exec_async = original

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # node 版本过低
        cfg = {"mode": "managed", "host": "127.0.0.1", "port": 8787, "auto_install": True}
        exec_log: list = []

        async def fake_exec(args, **kwargs):
            exec_log.append(list(args))
            return FakeProcess(stdout=b"v18.20.0\n") if "--version" in args else FakeProcess()

        manager = bp.BiliProxyManager(lambda: cfg, tmp)
        original = (bp._which, bp._exec_async)
        bp._which = lambda name: {"node": "/usr/bin/node"}.get(name)
        bp._exec_async = fake_exec
        manager.probe_health = lambda: _async_value((False, "down"))
        try:
            status = run(manager.ensure_started())
            assert status["state"] == bp.ST_NO_NODE and "过低" in status["detail"]
        finally:
            bp._which, bp._exec_async = original
    print("自动安装与 Node 门槛 ok")


def test_verify_session_header_states():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {"session_header": "x-astrbot-conversation-id"}
        manager = bp.BiliProxyManager(lambda: cfg, tmp)

        def endpoint(payload):
            async def stub():
                return payload

            return stub()

        # 端点明确一致
        manager._fetch_admin_config = lambda: endpoint(
            {"sessionHeader": "x-astrbot-conversation-id"}
        )
        run(manager.verify_session_header())
        assert manager.session_header_state == "ok"
        # 端点明确不一致
        manager._fetch_admin_config = lambda: endpoint({"sessionHeader": "x-acp-session"})
        run(manager.verify_session_header())
        assert (
            manager.session_header_state == "mismatch" and "不一致" in manager.session_header_detail
        )
        # 端点不可用 + 非自拉实例（REUSED）→ 未知，且不播种文件制造虚假信任
        manager.state = bp.ST_REUSED
        manager._fetch_admin_config = lambda: endpoint(None)
        run(manager.verify_session_header())
        assert manager.session_header_state == "unknown" and not manager.bili_config_path.exists()
        # 端点不可用 + 自拉实例 → 播种文件兜底
        manager.state = bp.ST_SPAWNED
        manager._proc = FakeProcess()
        run(manager.verify_session_header())
        assert manager.session_header_state == "ok" and manager.bili_config_path.exists()
        # 自拉 + 文件不一致 → mismatch
        manager.bili_config_path.write_text(
            json.dumps({"sessionHeader": "x-other"}), encoding="utf-8"
        )
        run(manager.verify_session_header())
        assert manager.session_header_state == "mismatch"
    print("会话标识验证 ok")


def test_monitor_respawns_owned_unhealthy():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {
            "mode": "managed",
            "host": "127.0.0.1",
            "port": 8787,
            "session_header": "x-astrbot-conversation-id",
        }
        exec_log: list = []
        procs: dict = {"__probe__": [(False, "down"), (True, "")]}
        manager = make_manager(tmp, cfg, exec_log, procs, {"bili": "/usr/bin/bili"})
        run(manager.ensure_started())
        assert manager.state == bp.ST_SPAWNED
        old_proc = procs["spawn"]

        # 连续失败达阈值：杀掉旧进程并重新拉起（重启时首探仍失败→走拉起，轮询转成功）
        fail_count = {"n": 0}
        fail_until = bp._MAX_CONSECUTIVE_FAILURES + 1  # 3 轮守护失败 + 重启首探

        async def respawn_probe():
            fail_count["n"] += 1
            return (False, "hanging") if fail_count["n"] <= fail_until else (True, "")

        manager.probe_health = respawn_probe
        for _ in range(bp._MAX_CONSECUTIVE_FAILURES):
            run(manager._monitor_once())
        assert old_proc.terminated
        assert sum(1 for args in exec_log if "start" in args) == 2  # 重新拉起
        assert manager._failures == 0 and manager.state == bp.ST_SPAWNED
        run(manager.stop())
        restore_module(manager)
    print("owned 不健康阈值重启 ok")


def test_monitor_keeps_reused_instance_alive_check():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {
            "mode": "managed",
            "host": "127.0.0.1",
            "port": 8787,
            "session_header": "x-astrbot-conversation-id",
        }
        exec_log: list = []
        procs: dict = {"__probe__": [(True, ""), (False, "down"), (True, "")]}
        manager = make_manager(tmp, cfg, exec_log, procs, {"bili": "/usr/bin/bili"})
        status = run(manager.ensure_started())
        assert status["state"] == bp.ST_REUSED and not exec_log  # 复用未拉进程
        # 复用实例随后失联：守护轮走获取链，探活失败 → 自行拉起
        run(manager._monitor_once())
        assert sum(1 for args in exec_log if "start" in args) == 1
        assert manager.state == bp.ST_SPAWNED and manager._failures == 0
        run(manager.stop())
        restore_module(manager)
    print("复用实例守护探活 ok")


def test_stop_terminates_owned_process():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = {"mode": "managed", "host": "127.0.0.1", "port": 8787}
        exec_log: list = []
        procs: dict = {"__probe__": [(False, "down"), (True, "")]}
        manager = make_manager(tmp, cfg, exec_log, procs, {"bili": "/usr/bin/bili"})
        run(manager.ensure_started())
        assert manager.state == bp.ST_SPAWNED
        proc = procs.get("spawn")
        assert proc is not None
        run(manager.stop())
        assert manager.state == bp.ST_STOPPED
        assert manager._proc is None
        assert proc.terminated
        restore_module(manager)
    print("停止清理 ok")


if __name__ == "__main__":
    fns = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\n全部通过：{len(fns)} 项")
