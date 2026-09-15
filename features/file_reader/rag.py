# Modified from astrbot_plugin_file_reader_pro (MIT) by zz6zz666
# https://github.com/zz6zz666/astrbot_plugin_file_reader_pro
"""RAG 索引：语义分块 + FAISS 向量库。

依赖 AstrBot 内核组件（RecursiveCharacterChunker / FaissVecDB），缺失时整体降级为不可用，
文件读取的其余功能（预读取登记、tool 全文读取）不受影响。

相对原版的调整：
- 目录结构按会话隔离（会话内不再按对话分层），文件读取不随对话切换失效；
- Windows 下目录名中的非法字符（如冒号）会被替换；
- 每文件独立向量库，过期清理时直接删除对应目录。
"""

import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

try:
    from astrbot.core.knowledge_base.chunking.recursive import RecursiveCharacterChunker
    from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

    RAG_AVAILABLE = True
except Exception as _exc:  # 内核组件缺失或 API 变动
    RecursiveCharacterChunker = None  # type: ignore[assignment]
    FaissVecDB = None  # type: ignore[assignment]
    RAG_AVAILABLE = False
    logger.warning(f"[烤箱-文件读取] RAG 组件不可用，将退回全文读取模式: {_exc}")


def safe_dir_name(name: str) -> str:
    """将会话 ID 等字符串转换为跨平台安全的目录名。"""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) or "_"


class RAGIndex:
    """按会话管理多个文件级向量库。"""

    def __init__(
        self,
        base_dir: Path,
        embedding_provider: Any = None,
        rerank_provider: Any = None,
        chunk_size: int = 512,
        chunk_overlap: int = 100,
    ):
        self.base_dir = Path(base_dir)
        self.embedding_provider = embedding_provider
        self.rerank_provider = rerank_provider
        self.chunk_size = int(chunk_size)
        self.chunk_overlap = int(chunk_overlap)
        # key: (session_dir, file_key) -> FaissVecDB
        self._dbs: Dict[Tuple[str, str], Any] = {}
        self._chunker: Optional[Any] = None
        if RAG_AVAILABLE:
            self._chunker = RecursiveCharacterChunker(
                chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
            )

    @property
    def available(self) -> bool:
        return bool(RAG_AVAILABLE and self._chunker and self.embedding_provider)

    def _db_dir(self, session_id: str, file_key: str) -> Path:
        return self.base_dir / safe_dir_name(session_id) / safe_dir_name(file_key)

    async def _get_db(self, session_id: str, file_key: str) -> Optional[Any]:
        key = (session_id, file_key)
        if key in self._dbs:
            return self._dbs[key]
        if not self.available:
            return None
        try:
            db_dir = self._db_dir(session_id, file_key)
            db_dir.mkdir(parents=True, exist_ok=True)
            vec_db = FaissVecDB(
                doc_store_path=str(db_dir / "doc.db"),
                index_store_path=str(db_dir / "index.faiss"),
                embedding_provider=self.embedding_provider,
                rerank_provider=self.rerank_provider,
            )
            await vec_db.initialize()
            self._dbs[key] = vec_db
            return vec_db
        except Exception as exc:
            logger.error(f"[烤箱-文件读取] 初始化向量库失败: {exc}")
            return None

    async def add_file(self, session_id: str, file_key: str, text: str) -> bool:
        """将文件文本分块并写入向量库。"""
        vec_db = await self._get_db(session_id, file_key)
        if not vec_db:
            return False
        try:
            chunks = await self._chunker.chunk(text)
            if not chunks:
                return False
            metadatas = [
                {"file_name": file_key, "chunk_index": i} for i in range(len(chunks))
            ]
            await vec_db.insert_batch(chunks, metadatas)
            logger.info(f"[烤箱-文件读取] {file_key} 已写入向量库（{len(chunks)} 块）")
            return True
        except Exception as exc:
            logger.error(f"[烤箱-文件读取] {file_key} 写入向量库失败: {exc}")
            return False

    async def retrieve(
        self,
        session_id: str,
        query: str,
        file_keys: Optional[List[str]] = None,
        top_k: int = 5,
        fetch_k: int = 20,
        rerank: bool = True,
    ) -> List[Tuple[str, str]]:
        """在会话内的文件向量库中检索，返回 (文件名, 片段) 列表。"""
        results: List[Tuple[str, str]] = []
        for db_session, file_key in list(self._dbs):
            if db_session != session_id:
                continue
            if file_keys is not None and file_key not in file_keys:
                continue
            vec_db = self._dbs.get((db_session, file_key))
            if not vec_db:
                continue
            try:
                hits = await vec_db.retrieve(
                    query, k=top_k, fetch_k=fetch_k, rerank=rerank and self.rerank_provider is not None
                )
            except Exception as exc:
                logger.error(f"[烤箱-文件读取] 检索 {file_key} 失败: {exc}")
                continue
            for hit in hits:
                data = getattr(hit, "data", None)
                if isinstance(data, dict):
                    chunk_text = data.get("text", "")
                    if chunk_text:
                        results.append((file_key, chunk_text))
        return results

    async def has_file(self, session_id: str, file_key: str) -> bool:
        if (session_id, file_key) in self._dbs:
            return True
        return self._db_dir(session_id, file_key).exists()

    async def remove_file(self, session_id: str, file_key: str) -> None:
        """移除单个文件的索引（内存实例与磁盘目录）。"""
        key = (session_id, file_key)
        vec_db = self._dbs.pop(key, None)
        if vec_db:
            try:
                await vec_db.close()
            except Exception:
                pass
        db_dir = self._db_dir(session_id, file_key)
        try:
            if db_dir.exists():
                shutil.rmtree(db_dir, ignore_errors=True)
        except Exception as exc:
            logger.error(f"[烤箱-文件读取] 删除索引目录失败: {exc}")

    async def remove_session(self, session_id: str) -> None:
        """移除整个会话的全部索引。"""
        for db_session, file_key in list(self._dbs):
            if db_session == session_id:
                await self.remove_file(session_id, file_key)
        session_dir = self.base_dir / safe_dir_name(session_id)
        try:
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
        except Exception as exc:
            logger.error(f"[烤箱-文件读取] 删除会话索引目录失败: {exc}")

    def list_file_keys(self, session_id: str) -> List[str]:
        """列出会话内已索引的文件键。"""
        keys = {fk for ds, fk in self._dbs if ds == session_id}
        session_dir = self.base_dir / safe_dir_name(session_id)
        if session_dir.exists():
            for child in session_dir.iterdir():
                if child.is_dir():
                    keys.add(child.name)
        return sorted(keys)

    async def close(self) -> None:
        for key, vec_db in list(self._dbs.items()):
            try:
                await vec_db.close()
            except Exception:
                pass
            self._dbs.pop(key, None)
