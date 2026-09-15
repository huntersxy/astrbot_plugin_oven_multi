# Modified from astrbot_plugin_file_reader_pro (MIT) by zz6zz666
# https://github.com/zz6zz666/astrbot_plugin_file_reader_pro
"""文件读取管理器：文件登记、预读取、RAG 检索与 LLM Tool 支持。

相对原版的调整：
- 文件副本保留到数据目录并在有效期内可反复使用（原版向量化后即删除源文件，
  且只有完成向量化的文件才可读）；
- 预读取、完成通知、LLM Tool 均为独立开关；
- 不再依赖 sqlite 记录使用轮数，改为内存计数（重启归零，语义更简单）；
- 过期清理支持定时任务与按需触发两种方式。
"""

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from .parsers import FileParseError, read_any_file_to_text, supported_extensions
from .rag import RAGIndex, safe_dir_name


@dataclass
class FileRecord:
    """会话内一个已登记文件的元信息。"""

    name: str
    path: str
    size: int
    uploaded_at: float = field(default_factory=time.time)
    rounds: int = 0  # 已参与回答的轮数


class FileReaderManager:
    """编排文件接收、预读取（向量化）、检索与 tool 读取。"""

    def __init__(self, plugin: Any, config: Dict[str, Any], data_dir: Path):
        self.plugin = plugin
        self.config = config or {}
        self.data_dir = Path(data_dir)
        self.files_dir = self.data_dir / "files"
        self.vec_dir = self.data_dir / "vec"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.vec_dir.mkdir(parents=True, exist_ok=True)

        self.max_file_size_mb = float(self.config.get("max_file_size_mb", 100) or 100)
        self.retention_minutes = float(self.config.get("retention_minutes", 60) or 60)
        self.max_rounds = int(self.config.get("max_rounds", 5) or 5)
        self.cleanup_interval = float(self.config.get("cleanup_interval_minutes", 15) or 15)

        preread_cfg = self.config.get("preread") or {}
        self.preread_enabled = bool(preread_cfg.get("enabled", True))
        self.preread_notify = bool(preread_cfg.get("notify", True))

        tool_cfg = self.config.get("tool") or {}
        self.tool_enabled = bool(tool_cfg.get("enabled", True))
        self.tool_max_chars = int(tool_cfg.get("max_chars", 12000) or 12000)

        rag_cfg = self.config.get("rag") or {}
        self.rag_enabled = bool(rag_cfg.get("enabled", True))
        self.injection_type = str(rag_cfg.get("injection_type", "temp_part") or "temp_part")
        self.retrieve_top_k = int(rag_cfg.get("retrieve_top_k", 5) or 5)
        self.fetch_k = int(rag_cfg.get("fetch_k", 20) or 20)
        self.enable_rerank = bool(rag_cfg.get("enable_rerank", True))

        allowed = self.config.get("supported_file_types") or []
        self.allowed_ext: Optional[set] = (
            {str(e).lower().lstrip(".") for e in allowed} if allowed else None
        )

        # session_id -> {文件名: FileRecord}
        self.records: Dict[str, Dict[str, FileRecord]] = {}

        self.embedding_provider: Any = None
        self.rerank_provider: Any = None
        self.rag = RAGIndex(
            self.vec_dir,
            chunk_size=int(rag_cfg.get("chunk_size", 512) or 512),
            chunk_overlap=int(rag_cfg.get("chunk_overlap", 100) or 100),
        )
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """初始化 embedding/rerank 提供者并启动定期清理。"""
        if self.rag_enabled:
            self._init_providers()
            self.rag.embedding_provider = self.embedding_provider
            self.rag.rerank_provider = self.rerank_provider
            if not self.rag.available:
                logger.warning(
                    "[烤箱-文件读取] RAG 不可用（缺少 embedding 提供者或内核组件），"
                    "预读取向量化与语义检索将跳过，全文读取与 Tool 不受影响"
                )
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        await self.rag.close()

    def _init_providers(self) -> None:
        """从上下文获取 embedding / rerank 提供者（失败不影响其余功能）。"""
        ctx = getattr(self.plugin, "context", None)
        if ctx is None:
            return
        try:
            provider_id = str(self.config.get("rag", {}).get("embedding_provider_id", "") or "")
            if provider_id:
                self.embedding_provider = ctx.get_provider_by_id(provider_id)
            if not self.embedding_provider:
                for provider in ctx.get_all_embedding_providers():
                    if hasattr(provider, "get_embedding"):
                        self.embedding_provider = provider
                        break
        except Exception as exc:
            logger.error(f"[烤箱-文件读取] 获取 embedding 提供者失败: {exc}")
        try:
            rerank_id = str(self.config.get("rag", {}).get("rerank_provider_id", "") or "")
            if rerank_id:
                self.rerank_provider = ctx.get_provider_by_id(rerank_id)
            if not self.rerank_provider:
                rerank_insts = getattr(ctx.provider_manager, "rerank_provider_insts", []) or []
                for provider in rerank_insts:
                    if hasattr(provider, "rerank"):
                        self.rerank_provider = provider
                        break
        except Exception as exc:
            logger.warning(f"[烤箱-文件读取] 未找到 rerank 提供者: {exc}")

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(max(self.cleanup_interval, 1) * 60)
            try:
                await self.cleanup_expired()
            except Exception as exc:
                logger.error(f"[烤箱-文件读取] 定期清理失败: {exc}")

    # ── 文件接收与预读取 ──────────────────────────────────────────────────

    def _session_dir(self, session_id: str) -> Path:
        d = self.files_dir / safe_dir_name(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _is_expired(self, record: FileRecord) -> bool:
        if self.retention_minutes > 0 and (
            time.time() - record.uploaded_at
        ) > self.retention_minutes * 60:
            return True
        if self.max_rounds > 0 and record.rounds >= self.max_rounds:
            return True
        return False

    async def cleanup_expired(self) -> None:
        """清理过期文件（磁盘副本 + 索引 + 登记）。"""
        for session_id in list(self.records):
            for name in list(self.records[session_id]):
                record = self.records[session_id][name]
                if self._is_expired(record):
                    await self.remove_file(session_id, name)

    async def ingest(self, session_id: str, src_path: str, file_name: str) -> Optional[str]:
        """接收一个文件：登记副本，并按开关决定是否预读取向量化。

        Returns:
            通知文本；无需通知时返回 None。

        Raises:
            FileParseError: 文件超限或类型不支持等前置失败。
        """
        file_name = os.path.basename(file_name or src_path)
        size = os.path.getsize(src_path)
        if size > self.max_file_size_mb * 1024 * 1024:
            raise FileParseError(
                f"文件 {file_name} 过大（{size / 1024 / 1024:.1f}MB > {self.max_file_size_mb:.0f}MB）"
            )

        ext = os.path.splitext(file_name)[1][1:].lower()
        allowed = self.allowed_ext if self.allowed_ext is not None else supported_extensions()
        if ext and ext not in allowed:
            raise FileParseError(f"不支持的文件类型: {ext}")

        dst = self._session_dir(session_id) / file_name
        await asyncio.to_thread(shutil.copy2, src_path, dst)

        self.records.setdefault(session_id, {})[file_name] = FileRecord(
            name=file_name, path=str(dst), size=size
        )
        logger.info(f"[烤箱-文件读取] 已登记文件 {file_name}（{size / 1024:.0f}KB）@ {session_id}")

        if not self.preread_enabled:
            return None
        if not self.rag_enabled or not self.rag.available:
            return f"已接收文件：{file_name}（RAG 未启用，可通过工具按需读取）"

        try:
            text = await asyncio.to_thread(read_any_file_to_text, dst, self.allowed_ext)
        except FileParseError as exc:
            return f"文件 {file_name} 解析失败：{exc}"
        if not text:
            return f"文件 {file_name} 内容为空"

        ok = await self.rag.add_file(session_id, file_name, text)
        if ok:
            return f"文件 {file_name} 已预处理完毕，可直接向我提问相关内容"
        return f"文件 {file_name} 已接收，但向量化失败，可通过工具读取全文"

    # ── 检索与注入 ────────────────────────────────────────────────────────

    async def _ensure_indexed(self, session_id: str) -> None:
        """懒惰补建索引：为已登记但未向量化的文件补建（供 tool 检索使用）。"""
        if not (self.rag_enabled and self.rag.available):
            return
        for name, record in list(self.records.get(session_id, {}).items()):
            if await self.rag.has_file(session_id, name):
                continue
            try:
                text = await asyncio.to_thread(read_any_file_to_text, record.path, self.allowed_ext)
            except FileParseError:
                continue
            if text:
                await self.rag.add_file(session_id, name, text)

    async def build_context(self, session_id: str, query: str) -> Optional[str]:
        """检索当前会话文件并构建上下文文本；无内容时返回 None。"""
        if not (self.rag_enabled and self.rag.available and query):
            return None
        self._bump_rounds(session_id)
        await self.cleanup_expired_if_needed(session_id)
        active = [
            name
            for name, record in self.records.get(session_id, {}).items()
            if not self._is_expired(record) and await self.rag.has_file(session_id, name)
        ]
        if not active:
            return None
        results = await self.rag.retrieve(
            session_id,
            query,
            file_keys=active,
            top_k=self.retrieve_top_k,
            fetch_k=self.fetch_k,
            rerank=self.enable_rerank,
        )
        if not results:
            return None
        lines = ["以下是与当前对话相关的文件内容片段："]
        for i, (name, chunk) in enumerate(results, 1):
            lines.append(f"【文件 {name} 片段 {i}】\n{chunk}")
        lines.append("请根据上述内容回答用户问题；若内容不足请如实说明。")
        return "\n".join(lines)

    async def cleanup_expired_if_needed(self, session_id: str) -> None:
        for name in list(self.records.get(session_id, {})):
            if self._is_expired(self.records[session_id][name]):
                await self.remove_file(session_id, name)

    def _bump_rounds(self, session_id: str) -> None:
        for record in self.records.get(session_id, {}).values():
            record.rounds += 1

    # ── Tool 支持 ────────────────────────────────────────────────────────

    def list_files(self, session_id: str) -> str:
        records = self.records.get(session_id) or {}
        alive = {n: r for n, r in records.items() if not self._is_expired(r)}
        if not alive:
            return "当前会话暂无可用文件。请让用户先发送文件。"
        lines = [f"当前会话共有 {len(alive)} 个文件："]
        for name, r in sorted(alive.items()):
            lines.append(f"- {name}（{r.size / 1024:.0f}KB，剩余可用约 {self._rounds_left(r)} 轮）")
        return "\n".join(lines)

    def _rounds_left(self, record: FileRecord) -> str:
        if self.max_rounds <= 0:
            return "不限"
        return str(max(self.max_rounds - record.rounds, 0))

    async def read_file_text(self, session_id: str, file_name: str) -> str:
        """按需解析并返回文件全文（截断到 tool_max_chars）。"""
        record = (self.records.get(session_id) or {}).get(file_name)
        if not record or self._is_expired(record):
            listed = self.list_files(session_id)
            return f"文件 {file_name} 不存在或已过期。\n{listed}"
        self._bump_rounds(session_id)
        try:
            text = await asyncio.to_thread(
                read_any_file_to_text, record.path, self.allowed_ext
            )
        except FileParseError as exc:
            return f"读取 {file_name} 失败：{exc}"
        if not text:
            return f"文件 {file_name} 内容为空"
        if len(text) > self.tool_max_chars:
            return text[: self.tool_max_chars] + f"\n……（内容过长，已截断，原文件共 {len(text)} 字符）"
        return text

    async def search_text(self, session_id: str, query: str) -> str:
        """语义检索会话内文件（未索引的文件自动补建索引）。"""
        if not (self.rag_enabled and self.rag.available):
            return "语义检索不可用：RAG 未启用或缺少 embedding 提供者。可改用 file_read 读取全文。"
        if not query.strip():
            return "检索关键词为空"
        self._bump_rounds(session_id)
        await self._ensure_indexed(session_id)
        await self.cleanup_expired_if_needed(session_id)
        active = [
            name
            for name, record in self.records.get(session_id, {}).items()
            if not self._is_expired(record)
        ]
        if not active:
            return "当前会话暂无可用文件"
        results = await self.rag.retrieve(
            session_id, query, file_keys=active, top_k=self.retrieve_top_k,
            fetch_k=self.fetch_k, rerank=self.enable_rerank,
        )
        if not results:
            return f"未在当前会话文件中检索到与「{query}」相关的内容"
        lines = [f"检索「{query}」的相关片段："]
        for i, (name, chunk) in enumerate(results, 1):
            lines.append(f"【{name} 片段 {i}】\n{chunk}")
        return "\n".join(lines)

    # ── 清理 ─────────────────────────────────────────────────────────────

    async def remove_file(self, session_id: str, file_name: str) -> None:
        record = (self.records.get(session_id) or {}).pop(file_name, None)
        if record:
            try:
                if os.path.isfile(record.path):
                    os.remove(record.path)
            except OSError as exc:
                logger.warning(f"[烤箱-文件读取] 删除文件副本失败: {exc}")
        await self.rag.remove_file(session_id, file_name)

    async def clear_session(self, session_id: str) -> int:
        """清空会话的全部文件与索引，返回清理数量。"""
        count = len(self.records.pop(session_id, {}))
        await self.rag.remove_session(session_id)
        session_dir = self.files_dir / safe_dir_name(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        return count
