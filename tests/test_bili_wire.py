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
"""离线测试：bili 接管/恢复引擎（目标检测、diff、拷贝替换、三方恢复、巡检）。

引擎不依赖 astrbot，直接以 FakeHostConfig 驱动。运行：
    python -m pytest tests/test_bili_wire.py -q
    python tests/test_bili_wire.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_DIR = Path(_TESTS_DIR).parent
sys.path.insert(0, _PLUGIN_DIR.parent)

# bili_wire 不依赖 astrbot；按文件路径加载，绕过会拉起 main 的包初始化
_SPEC = importlib.util.spec_from_file_location(
    "oven_bili_wire_standalone", _PLUGIN_DIR / "features" / "bili_wire.py"
)
bw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bw)


class FakeHostConfig(dict):
    """宿主配置替身（记录整体持久化快照）。"""

    def __init__(self, data=None):
        super().__init__(data or {})
        self.saved: list[dict] = []

    def save_config(self, replace_config=None, **kwargs):
        if replace_config:
            self.update(replace_config)
        self.saved.append(json.loads(json.dumps(dict(self))))


def make_manager(tmp: Path, host: FakeHostConfig | None = None):
    host = host or FakeHostConfig()
    return bw.BiliWireManager(lambda: host, tmp), host


def sample_host() -> FakeHostConfig:
    return FakeHostConfig(
        {
            "provider": [
                {
                    "id": "main",
                    "type": "openai_chat_completion",
                    "api_base": "https://api.example.com/v1",
                    "key": "sk-SECRET",
                    "model_config": {"model": "gpt-test"},
                },
                {
                    "id": "bare",
                    "type": "openai_chat_completion",
                    "api_base": "https://bare.example.com/v1",
                    "key": "sk-BARE",
                },
                {"id": "embed", "type": "openai_embedding", "api_base": "https://e.example.com"},
            ],
            "agent_runner": {
                "runner_type": "local",
                "config": {
                    "model": {"provider_id": "main", "fallback_provider_ids": ["backup"]},
                    "compression": {"max_turns": 50, "overflow_strategy": "llm_compress"},
                },
            },
        }
    )


def test_detect_targets():
    ok, msg = bw.BiliWireManager.detect_targets(["main"], ["main", "bare"], "main")
    assert ok == ["main"] and msg == ""
    ok, msg = bw.BiliWireManager.detect_targets(["main", "nope"], ["main"], None)
    assert ok == [] and "nope" in msg and "对话类" in msg
    ok, msg = bw.BiliWireManager.detect_targets([], ["main", "bare"], "main")
    assert ok == ["main"]
    ok, msg = bw.BiliWireManager.detect_targets([], ["main", "bare"], "unknown")
    assert ok == [] and "无法唯一确定" in msg and "main" in msg
    ok, msg = bw.BiliWireManager.detect_targets([], [], None)
    assert ok == [] and "候选" in msg
    print("detect_targets ok")


def test_preview_and_skip_reasons():
    with tempfile.TemporaryDirectory() as td:
        manager, host = make_manager(Path(td), sample_host())
        preview = manager.preview(["main", "embed", "missing"], "127.0.0.1", 8787)
        fields = {(row["target"], row["field"]) for row in preview["changes"]}
        assert ("provider[main]", "api_base") in fields
        assert ("provider[main]", "max_context_tokens") in fields
        assert ("agent_runner.config.compression", "max_turns") in fields
        assert ("agent_runner.config.compression", "overflow_strategy") in fields
        api_row = next(r for r in preview["changes"] if r["field"] == "api_base")
        assert api_row["new"] == "http://127.0.0.1:8787/bili/https://api.example.com/v1"
        reasons = {item["id"]: item["reason"] for item in preview["skipped"]}
        assert "不存在" in reasons["missing"]
        print("preview ok")


def test_wire_copy_on_write_and_record_no_secret():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, sample_host())
        original_entry = host["provider"][0]
        result = manager.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        assert result["ok"] is True
        # 拷贝替换：原 dict 对象不被原地修改（避免影响运行中 provider 实例）
        assert original_entry["api_base"] == "https://api.example.com/v1"
        assert host["provider"][0] is not original_entry
        assert (
            host["provider"][0]["api_base"]
            == "http://127.0.0.1:8787/bili/https://api.example.com/v1"
        )
        assert host["provider"][0]["max_context_tokens"] == bw.INFLATED_MAX_CONTEXT_TOKENS
        comp = host["agent_runner"]["config"]["compression"]
        assert comp["max_turns"] == -1 and comp["overflow_strategy"] == "truncate_by_turns"
        assert host.saved, "应整体持久化一次"
        # 记录只含被改字段，绝无 API Key
        record_text = (tmp / bw.RECORD_FILENAME).read_text(encoding="utf-8")
        assert "sk-SECRET" not in record_text and "key" not in record_text
        record = json.loads(record_text)
        assert record["providers"]["main"]["api_base"]["old"] == "https://api.example.com/v1"
        assert record["providers"]["main"]["max_context_tokens"]["old_absent"] is True
        assert record["compression"]["max_turns"]["old"] == 50
        # 已有记录 → 拒绝再次接管
        again = manager.wire(["bare"], "127.0.0.1", 8787, health_check=lambda: True)
        assert again["ok"] is False and "先" in again["message"]
        # 健康检查不通过 → 拒绝
        manager2, host2 = make_manager(tmp / "x", sample_host())
        denied = manager2.wire(["main"], "127.0.0.1", 8787, health_check=lambda: False)
        assert denied["ok"] is False and "健康检查" in denied["message"]
        # 目标缺失 → 拒绝且零修改
        manager3, host3 = make_manager(tmp / "y", sample_host())
        missing = manager3.wire(["ghost"], "127.0.0.1", 8787, health_check=lambda: True)
        assert missing["ok"] is False and "不可接管" in missing["message"]
        assert host3["provider"][0]["api_base"] == "https://api.example.com/v1"
        print("wire 拷贝替换与记录 ok")


def test_restore_full_and_three_way():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, sample_host())
        manager.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        # 用户接管后又改了 api_base（指向别的代理）→ 三方校验跳过该字段
        host["provider"][0]["api_base"] = "http://other:9999/bili/https://api.example.com/v1"
        result = manager.restore()
        assert result["ok"] is True
        assert any("api_base" in item["field"] for item in result["skipped"])
        # compression 字段未被用户改过 → 正常恢复
        comp = host["agent_runner"]["config"]["compression"]
        assert comp["max_turns"] == 50 and comp["overflow_strategy"] == "llm_compress"
        # api_base 保留用户的新值
        assert host["provider"][0]["api_base"].startswith("http://other:9999")
        # max_context_tokens 恢复时按 old_absent 删除
        assert "max_context_tokens" not in host["provider"][0]
        # 有跳过项 → 记录保留
        assert (tmp / bw.RECORD_FILENAME).exists()
        assert "未覆盖" in result["message"]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, sample_host())
        manager.wire(["main", "bare"], "127.0.0.1", 8787, health_check=lambda: True)
        result = manager.restore()
        assert result["ok"] is True and not result["skipped"]
        assert host["provider"][0]["api_base"] == "https://api.example.com/v1"
        assert host["provider"][1]["api_base"] == "https://bare.example.com/v1"
        assert "max_context_tokens" not in host["provider"][0]
        comp = host["agent_runner"]["config"]["compression"]
        assert comp["max_turns"] == 50 and comp["overflow_strategy"] == "llm_compress"
        assert not (tmp / bw.RECORD_FILENAME).exists()  # 全量恢复后删除记录
        assert "没有接管记录" in manager.restore()["message"]
        print("restore 全量与三方 ok")


def test_doctor_notices():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, sample_host())
        assert manager.doctor(proxy_healthy=True) == []  # 无记录无告警
        manager.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        notices = manager.doctor(proxy_healthy=False, fallback_ids=["backup"])
        assert any("不可用" in n for n in notices)
        assert any("备用链" in n and "backup" in n for n in notices)
        notices = manager.doctor(
            proxy_healthy=True,
            runtime_client_api={"main": "https://api.example.com/v1"},  # 运行中仍旧值
        )
        assert any("尚未重载" in n and "main" in n for n in notices)
        # 配置被改回 → 不一致告警
        host["provider"][0]["api_base"] = "https://api.example.com/v1"
        notices = manager.doctor(proxy_healthy=True)
        assert any("不一致" in n for n in notices)
        # 记录中的 provider 被删除
        host["provider"].pop(0)
        notices = manager.doctor(proxy_healthy=True)
        assert any("已从配置移除" in n for n in notices)
        print("doctor 巡检 ok")


def test_doctor_detects_model_switch_without_expanding_explicit_scope():
    with tempfile.TemporaryDirectory() as td:
        manager, host = make_manager(Path(td), sample_host())
        assert manager.wire(["main"], "127.0.0.1", 8787)["ok"]
        before = json.loads(json.dumps(host))
        record = manager.record_path.read_bytes()
        assert manager.doctor(proxy_healthy=True, active_ids=["main"]) == []
        notices = manager.doctor(proxy_healthy=True, active_ids=["backup", "backup"])
        assert len(notices) == 1
        assert "当前对话模型未接管（backup）" in notices[0]
        assert "绕过 bili" in notices[0]
        assert host == before
        assert manager.record_path.read_bytes() == record


def inherited_host() -> FakeHostConfig:
    """provider 无自身 api_base、经 provider_source 继承的宿主配置。"""
    return FakeHostConfig(
        {
            "provider_sources": [
                {"id": "src-1", "api_base": "https://src.example.com/v1", "key": "sk-SRC"},
            ],
            "provider": [
                {
                    "id": "inh",
                    "type": "openai_chat_completion",
                    "provider_source_id": "src-1",
                    "key": "sk-x",
                },
                # 自身地址优先于继承
                {
                    "id": "own",
                    "type": "openai_chat_completion",
                    "provider_source_id": "src-1",
                    "api_base": "https://own.example.com/v1",
                },
            ],
            "agent_runner": {"config": {"compression": {}}},
        }
    )


def test_wire_resolves_provider_source_inheritance():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, inherited_host())
        # 预览按实际生效地址解析（继承 → source；自身 → 覆盖 source）
        preview = manager.preview(["inh", "own"], "127.0.0.1", 8787)
        api_old = {
            row["target"]: row["old"] for row in preview["changes"] if row["field"] == "api_base"
        }
        assert api_old["provider[inh]"] == "https://src.example.com/v1"
        assert api_old["provider[own]"] == "https://own.example.com/v1"
        # 接管：继承地址物化为自身 api_base，共享 source 不被修改
        result = manager.wire(["inh"], "127.0.0.1", 8787, health_check=lambda: True)
        assert result["ok"] is True, result["message"]
        inh = host["provider"][0]
        assert inh["api_base"] == "http://127.0.0.1:8787/bili/https://src.example.com/v1"
        assert host["provider_sources"][0]["api_base"] == "https://src.example.com/v1"
        record = json.loads((tmp / bw.RECORD_FILENAME).read_text(encoding="utf-8"))
        spec = record["providers"]["inh"]["api_base"]
        assert spec["old_absent"] is True  # 记录“原先不存在覆盖字段”
        assert spec["old"] == "https://src.example.com/v1"
        # 恢复：删除覆盖键 → 继承恢复生效
        manager.restore()
        assert "api_base" not in host["provider"][0]
        assert host["provider_sources"][0]["api_base"] == "https://src.example.com/v1"
        assert not (tmp / bw.RECORD_FILENAME).exists()
        assert host["agent_runner"]["config"]["compression"] == {}
    print("provider_source 继承解析 ok")


def test_wire_record_write_failure_leaves_config_untouched():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, sample_host())
        blocker = tmp / "blocker"
        blocker.write_text("not-a-dir", encoding="utf-8")
        manager.record_path = blocker / "record.json"  # 父路径是文件 → 写入必败
        result = manager.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        assert result["ok"] is False and "未修改任何配置" in result["message"]
        assert host["provider"][0]["api_base"] == "https://api.example.com/v1"
        comp = host["agent_runner"]["config"]["compression"]
        assert comp.get("max_turns") == 50 and not host.saved
    print("记录写失败不改配置 ok")


def test_wire_save_failure_rolls_back_and_keeps_recovery_record():
    class ExplodingHost(FakeHostConfig):
        def save_config(self, replace_config=None, **kwargs):
            raise OSError("disk full")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        host = ExplodingHost(dict(sample_host()))
        manager = bw.BiliWireManager(lambda: host, tmp)
        result = manager.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        assert result["ok"] is False and "回滚" in result["message"]
        assert host["provider"][0]["api_base"] == "https://api.example.com/v1"
        assert "max_context_tokens" not in host["provider"][0]
        comp = host["agent_runner"]["config"]["compression"]
        assert comp.get("max_turns") == 50 and comp.get("overflow_strategy") == "llm_compress"
        assert (tmp / bw.RECORD_FILENAME).exists()
        # A real save may have committed before raising; retain its recovery information.
        host2 = FakeHostConfig(dict(sample_host()))
        manager2 = bw.BiliWireManager(lambda: host2, tmp)
        assert manager2.restore()["ok"] is True
        assert not manager2.record_exists()
        ok = manager2.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        assert ok["ok"] is True
    print("持久化失败回滚 ok")


def test_corrupt_record_blocks_wire_and_restore():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manager, host = make_manager(tmp, sample_host())
        (tmp / bw.RECORD_FILENAME).write_text("{broken json", encoding="utf-8")
        assert manager.record_state() == "corrupt"
        assert manager.record_exists() is True
        denied = manager.wire(["main"], "127.0.0.1", 8787, health_check=lambda: True)
        assert denied["ok"] is False and "损坏" in denied["message"]
        assert host["provider"][0]["api_base"] == "https://api.example.com/v1"
        refused = manager.restore()
        assert refused["ok"] is False and "损坏" in refused["message"]
        notices = manager.doctor(proxy_healthy=True)
        assert any("损坏" in notice for notice in notices)
    print("损坏记录阻断 ok")


def test_partial_restore_is_retryable_and_preserves_user_changes():
    with tempfile.TemporaryDirectory() as td:
        manager, host = make_manager(Path(td), sample_host())
        assert manager.wire(["main", "main"], "127.0.0.1", 8787)["ok"]
        host["provider"][0]["api_base"] = "https://user.example/v1"
        first = manager.restore()
        assert len(first["skipped"]) == 1
        assert "max_context_tokens" not in host["provider"][0]
        again = manager.restore()
        assert len(again["skipped"]) == 1
        host["provider"][0]["api_base"] = "https://api.example.com/v1"
        assert not manager.restore()["skipped"]
        assert not manager.record_exists()


def test_restore_save_failure_rolls_back_and_can_retry():
    with tempfile.TemporaryDirectory() as td:
        manager, host = make_manager(Path(td), sample_host())
        manager.wire(["main"], "127.0.0.1", 8787)
        save = host.save_config
        host.save_config = lambda: (_ for _ in ()).throw(OSError("disk full"))
        assert not manager.restore()["ok"]
        assert manager.record_exists()
        assert "/bili/" in host["provider"][0]["api_base"]
        host.save_config = save
        assert manager.restore()["ok"] and not manager.record_exists()


def test_explicit_empty_address_does_not_inherit_source():
    with tempfile.TemporaryDirectory() as td:
        host = inherited_host()
        host["provider"][0]["api_base"] = ""
        manager, _ = make_manager(Path(td), host)
        assert not manager.wire(["inh"], "127.0.0.1", 8787)["ok"]


def test_invalid_record_shape_is_corrupt():
    with tempfile.TemporaryDirectory() as td:
        manager, _ = make_manager(Path(td), sample_host())
        for value in (
            {},
            {"version": 1, "providers": [], "compression": {}},
            {"version": 1, "providers": {"main": {}}, "compression": {}},
        ):
            manager.record_path.write_text(json.dumps(value), encoding="utf-8")
            assert manager.record_state() == "corrupt"
            assert not manager.wire(["main"], "127.0.0.1", 8787)["ok"]


def test_sdk_trailing_slash_does_not_report_pending_reload():
    with tempfile.TemporaryDirectory() as td:
        manager, host = make_manager(Path(td), sample_host())
        manager.wire(["main"], "127.0.0.1", 8787)
        notices = manager.doctor(
            proxy_healthy=True, runtime_client_api={"main": host["provider"][0]["api_base"] + "/"}
        )
        assert not notices


if __name__ == "__main__":
    fns = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\n全部通过：{len(fns)} 项")
