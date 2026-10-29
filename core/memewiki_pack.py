# ruff: noqa: E501
# Adapted from astrbot_plugin_memewiki 1.7.0 by in1t1a1 (MIT).
# See memewiki.LICENSE and memewiki.SOURCE.json for attribution and source hashes.
#!/usr/bin/env python3
"""把抓好的词条打包成随插件分发的 SQLite 数据库。

用法：
    python scripts/pack_data.py --source /root/cnmeme --out data/memes.db

产物 `memes.db` 含一张表：
    memes(title PRIMARY KEY, slug, tags, usage, origin, other, summary)
以及 meta(built_at, count)。

「用法 / 出处」在这里就拆好，运行时不用再解析 HTML。
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path


def _ro_uri(path) -> str:
    """只读 file URI。

    必须走 URI 形式才能带 `?mode=ro`；而 Windows 上盘符反斜杠、空格、`#`、`?`、
    中文目录都会让手拼的 `file:C:\\...` 失效。`Path.as_uri()` 会做百分号编码，
    sqlite 自己会解码，两端都稳。
    """
    p = Path(path)
    try:
        return p.resolve().as_uri() + "?mode=ro"
    except ValueError:  # 相对路径等极端情况，退回老写法
        return f"file:{p}?mode=ro"


def _replace_with_retry(src, dst, tries: int = 20, delay: float = 0.15) -> None:
    """原子替换，并容忍 Windows 的「文件被占用」。

    POSIX 上 `os.replace` 对「目标文件正被读」是安全的；Windows 上同一操作会抛
    `PermissionError: [WinError 32] 另一个程序正在使用此文件`。插件和同步脚本会同时
    碰同一份词库/缓存，所以这里统一重试，避免 Windows 上偶发「换库失败」。
    """
    src, dst = Path(src), Path(dst)
    last: Exception | None = None
    for _ in range(tries):
        try:
            src.replace(dst)
            return
        except PermissionError as exc:  # Windows 文件占用
            last = exc
            time.sleep(delay)
    assert last is not None  # noqa: S101 - internal retry-loop invariant
    raise last


def html_to_text(raw: str) -> str:
    s = re.sub(r"<h2[^>]*>", "\n## ", raw or "", flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</p>|<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"\n##\s*", "\n" + (text or ""))
    usage, origin, other = [], [], []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        head, _, rest = part.partition("\n")
        head = head.strip()
        if any(k in head for k in ("用法", "怎么用", "如何用", "例句", "接梗")):
            usage.append(rest.strip() or head)
        elif any(k in head for k in ("来源", "出处", "背景", "起源", "介绍")):
            origin.append(rest.strip() or head)
        else:
            other.append((head + "\n" + rest).strip() if rest else head)
    return {
        "usage": "\n".join(usage).strip(),
        "origin": "\n".join(origin).strip(),
        "other": "\n".join(other).strip(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="抓取数据目录（含 items.json 与 details/）")
    ap.add_argument("--out", required=True, help="输出 db 路径")
    args = ap.parse_args()

    src = Path(args.source).expanduser()
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时库，完整校验后再原子替换；同步中断或列表截断时不破坏旧库。
    tmp_out = out.with_name(out.name + ".new")
    tmp_out.unlink(missing_ok=True)

    items_path = src / "items.json"
    detail_dir = src / "details"
    if not items_path.exists() or not detail_dir.is_dir():
        print(f"缺少数据：{items_path} 或 {detail_dir}")
        return 1

    items = json.loads(items_path.read_text(encoding="utf-8")).get("items") or []
    print(f"列表 {len(items)} 条，详情目录 {detail_dir}")

    conn = sqlite3.connect(tmp_out)
    conn.execute(
        "CREATE TABLE memes ("
        "title TEXT PRIMARY KEY, slug TEXT, tags TEXT, usage TEXT, origin TEXT, other TEXT, summary TEXT)"
    )
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    written = missing = 0
    for idx, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        slug = (it.get("slug") or "").strip()
        if not title or not slug:
            continue
        path = detail_dir / (urllib.parse.quote(slug, safe="") + ".json")
        item: dict = {}
        try:
            if path.exists():
                item = json.loads(path.read_text(encoding="utf-8")).get("item") or {}
        except OSError:
            # slug 太长（文件名超过 255 字节）等情况，按「无详情」处理
            item = {}
        except Exception:
            item = {}
        if item:
            body = html_to_text(item.get("content_html") or "") or (item.get("summary") or "")
            sec = split_sections(body)
            tags = item.get("tags") or it.get("tags") or []
            summary = item.get("summary") or it.get("summary") or ""
        else:
            # 详情还没抓到：先用列表里的标题/标签/摘要兜底，
            # 保证增量同步不必等详情全部抓完也能重建出完整词库。
            missing += 1
            sec = {"usage": "", "origin": "", "other": ""}
            tags = it.get("tags") or []
            summary = it.get("summary") or ""
        conn.execute(
            "INSERT OR REPLACE INTO memes VALUES (?,?,?,?,?,?,?)",
            (
                title,
                slug,
                "、".join(tags),
                sec["usage"],
                sec["origin"],
                sec["other"],
                summary,
            ),
        )
        written += 1
        if idx % 1000 == 0:
            print(f"  已处理 {idx}/{len(items)}")

    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),)
    )
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('count', ?)", (str(written),))
    conn.commit()
    conn.execute("VACUUM")
    check = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
    if not check or check[0] != "ok":
        tmp_out.unlink(missing_ok=True)
        print(f"新词库完整性检查失败：{check}")
        return 1

    old_count = 0
    if out.exists():
        try:
            old = sqlite3.connect(_ro_uri(out), uri=True)
            try:
                row = old.execute("SELECT COUNT(*) FROM memes").fetchone()
                old_count = int(row[0]) if row else 0
            finally:
                old.close()
        except Exception:
            old_count = 0
    # 正常的增量同步不应令词库骤减；疑似分页/API 截断时保留旧库。
    if old_count and written < old_count:
        tmp_out.unlink(missing_ok=True)
        print(f"拒绝替换：新库仅 {written} 条，少于旧库 {old_count} 条")
        return 2

    _replace_with_retry(tmp_out, out)
    size_mb = out.stat().st_size / 1048576
    print(
        f"写入 {written} 条（其中 {missing} 条无详情，已用列表摘要兜底），db {size_mb:.1f} MB → {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
