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
"""接管/恢复引擎：把对话 provider 的 api_base 指向 bili 代理并等效停用原生压缩。

安全设计：
- 拷贝后整条替换 provider 条目与 agent_runner 子树——配置文件里的条目与运行中
  provider 实例共享 dict 引用，原地改会造成「SDK 客户端仍旧地址、压缩阈值已变」
  的半生效状态；
- 接管记录只存被改字段的原值与写入值（绝不复制 API Key），恢复时三方比对
  （原值 / 我方写入值 / 当前值），用户接管后再改过的字段跳过不覆盖；
- 引擎不导入 astrbot 模块，宿主配置与运行时视图经参数注入（离线可测）。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

# 等效停用原生压缩的配方：不做轮数截断 + 不走 LLM 压缩器 + 抬高令牌触发阈值
INFLATED_MAX_CONTEXT_TOKENS = 10_000_000
RECIPE_MAX_TURNS = -1
RECIPE_OVERFLOW_STRATEGY = "truncate_by_turns"
RECORD_FILENAME = "bili_wire_record.json"
RECORD_VERSION = 1


def bili_prefix_url(host: str, port: int, upstream: str) -> str:
    return f"http://{host}:{port}/bili/{str(upstream).rstrip('/')}"


def is_wired_url(url: str) -> bool:
    return "/bili/" in str(url or "")


def _field(old_value, old_absent: bool, new_value) -> dict:
    return {"old": old_value, "new": new_value, "old_absent": old_absent}


class BiliWireManager:
    """provider 接管记录与配置改写。"""

    def __init__(self, host_config_get: Callable[[], dict], data_dir: Path):
        self._host_config_get = host_config_get
        self.record_path = Path(data_dir) / RECORD_FILENAME

    # ── 记录读写 ─────────────────────────────────────────────────────────

    def load_record(self) -> dict | None:
        try:
            data = json.loads(self.record_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") != RECORD_VERSION:
            return None
        providers = data.get("providers")
        compression = data.get("compression")
        if not isinstance(providers, dict) or not providers or not isinstance(compression, dict):
            return None
        groups = [(fields, {"api_base", "max_context_tokens"}) for fields in providers.values()]
        groups.append((compression, {"max_turns", "overflow_strategy"}))
        for fields, allowed in groups:
            if not isinstance(fields, dict) or set(fields) != allowed:
                return None
            for spec in fields.values():
                if not isinstance(spec, dict) or not {"old", "new", "old_absent"} <= spec.keys():
                    return None
                if not isinstance(spec["old_absent"], bool):
                    return None
        return data

    def record_state(self) -> str:
        """记录状态：``none`` 无记录；``ok`` 可读；``corrupt`` 损坏/不可读。

        损坏记录同样阻断接管（否则覆盖后无从恢复），恢复也拒绝执行。
        """
        if not self.record_path.exists():
            return "none"
        return "ok" if self.load_record() is not None else "corrupt"

    def record_exists(self) -> bool:
        return self.record_state() != "none"

    def record_summary(self) -> dict | None:
        record = self.load_record()
        if not record:
            return None
        return {
            "wired_at": record.get("wired_at"),
            "proxy_url": f"http://{record.get('host')}:{record.get('port')}",
            "providers": sorted((record.get("providers") or {}).keys()),
        }

    def _save_record(self, record: dict) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.record_path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.record_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary).unlink()

    def _delete_record(self) -> None:
        with contextlib.suppress(OSError):
            self.record_path.unlink()

    # ── 目标检测 ─────────────────────────────────────────────────────────

    @staticmethod
    def detect_targets(
        explicit: list[str],
        chat_provider_ids: list[str],
        using_provider_id: str | None,
    ) -> tuple[list[str], str]:
        """显式列表校验 / 懒人模式取当前生效对话 provider；歧义时返回空列表与原因。"""
        explicit = [pid.strip() for pid in explicit if str(pid).strip()]
        if explicit:
            known = set(chat_provider_ids)
            unknown = [pid for pid in explicit if pid not in known]
            if unknown:
                return [], (
                    f"以下 provider 不是对话类或不存在：{'、'.join(unknown)}。"
                    f"当前对话类 provider：{'、'.join(chat_provider_ids) or '（无）'}"
                )
            return explicit, ""
        if using_provider_id and using_provider_id in chat_provider_ids:
            return [using_provider_id], ""
        return [], (
            "无法唯一确定当前生效的对话 provider，"
            f"请在配置「provider_ids」中显式指定（候选：{'、'.join(chat_provider_ids) or '无'}）。"
        )

    # ── 接管 ─────────────────────────────────────────────────────────────

    def preview(self, provider_ids: list[str], host: str, port: int) -> dict:
        """计算接管 diff（不落盘）：applied 行与 skip 原因。"""
        host_config = self._host_config_get()
        entries = self._provider_entries(host_config)
        rows, skipped = [], []
        comp = self._compression_dict(host_config)
        rows.append(
            {
                "target": "agent_runner.config.compression",
                "field": "max_turns",
                "old": comp.get("max_turns"),
                "new": RECIPE_MAX_TURNS,
            }
        )
        rows.append(
            {
                "target": "agent_runner.config.compression",
                "field": "overflow_strategy",
                "old": comp.get("overflow_strategy"),
                "new": RECIPE_OVERFLOW_STRATEGY,
            }
        )
        for pid in provider_ids:
            index, entry = self._find_entry(entries, pid)
            if entry is None:
                skipped.append({"id": pid, "reason": "配置中不存在该 provider"})
                continue
            api_base = self._effective_api_base(host_config, entry)
            if not api_base:
                skipped.append(
                    {
                        "id": pid,
                        "reason": "实际生效地址为空（含 provider_source 继承），"
                        "请先在 WebUI 填写接口地址",
                    }
                )
                continue
            if is_wired_url(api_base):
                skipped.append({"id": pid, "reason": "api_base 已指向 bili 代理"})
                continue
            row = {
                "target": f"provider[{pid}]",
                "field": "api_base",
                "old": api_base,
                "new": bili_prefix_url(host, port, api_base),
            }
            if "api_base" not in entry:
                row["note"] = (
                    "继承自 provider_source，接管将物化为自身地址（恢复时移除覆盖、恢复继承）"
                )
            rows.append(row)
            rows.append(
                {
                    "target": f"provider[{pid}]",
                    "field": "max_context_tokens",
                    "old": entry.get("max_context_tokens"),
                    "new": INFLATED_MAX_CONTEXT_TOKENS,
                }
            )
        return {"changes": rows, "skipped": skipped}

    def wire(
        self,
        provider_ids: list[str],
        host: str,
        port: int,
        health_check: Callable[[], bool] | None = None,
    ) -> dict:
        """执行接管：先落盘恢复依据，再改配置并持久化；任一步失败整体回滚。

        已有（或损坏的）记录时拒绝——损坏记录同样阻断，避免覆盖后无从恢复。
        """
        provider_ids = list(dict.fromkeys(provider_ids))
        if health_check is not None and not health_check():
            return {"ok": False, "message": "bili 代理健康检查未通过，已拒绝接管。"}
        state = self.record_state()
        if state == "corrupt":
            return {
                "ok": False,
                "message": (
                    f"接管记录 {self.record_path} 已损坏，无法安全接管；"
                    "请先核对配置是否仍指向代理，修复或删除该文件后再试。"
                ),
            }
        if state == "ok":
            summary = self.record_summary()
            return {
                "ok": False,
                "message": (
                    f"已存在接管记录（{summary['wired_at']}，"
                    f"providers：{'、'.join(summary['providers'])}），请先「烤箱压缩恢复」再重新接管。"
                ),
            }
        preview = self.preview(provider_ids, host, port)
        if preview["skipped"] or not provider_ids:
            reasons = "；".join(f"{item['id']}: {item['reason']}" for item in preview["skipped"])
            return {"ok": False, "message": f"存在不可接管项，未做任何修改。{reasons}"}

        host_config = self._host_config_get()
        record: dict = {
            "version": RECORD_VERSION,
            "wired_at": datetime.now().isoformat(timespec="seconds"),
            "host": host,
            "port": port,
            "providers": {},
            "compression": {},
        }

        # 1) 恢复依据先行落盘：记录写不进去就绝不改配置
        entries = self._provider_entries(host_config)
        for pid in provider_ids:
            _, entry = self._find_entry(entries, pid)
            if entry is None:
                continue
            effective = self._effective_api_base(host_config, entry)
            record["providers"][pid] = {
                "api_base": _field(
                    entry.get("api_base", effective),
                    "api_base" not in entry,
                    bili_prefix_url(host, port, effective),
                ),
                "max_context_tokens": _field(
                    entry.get("max_context_tokens"),
                    "max_context_tokens" not in entry,
                    INFLATED_MAX_CONTEXT_TOKENS,
                ),
            }
        comp_now = self._compression_dict(host_config)
        record["compression"]["max_turns"] = _field(
            comp_now.get("max_turns"), "max_turns" not in comp_now, RECIPE_MAX_TURNS
        )
        record["compression"]["overflow_strategy"] = _field(
            comp_now.get("overflow_strategy"),
            "overflow_strategy" not in comp_now,
            RECIPE_OVERFLOW_STRATEGY,
        )
        try:
            self._save_record(record)
        except OSError as exc:
            return {"ok": False, "message": f"接管记录写入失败（{exc}），未修改任何配置。"}

        # 2) 改配置（拷贝后整条替换），失败则回滚内存并撤销记录
        backup_entries = list(entries)
        backup_runner = json.loads(json.dumps(host_config.get("agent_runner") or {}))
        try:
            for pid in provider_ids:
                index, entry = self._find_entry(entries, pid)
                if entry is None:
                    continue
                new_entry = dict(entry)
                new_entry["api_base"] = record["providers"][pid]["api_base"]["new"]
                new_entry["max_context_tokens"] = INFLATED_MAX_CONTEXT_TOKENS
                entries[index] = new_entry

            runner = dict(host_config.get("agent_runner") or {})
            runner_config = dict(runner.get("config") or {})
            comp = dict(runner_config.get("compression") or {})
            comp["max_turns"] = RECIPE_MAX_TURNS
            comp["overflow_strategy"] = RECIPE_OVERFLOW_STRATEGY
            runner_config["compression"] = comp
            runner["config"] = runner_config
            host_config["agent_runner"] = runner

            save = getattr(host_config, "save_config", None)
            if callable(save):
                save()
        except Exception as exc:  # noqa: BLE001
            entries[:] = backup_entries
            host_config["agent_runner"] = backup_runner
            return {
                "ok": False,
                "message": f"宿主配置持久化失败（{type(exc).__name__}），已回滚内存；"
                "恢复记录保留，请恢复后重试。",
            }
        return {
            "ok": True,
            "message": (
                f"已接管 {'、'.join(provider_ids)}：api_base 指向 http://{host}:{port}/bili/…，"
                "原生压缩已等效停用。请重载/重启 AstrBot 后生效；恢复用「烤箱压缩恢复」。"
            ),
            "changes": preview["changes"],
        }

    # ── 恢复 ─────────────────────────────────────────────────────────────

    def extend(self, provider_ids: list[str], host: str, port: int, health_check=None) -> dict:
        """Add chat models without restoring existing routes or losing their originals."""
        record = self.load_record()
        if self.record_state() == "none":
            return self.wire(provider_ids, host, port, health_check)
        if not record:
            return {"ok": False, "message": "接管记录损坏，已停止自动接管。"}
        if health_check is not None and not health_check():
            return {"ok": False, "message": "代理未就绪，已停止自动接管。"}
        if (record.get("host"), record.get("port")) != (host, port):
            return {"ok": False, "message": "代理地址与接管记录不同，请先恢复原接管。"}
        config = self._host_config_get()
        entries = self._provider_entries(config)
        for pid, fields in record["providers"].items():
            _, entry = self._find_entry(entries, pid)
            # Preserve deliberate edits instead of silently undoing them.
            if entry is not None and any(
                entry.get(key) != spec["new"] for key, spec in fields.items()
            ):
                return {"ok": False, "message": f"模型 {pid} 的接管字段已被修改，请先恢复或核对。"}
        for key, spec in record["compression"].items():
            if self._compression_dict(config).get(key) != spec["new"]:
                return {"ok": False, "message": "宿主压缩设置已被修改，请先核对后再自动接管。"}
        additions = list(
            dict.fromkeys(pid for pid in provider_ids if pid not in record["providers"])
        )
        if not additions:
            return {"ok": True, "message": "所需模型已接管。", "added": []}
        preview = self.preview(additions, host, port)
        if preview["skipped"]:
            return {"ok": False, "message": "新增模型地址不可接管，请核对 Provider 设置。"}
        expanded = json.loads(json.dumps(record))
        for pid in additions:
            _, entry = self._find_entry(entries, pid)
            effective = self._effective_api_base(config, entry)
            expanded["providers"][pid] = {
                "api_base": _field(
                    entry.get("api_base", effective),
                    "api_base" not in entry,
                    bili_prefix_url(host, port, effective),
                ),
                "max_context_tokens": _field(
                    entry.get("max_context_tokens"),
                    "max_context_tokens" not in entry,
                    INFLATED_MAX_CONTEXT_TOKENS,
                ),
            }
        try:
            self._save_record(expanded)
        except OSError:
            return {"ok": False, "message": "接管记录写入失败，未修改模型配置。"}
        previous = list(entries)
        try:
            for pid in additions:
                index, entry = self._find_entry(entries, pid)
                entries[index] = entry | {
                    key: spec["new"] for key, spec in expanded["providers"][pid].items()
                }
            save = getattr(config, "save_config", None)
            if callable(save):
                save()
        except Exception:
            entries[:] = previous
            # Keep the expanded recovery record even if saving partially wrote.
            # restore() accepts an already-original field and remains safe.
            return {"ok": False, "message": "模型配置保存失败，内存已回滚；恢复记录保留。"}
        return {"ok": True, "message": "已自动接管新增聊天模型。", "added": additions}

    def restore(self) -> dict:
        """三方比对恢复：当前值≠我方写入值的字段跳过（用户已再修改）。"""
        if self.record_state() == "corrupt":
            return {
                "ok": False,
                "message": (
                    f"接管记录 {self.record_path} 已损坏，无法自动恢复；"
                    "请按接管时修改的字段（api_base / 压缩三件套）手动核对配置。"
                ),
            }
        record = self.load_record()
        if not record:
            return {"ok": False, "message": "没有接管记录，无需恢复。"}
        host_config = self._host_config_get()
        entries = self._provider_entries(host_config)
        backup_entries = list(entries)
        backup_runner = host_config.get("agent_runner")
        restored: list[str] = []
        skipped: list[dict] = []

        def apply(container: dict, target: str, field: str, spec: dict) -> None:
            current = container.get(field)
            if current == spec["new"]:
                if spec.get("old_absent"):
                    container.pop(field, None)
                else:
                    container[field] = spec["old"]
                restored.append(f"{target}.{field}")
            elif (
                field not in container
                if spec.get("old_absent")
                else field in container and current == spec["old"]
            ):
                # A previous partial restore (or failed save) may already have restored it.
                restored.append(f"{target}.{field}")
            else:
                skipped.append(
                    {"field": f"{target}.{field}", "current": current, "expected": spec["new"]}
                )

        # provider 条目同样拷贝后替换
        entries = self._provider_entries(host_config)
        for pid, fields in (record.get("providers") or {}).items():
            index, entry = self._find_entry(entries, pid)
            if entry is None:
                skipped.append({"field": f"provider[{pid}]", "current": None, "expected": "存在"})
                continue
            new_entry = dict(entry)
            for field, spec in fields.items():
                if field not in ("api_base", "max_context_tokens"):
                    continue
                apply(new_entry, f"provider[{pid}]", field, spec)
            entries[index] = new_entry

        runner = dict(host_config.get("agent_runner") or {})
        runner_config = dict(runner.get("config") or {})
        comp = dict(runner_config.get("compression") or {})
        for field, spec in (record.get("compression") or {}).items():
            if field in ("max_turns", "overflow_strategy"):
                apply(comp, "agent_runner.config.compression", field, spec)
        runner_config["compression"] = comp
        runner["config"] = runner_config
        host_config["agent_runner"] = runner

        save = getattr(host_config, "save_config", None)
        try:
            if callable(save):
                save()
        except Exception as exc:
            entries[:] = backup_entries
            if backup_runner is None:
                host_config.pop("agent_runner", None)
            else:
                host_config["agent_runner"] = backup_runner
            return {
                "ok": False,
                "message": f"恢复配置写入失败（{type(exc).__name__}），"
                "内存已回滚，恢复记录保留，请重试。",
            }

        if skipped:
            message = (
                "部分字段已被再次修改，未覆盖："
                + "；".join(f"{item['field']}（当前 {item['current']!r}）" for item in skipped)
                + "。其余已恢复；接管记录保留，确认无误后可删除插件数据目录的 "
                f"{RECORD_FILENAME}。"
            )
            return {"ok": True, "restored": restored, "skipped": skipped, "message": message}
        self._delete_record()
        return {
            "ok": True,
            "restored": restored,
            "skipped": [],
            "message": "已全部恢复原配置，请重载/重启 AstrBot 后生效。",
        }

    # ── 巡检 ─────────────────────────────────────────────────────────────

    def doctor(
        self,
        *,
        proxy_healthy: bool,
        runtime_client_api: dict[str, str] | None = None,
        fallback_ids: list[str] | None = None,
        active_ids: list[str] | None = None,
        auto_follow: bool = False,
    ) -> list[str]:
        """同步巡检告警（供状态命令/面板通知；不发起网络请求）。"""
        notices: list[str] = []
        if self.record_state() == "corrupt":
            notices.append(f"接管记录已损坏（{self.record_path}），请修复或删除后再操作。")
            return notices
        record = self.load_record()
        if not record:
            return notices
        wired = set((record.get("providers") or {}).keys())
        unwired_active = list(
            dict.fromkeys(pid for pid in (active_ids or []) if pid and pid not in wired)
        )
        if unwired_active:
            if auto_follow:
                notices.append(
                    f"当前对话模型（{'、'.join(unwired_active)}）等待下一次聊天请求前自动接管。"
                )
            else:
                notices.append(
                    f"当前对话模型未接管（{'、'.join(unwired_active)}）：主聊天正在绕过 bili。"
                    "若需继续压缩，请恢复后将新模型加入接管列表，再重新接管并重载对应 Provider。"
                )
        if not proxy_healthy:
            notices.append(
                "bili 代理当前不可用：被接管 provider 的请求将失败，请先恢复或修复代理。"
            )
        host_config = self._host_config_get()
        entries = self._provider_entries(host_config)
        for pid in wired:
            _, entry = self._find_entry(entries, pid)
            if entry is None:
                notices.append(f"接管记录中的 provider {pid} 已从配置移除，请恢复或清理记录。")
                continue
            if not is_wired_url(entry.get("api_base") or ""):
                notices.append(f"provider {pid} 的 api_base 已被改回，与接管记录不一致。")
        if runtime_client_api:
            pending = [
                pid
                for pid in wired
                if pid in runtime_client_api
                and str(runtime_client_api.get(pid) or "").rstrip("/")
                != str(
                    (record.get("providers") or {}).get(pid, {}).get("api_base", {}).get("new")
                ).rstrip("/")
            ]
            if pending:
                notices.append(
                    f"配置已接管但运行中 Provider 尚未重载（{'、'.join(pending)}），"
                    "请重载/重启 AstrBot。"
                )
        unwired_fallback = [pid for pid in (fallback_ids or []) if pid and pid not in wired]
        if unwired_fallback:
            notices.append(
                f"被接管 provider 的备用链含未接管项（{'、'.join(unwired_fallback)}）："
                "代理不可用时完整历史将直送上游，建议一并接管或移除备用链。"
            )
        return notices

    # ── 工具 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _provider_entries(host_config: dict) -> list:
        entries = host_config.get("provider")
        return entries if isinstance(entries, list) else []

    @staticmethod
    def _effective_api_base(host_config: dict, entry: dict) -> str:
        """实际生效地址：entry 自身 ``api_base`` 优先，否则按 ``provider_source_id``
        从 ``provider_sources`` 继承（合并语义与 AstrBot ``get_merged_provider_config``
        一致：entry 键覆盖 source）。绝不修改共享的 source 条目。
        """
        if "api_base" in entry:
            return str(entry["api_base"] or "").strip()
        source_id = str(entry.get("provider_source_id") or "")
        if source_id:
            for source in host_config.get("provider_sources") or []:
                if isinstance(source, dict) and source.get("id") == source_id:
                    return str(source.get("api_base") or "").strip()
        return ""

    @staticmethod
    def _find_entry(entries: list, pid: str) -> tuple[int, dict | None]:
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("id") == pid:
                return index, entry
        return -1, None

    @staticmethod
    def _compression_dict(host_config: dict) -> dict:
        comp = ((host_config.get("agent_runner") or {}).get("config") or {}).get("compression")
        return comp if isinstance(comp, dict) else {}
