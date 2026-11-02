# Copyright (C) 2026 汐兮雨
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
# ruff: noqa: S608 — 表名与子句均为本模块常量，无外部输入拼接
"""只读的 token 用量与花费统计。

数据来源优先级：
1. astrbot_plugin_cost_control 的 supplement.db（含按价格表算出的费用）；
2. AstrBot 核心 provider_stats 表（只有 token，没有费用）。

两个库都以只读方式打开（``mode=ro``），不写入、不加锁、不修改任何数据。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import logger

COST_PLUGIN_NAME = "astrbot_plugin_cost_control"
COST_DB_NAME = "supplement.db"
CORE_DB_NAME = "data_v4.db"

_SCOPE_LABELS = {
    "session": "本会话",
    "today": "本会话今天",
    "all": "全部会话",
}


@dataclass
class Totals:
    requests: int = 0
    input_other: int = 0
    input_cached: int = 0
    output: int = 0
    cost: float = 0.0
    priced: int = 0
    currencies: set[str] = field(default_factory=set)
    models: list[dict] = field(default_factory=list)

    @property
    def input_total(self) -> int:
        return self.input_other + self.input_cached

    @property
    def total(self) -> int:
        return self.input_total + self.output

    @property
    def cache_rate(self) -> float:
        return self.input_cached / self.input_total if self.input_total else 0.0


class UsageStatsReader:
    """把用量查询收敛成一次只读 SQL，并对缺库 / 读失败给出可读结果。"""

    def __init__(self, data_dir: Path, cfg: dict | None = None):
        self.data_dir = Path(data_dir)
        self.cfg = cfg if isinstance(cfg, dict) else {}
        override = str(self.cfg.get("db_path") or "").strip()
        # data_dir = <AstrBot>/data/plugin_data/astrbot_plugin_oven_multi
        plugin_data = self.data_dir.parent
        self.cost_db = Path(override) if override else plugin_data / COST_PLUGIN_NAME / COST_DB_NAME
        self.core_db = plugin_data.parent / CORE_DB_NAME

    def summarize(self, umo: str, scope: str = "session", breakdown: bool = False) -> str:
        scope = scope if scope in _SCOPE_LABELS else "session"
        since = self._since(scope)
        # all = 整个机器人的所有会话；其余只统计当前会话
        target = "" if scope == "all" else str(umo or "")
        try:
            if self.cost_db.is_file():
                totals = self._read(
                    self.cost_db, "cost_supplements", target, since, True, breakdown
                )
                source = "token 统计插件（supplement.db）"
            elif self.core_db.is_file():
                totals = self._read(self.core_db, "provider_stats", target, since, False, breakdown)
                source = "AstrBot provider_stats"
            else:
                return "用量统计不可用：既没有 token 统计插件的库，也没有 AstrBot 的统计库"
        except sqlite3.Error as exc:
            logger.warning(f"[烤箱-用量统计] 读取失败: {exc}")
            return f"用量统计读取失败：{exc}"
        return self._render(totals, scope, source)

    def _since(self, scope: str) -> str | None:
        if scope != "today":
            return None
        try:
            offset = float(self.cfg.get("timezone_offset", 8))
        except (TypeError, ValueError):
            offset = 8.0
        offset = max(-12.0, min(14.0, offset))
        tz = timezone(timedelta(hours=offset))
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        # created_at 以 UTC naive 字符串存储，可直接按字典序比较
        return start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _read(
        self,
        path: Path,
        table: str,
        umo: str,
        since: str | None,
        priced: bool,
        breakdown: bool,
    ) -> Totals:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            where: list[str] = []
            params: list[str] = []
            if umo:
                where.append("umo = ?")
                params.append(umo)
            if since:
                where.append("created_at >= ?")
                params.append(since)
            clause = f" WHERE {' AND '.join(where)}" if where else ""

            cols = (
                "COUNT(*), COALESCE(SUM(token_input_other), 0), "
                "COALESCE(SUM(token_input_cached), 0), COALESCE(SUM(token_output), 0)"
            )
            if priced:
                cols += (
                    ", COALESCE(SUM(cost_amount), 0), COUNT(cost_amount), "
                    "COALESCE(GROUP_CONCAT(DISTINCT currency_symbol), '')"
                )
            # table/clause 均为本模块内部常量，不含用户输入
            row = conn.execute(
                f"SELECT {cols} FROM {table}{clause}",
                params,  # noqa: S608
            ).fetchone()
            totals = Totals(
                requests=int(row[0] or 0),
                input_other=int(row[1] or 0),
                input_cached=int(row[2] or 0),
                output=int(row[3] or 0),
            )
            if priced:
                totals.cost = float(row[4] or 0.0)
                totals.priced = int(row[5] or 0)
                totals.currencies = {
                    item.strip() for item in str(row[6] or "").split(",") if item.strip()
                }
            if breakdown and totals.requests:
                totals.models = self._models(conn, table, clause, params, priced)
            return totals
        finally:
            conn.close()

    def _models(
        self, conn: sqlite3.Connection, table: str, clause: str, params: list[str], priced: bool
    ) -> list[dict]:
        cols = (
            "provider_model, COUNT(*), COALESCE(SUM(token_input_other), 0), "
            "COALESCE(SUM(token_input_cached), 0), COALESCE(SUM(token_output), 0)"
        )
        cols += ", COALESCE(SUM(cost_amount), 0)" if priced else ""
        order = " ORDER BY 6 DESC" if priced else " ORDER BY 2 DESC"
        sql = (  # noqa: S608 - 同上，全部为内部常量
            f"SELECT {cols} FROM {table}{clause} GROUP BY provider_model{order} LIMIT 5"
        )
        out: list[dict] = []
        for row in conn.execute(sql, params):
            out.append(
                {
                    "model": row[0] or "未知模型",
                    "requests": int(row[1] or 0),
                    "tokens": int(row[2] or 0) + int(row[3] or 0) + int(row[4] or 0),
                    "cost": float(row[5] or 0.0) if priced else None,
                }
            )
        return out

    def _render(self, totals: Totals, scope: str, source: str) -> str:
        label = _SCOPE_LABELS[scope]
        if not totals.requests:
            return f"{label}暂无用量记录（数据源：{source}）"

        symbol = "$" if totals.currencies <= {"USD", "$", ""} else ""
        lines = [f"用量统计 · {label}", f"请求 {totals.requests:,} 次"]
        hit = (
            f"（缓存命中 {totals.input_cached:,}，{totals.cache_rate:.1%}）"
            if totals.input_cached
            else ""
        )
        lines.append(f"输入 {totals.input_total:,} tok{hit}")
        lines.append(f"输出 {totals.output:,} tok")
        lines.append(f"合计 {totals.total:,} tok")
        if totals.priced:
            amount = f"{totals.cost:.4f}"
            currency = "/".join(sorted(totals.currencies)) or "USD"
            lines.append(f"费用 {symbol}{amount}" if symbol else f"费用 {amount} {currency}")
            if 0 < totals.priced < totals.requests:
                lines.append(
                    f"注：{totals.requests - totals.priced} 次请求未匹配到价格，实际花费可能更高"
                )
        else:
            lines.append("费用 未知（未接入 token 统计插件）")
        if totals.models:
            lines.append("按模型：")
            for item in totals.models:
                cost = f"，{symbol}{item['cost']:.4f}" if item["cost"] is not None else ""
                lines.append(
                    f"- {item['model']}：{item['requests']} 次，{item['tokens']:,} tok{cost}"
                )
        lines.append(f"数据源：{source} · 截至 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
        return "\n".join(lines)
