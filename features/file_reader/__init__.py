# Modified from astrbot_plugin_file_reader_pro (MIT) by zz6zz666
# https://github.com/zz6zz666/astrbot_plugin_file_reader_pro
"""文件读取功能模块。

核心实现改写自 astrbot_plugin_file_reader_pro（MIT License，Copyright (c) 2025 xiewoc），
在其基础上调整了架构与行为，原版许可证文本见同目录 LICENSE 文件。
"""

from .manager import FileRecord, FileReaderManager
from .parsers import FileParseError, read_any_file_to_text, supported_extensions
from .rag import RAGIndex

__all__ = [
    "FileParseError",
    "FileReaderManager",
    "FileRecord",
    "RAGIndex",
    "read_any_file_to_text",
    "supported_extensions",
]
