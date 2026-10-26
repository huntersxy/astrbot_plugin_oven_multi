"""Build the committed plugin using Git's release exclusions."""

import re
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = re.search(r"(?m)^version:\s*(\S+)", (root / "metadata.yaml").read_text("utf-8"))[1]
archive = root / "dist" / f"{root.name}-v{version}.zip"
archive.parent.mkdir(exist_ok=True)
subprocess.run(  # noqa: S603
    [shutil.which("git") or "git", "archive", "--format=zip", f"--output={archive}", "HEAD"],
    cwd=root,
    check=True,
)
print(archive)
