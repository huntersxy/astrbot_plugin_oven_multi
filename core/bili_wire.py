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


def strip_bili_prefix(url: str) -> str:
    """Return the upstream URL embedded after ``/bili/``, or the original string."""
    text = str(url or "").strip()
    marker = "/bili/"
    if marker in text:
        return text.split(marker, 1)[1].rstrip("/")
    return text.rstrip("/")


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

    def repair(self, host: str, port: int, health_check=None) -> dict:
        """修复漂移：清理已删除模型，并把仍在的模型重新指回当前代理。

        保留首次接管时的 ``old``（撤销依据）；若当前地址已是新的上游明文，
        则把 ``old`` 更新为该上游，使之后「恢复」回到最新上游而非陈旧地址。
        """
        if self.record_state() == "corrupt":
            return {
                "ok": False,
                "message": (
                    f"接管记录 {self.record_path} 已损坏，无法自动修复；"
                    "可删除该文件后重新接管，或手动核对 Provider 地址。"
                ),
            }
        record = self.load_record()
        if not record:
            return {"ok": False, "message": "没有接管记录，无需修复。"}
        if health_check is not None and not health_check():
            return {"ok": False, "message": "代理未就绪，已拒绝修复。"}

        host_config = self._host_config_get()
        entries = self._provider_entries(host_config)
        backup_entries = list(entries)
        backup_runner = host_config.get("agent_runner")
        expanded = json.loads(json.dumps(record))
        expanded["host"] = host
        expanded["port"] = port
        removed: list[str] = []
        rewired: list[str] = []
        unchanged: list[str] = []

        for pid, fields in list((expanded.get("providers") or {}).items()):
            index, entry = self._find_entry(entries, pid)
            if entry is None:
                expanded["providers"].pop(pid, None)
                removed.append(pid)
                continue
            current = str(entry.get("api_base") or "").strip()
            recorded_new = str((fields.get("api_base") or {}).get("new") or "").rstrip("/")
            recorded_old = str((fields.get("api_base") or {}).get("old") or "").rstrip("/")
            if is_wired_url(current):
                upstream = strip_bili_prefix(current) or recorded_old
            elif current:
                upstream = current.rstrip("/")
            else:
                upstream = recorded_old or strip_bili_prefix(recorded_new)
            target = bili_prefix_url(host, port, upstream)
            old_absent = bool((fields.get("api_base") or {}).get("old_absent"))
            # Prefer the live upstream as the restore baseline when it is plain text.
            if current and not is_wired_url(current):
                old_value, old_absent = current, False
            else:
                old_value = (fields.get("api_base") or {}).get("old")
            token_spec = fields.get("max_context_tokens") or _field(
                entry.get("max_context_tokens"),
                "max_context_tokens" not in entry,
                INFLATED_MAX_CONTEXT_TOKENS,
            )
            expanded["providers"][pid] = {
                "api_base": _field(old_value, old_absent, target),
                "max_context_tokens": {
                    "old": token_spec.get("old"),
                    "new": INFLATED_MAX_CONTEXT_TOKENS,
                    "old_absent": bool(token_spec.get("old_absent")),
                },
            }
            needs_write = (
                current.rstrip("/") != target.rstrip("/")
                or entry.get("max_context_tokens") != INFLATED_MAX_CONTEXT_TOKENS
            )
            if needs_write:
                entries[index] = entry | {
                    "api_base": target,
                    "max_context_tokens": INFLATED_MAX_CONTEXT_TOKENS,
                }
                rewired.append(pid)
            else:
                unchanged.append(pid)

        # Keep compression recipe aligned with the wire contract.
        runner = dict(host_config.get("agent_runner") or {})
        runner_config = dict(runner.get("config") or {})
        comp = dict(runner_config.get("compression") or {})
        compression = dict(expanded.get("compression") or {})
        compression_changed = False
        for field, value in (
            ("max_turns", RECIPE_MAX_TURNS),
            ("overflow_strategy", RECIPE_OVERFLOW_STRATEGY),
        ):
            previous = compression.get(field) or _field(comp.get(field), field not in comp, value)
            compression[field] = {
                "old": previous.get("old"),
                "new": value,
                "old_absent": bool(previous.get("old_absent")),
            }
            if comp.get(field) != value:
                compression_changed = True
                comp[field] = value
        expanded["compression"] = compression
        if compression_changed:
            runner_config["compression"] = comp
            runner["config"] = runner_config
            host_config["agent_runner"] = runner

        if not expanded.get("providers"):
            # Nothing left to keep wired — fall back to a full restore.
            try:
                self._save_record(expanded)
            except OSError:
                return {"ok": False, "message": "接管记录写入失败，未修改配置。"}
            result = self.restore()
            if result["ok"]:
                result["message"] = (
                    "记录中的模型均已不存在，已恢复宿主压缩设置并清除接管记录。"
                    "请重载/重启 AstrBot 后生效。"
                )
                result["removed"] = removed
            return result

        try:
            self._save_record(expanded)
        except OSError:
            entries[:] = backup_entries
            if backup_runner is None:
                host_config.pop("agent_runner", None)
            else:
                host_config["agent_runner"] = backup_runner
            return {"ok": False, "message": "接管记录写入失败，未修改配置。"}

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
                "message": f"修复写入失败（{type(exc).__name__}），内存已回滚，请重试。",
            }

        parts = []
        if rewired:
            parts.append(f"已重新指向代理：{'、'.join(rewired)}")
        if removed:
            parts.append(f"已清理不存在的模型：{'、'.join(removed)}")
        if unchanged and not rewired and not removed and not compression_changed:
            parts.append("接管状态已一致，无需改动")
        elif unchanged and (rewired or removed):
            parts.append(f"保持不变：{len(unchanged)} 个")
        if compression_changed:
            parts.append("已校正原生压缩停用设置")
        parts.append("请重载/重启 AstrBot 或对应 Provider 后生效")
        return {
            "ok": True,
            "rewired": rewired,
            "removed": removed,
            "unchanged": unchanged,
            "message": "；".join(parts) + "。",
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
            notices.append(
                f"接管记录已损坏（{self.record_path}）。"
                "可删除该文件后点「接管」，或手动核对 Provider 的 api_base。"
            )
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
                    f"当前对话模型（{'、'.join(unwired_active)}）将在下一次聊天前自动接管；"
                    "若长时间不变，可点「修复接管」。"
                )
            else:
                notices.append(
                    f"当前对话模型未接管（{'、'.join(unwired_active)}），聊天会绕过压缩代理。"
                    "点「修复接管」按当前模型补接管；若要彻底停用压缩，点「恢复」。"
                )
        if not proxy_healthy:
            notices.append(
                "压缩代理当前不可用，被接管模型的请求会失败。"
                "请先点「安装／重试」修好代理；若要临时直连上游，点「恢复」撤销接管。"
            )
        host_config = self._host_config_get()
        entries = self._provider_entries(host_config)
        missing = []
        drifted = []
        for pid in wired:
            _, entry = self._find_entry(entries, pid)
            if entry is None:
                missing.append(pid)
                continue
            expected = str(
                (record.get("providers") or {}).get(pid, {}).get("api_base", {}).get("new") or ""
            ).rstrip("/")
            current = str(entry.get("api_base") or "").rstrip("/")
            if current != expected or not is_wired_url(current):
                drifted.append(pid)
        if missing:
            notices.append(
                f"这些模型已从 AstrBot 配置删除，但仍留在接管记录："
                f"{'、'.join(missing)}。点「修复接管」清理记录；"
                "点「恢复」则会撤销全部接管并还原其余模型地址。"
            )
        if drifted:
            notices.append(
                f"这些模型的 api_base 与接管记录不一致："
                f"{'、'.join(drifted)}。点「修复接管」重新指回压缩代理；"
                "若你本意是停用压缩，点「恢复」还原接管前地址。"
            )
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
                    f"配置已接管，但运行中的 Provider 尚未加载新地址（{'、'.join(pending)}）。"
                    "请重载对应 Provider 或重启 AstrBot；这与「恢复」无关。"
                )
        unwired_fallback = [pid for pid in (fallback_ids or []) if pid and pid not in wired]
        if unwired_fallback:
            notices.append(
                f"备用链含未接管模型（{'、'.join(unwired_fallback)}）："
                "代理挂了时完整历史会直送上游。点「修复接管」可尝试一并接管，"
                "或到宿主配置里移除备用链。"
            )
        return notices

    def repair_needed(
        self,
        *,
        runtime_client_api: dict[str, str] | None = None,
        fallback_ids: list[str] | None = None,
        active_ids: list[str] | None = None,
        auto_follow: bool = False,
    ) -> bool:
        """Whether the panel should offer the one-click repair action."""
        if self.record_state() != "ok":
            return False
        record = self.load_record() or {}
        wired = set((record.get("providers") or {}).keys())
        entries = self._provider_entries(self._host_config_get())
        for pid in wired:
            _, entry = self._find_entry(entries, pid)
            if entry is None:
                return True
            expected = str(
                (record.get("providers") or {}).get(pid, {}).get("api_base", {}).get("new") or ""
            ).rstrip("/")
            current = str(entry.get("api_base") or "").rstrip("/")
            if current != expected or not is_wired_url(current):
                return True
        if any(pid and pid not in wired for pid in (active_ids or [])):
            return True
        if any(pid and pid not in wired for pid in (fallback_ids or [])):
            return True
        return False

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
