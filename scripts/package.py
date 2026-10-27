"""Build the committed plugin using Git's release exclusions."""

import ast
import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile


def validate_logger_imports(source, filename):
    for node in ast.walk(ast.parse(source, filename=filename)):
        invalid = False
        if isinstance(node, ast.Import):
            invalid = any(alias.name.split(".")[0] == "logging" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            invalid = (node.module or "").split(".")[0] == "logging" or any(
                alias.name in {"get_logger", "getLogger"} for alias in node.names
            )
            if any(alias.name == "logger" for alias in node.names):
                invalid = invalid or not (
                    node.module == "astrbot.api"
                    and not node.level
                    and len(node.names) == 1
                    and node.names[0].asname is None
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            invalid = node.name in {"get_logger", "getLogger"}
        elif isinstance(node, ast.Call):
            invalid = (
                isinstance(node.func, ast.Name)
                and node.func.id in {"get_logger", "getLogger"}
            ) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get_logger", "getLogger"}
            )
        if invalid:
            raise ValueError(
                f"{filename}:{node.lineno}: logger must use from astrbot.api import logger"
            )

root = Path(__file__).resolve().parents[1]
version = re.search(r"(?m)^version:\s*(\S+)", (root / "metadata.yaml").read_text("utf-8"))[1]
archive = root / "dist" / f"{root.name}-v{version}.zip"
archive.parent.mkdir(exist_ok=True)
subprocess.run(  # noqa: S603
    [shutil.which("git") or "git", "archive", "--format=zip", f"--output={archive}", "HEAD"],
    cwd=root,
    check=True,
)
try:
    with ZipFile(archive) as package:
        for name in package.namelist():
            if name.endswith(".py"):
                validate_logger_imports(package.read(name), name)
except (ValueError, SyntaxError):
    archive.unlink()
    raise
print(archive)
