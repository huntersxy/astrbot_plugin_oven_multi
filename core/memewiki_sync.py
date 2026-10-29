# ruff: noqa: E501
# Adapted from astrbot_plugin_memewiki 1.7.0 by in1t1a1 (MIT).
# See memewiki.LICENSE and memewiki.SOURCE.json for attribution and source hashes.
"""把 cnmeme.wiki 公开词库同步到本地（增量）。

默认行为（增量）：
  1. 列表按 updated_at 倒序翻页，一旦整页都是「已见过且 updated_at 未变」的词条就停止；
     没翻到的老词条沿用上次 items.json，保证列表仍然完整。
  2. 详情只为「新增」和「updated_at 变更」的词条抓取，已缓存的直接复用。
  3. 已有但缺详情的词条（首轮冷启动/上次没抓完）继续排队补，可用 --limit 限制单次条数。

--full 时退回全量：完整翻完列表、删除列表里已消失的词条、不限制详情数量。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://cnmeme.wiki/api/v1/public/memes"
UA = "AstrBot-memewiki/1.0 (+https://github.com/in1t1a1/astrbot_plugin_memewiki)"
PAGE_LIMIT = 30
DEFAULT_SLEEP = 0.35
MAX_RETRY = 3
MAX_NAME_BYTES = 240  # ext4/xfs 单个文件名上限 255 字节，留点余量


def _replace_with_retry(src, dst, tries: int = 20, delay: float = 0.15) -> None:
    """原子替换，并容忍 Windows 的「文件被占用」。

    POSIX 上 `os.replace` 对「目标文件正被读」是安全的；Windows 上同一操作会抛
    `PermissionError: [WinError 32] 另一个程序正在使用此文件`。插件与同步脚本会同时
    碰同一份词库/缓存，所以统一重试，避免 Windows 上偶发「换库失败」。
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


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def api_get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})  # noqa: S310 - module-owned HTTPS API URL
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - module-owned HTTPS API URL
        return json.loads(resp.read().decode("utf-8"))


def api_get_retry(url: str, what: str) -> dict | None:
    for attempt in range(MAX_RETRY):
        try:
            return api_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("Retry-After") or 5)
                log(f"  429 限流，等 {wait}s（{what}）")
                time.sleep(wait)
                continue
            log(f"  HTTP {exc.code}（{what}）")
            return None
        except Exception as exc:  # noqa: BLE001
            log(f"  请求失败：{type(exc).__name__} {exc}（{what}）")
            time.sleep(1 + attempt)
    return None


def load_prev_items(data_dir: Path) -> list[dict]:
    """上次的列表快照；坏了就当没有（退回全量）。"""
    path = data_dir / "items.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") or []
        return [it for it in items if isinstance(it, dict) and it.get("slug")]
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        log(f"旧列表不可用（{type(exc).__name__}），本次走全量")
        return []


def _updated_at(item: dict) -> str:
    return str(item.get("updated_at") or "")


def fetch_list(
    sleep: float, prev: list[dict], incremental: bool, max_pages: int = 0
) -> tuple[list[dict], dict]:
    """翻列表页。incremental=True 时遇到「整页都没更新」就提前停。

    返回 (fetched_items, stats)；stats 里 stopped_early 表示是否提前停止。
    """
    seen = {str(it.get("slug")): _updated_at(it) for it in prev}
    fetched: list[dict] = []
    new_count = changed = unchanged = 0
    offset = 0
    pages = 0
    clean_pages = 0
    stopped_early = False

    while True:
        data = api_get_retry(f"{API_BASE}?limit={PAGE_LIMIT}&offset={offset}", f"list@{offset}")
        if not data:
            log(f"列表在 offset={offset} 中断，本次结果作废")
            return [], {}
        batch = data.get("items") or []
        if not batch:
            if data.get("has_more"):
                log(f"列表在 offset={offset} 提前返回空页，本次结果作废")
                return [], {}
            break
        pages += 1
        offset += len(batch)

        page_unchanged = 0
        for it in batch:
            fetched.append(it)
            slug = str(it.get("slug"))
            old = seen.get(slug)
            if old is None:
                new_count += 1
            elif old != _updated_at(it):
                changed += 1
            else:
                unchanged += 1
                page_unchanged += 1

        if pages % 20 == 0:
            log(f"列表进度 {len(fetched)} 条")

        full_page = len(batch) >= PAGE_LIMIT
        clean_pages = clean_pages + 1 if (full_page and page_unchanged == len(batch)) else 0
        if incremental and clean_pages >= 1:
            stopped_early = True
            break
        if max_pages and pages >= max_pages:
            log(f"到达本次翻页上限 {max_pages} 页，剩余列表沿用旧缓存")
            stopped_early = True
            break
        if not data.get("has_more"):
            break
        time.sleep(sleep)

    stats = {
        "pages": pages,
        "stopped_early": stopped_early,
        "new": new_count,
        "changed": changed,
        "unchanged": unchanged,
        "fetched": len(fetched),
    }
    log(
        f"列表翻页 {pages} 页：{len(fetched)} 条（新增 {new_count}、变更 {changed}、未变 {unchanged}）"
        + ("，已提前停止" if stopped_early else "，已翻到底")
    )
    return fetched, stats


def merge_prev(fetched: list[dict], prev: list[dict]) -> list[dict]:
    """把「没翻到」的老词条接在后面，保持 updated_at 倒序、slug 去重。"""
    seen = {str(it.get("slug")) for it in fetched}
    merged = list(fetched)
    merged.extend(it for it in prev if str(it.get("slug")) not in seen)
    merged.sort(key=_updated_at, reverse=True)
    return merged


def write_index(data_dir: Path, items: list[dict]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for it in items:
        title = (it.get("title") or "").strip()
        slug = it.get("slug") or ""
        if title and slug and title not in titles:
            titles[title] = slug

    detail_dir = data_dir / "details"
    cached = len(list(detail_dir.glob("*.json"))) if detail_dir.is_dir() else 0
    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "built_at": built_at,
        "count": len(titles),
        "details_cached": cached,
        "source": API_BASE,
        "titles": titles,
    }
    out = data_dir / "index.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _replace_with_retry(tmp, out)

    # pack_data.py 读 items.json（列表快照），必须一起落盘，否则重建 SQLite 会失败
    items_out = data_dir / "items.json"
    items_tmp = items_out.with_suffix(".tmp")
    items_tmp.write_text(
        json.dumps({"built_at": built_at, "count": len(items), "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    _replace_with_retry(items_tmp, items_out)

    log(
        f"索引已写入 {out}（{len(titles)} 条，详情缓存 {cached} 条，{out.stat().st_size // 1024} KB）"
        f" + items.json（{items_out.stat().st_size // 1024} KB）"
    )
    return titles


def _detail_name(slug: str) -> str | None:
    """slug → 详情文件名；整句当 slug 的极端词条会超过文件名上限，直接跳过缓存。"""
    name = urllib.parse.quote(slug, safe="") + ".json"
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        return None
    return name


def fetch_details(
    data_dir: Path,
    titles: dict[str, str],
    sleep: float,
    limit: int = 0,
    refresh: set[str] | None = None,
) -> int:
    """抓详情。只抓「缺缓存」和「updated_at 变更」的词条；refresh 里的旧缓存会先删掉。

    返回待抓总数（含本次因 limit 没抓的），便于日志里说明还差多少。
    """
    detail_dir = data_dir / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    refresh = refresh or set()

    pending: list[tuple[str, str]] = []
    too_long = 0
    for title, slug in titles.items():
        name = _detail_name(slug)
        if name is None:
            too_long += 1
            continue
        path = detail_dir / name
        if slug in refresh and path.exists():
            with contextlib.suppress(OSError):
                path.unlink()
        if not path.exists():
            pending.append((title, slug))

    total = len(pending)
    if too_long:
        log(f"跳过 {too_long} 条 slug 过长（文件名超限）的词条")
    if not total:
        log("详情已是最新，无需抓取")
        return 0
    todo = pending[:limit] if limit else pending
    log(
        f"待抓详情 {total} 条"
        + (f"，本次抓 {len(todo)} 条（上限 {limit}）" if limit and len(todo) < total else "")
    )

    ok = fail = 0
    for idx, (_title, slug) in enumerate(todo, 1):
        url = API_BASE + "/" + urllib.parse.quote(slug, safe="")
        data = api_get_retry(url, slug[:40])
        if not data or not data.get("item"):
            fail += 1
        else:
            name = _detail_name(slug)
            try:
                if name is None:
                    raise OSError("文件名超限")
                (detail_dir / name).write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
                ok += 1
            except OSError as exc:
                fail += 1
                log(f"  写盘失败（{type(exc).__name__}）：{slug[:30]}")
        if idx % 100 == 0 or idx == len(todo):
            log(f"详情进度 {idx}/{len(todo)}  成功 {ok} 失败 {fail}")
        time.sleep(sleep)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 cnmeme.wiki 词库到本地（默认增量）")
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"))
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    parser.add_argument("--list-only", action="store_true", help="只更新索引，不抓详情")
    parser.add_argument("--limit", type=int, default=0, help="本次最多抓多少条详情（0 = 全部）")
    parser.add_argument(
        "--reuse-items", action="store_true", help="复用 data-dir 里已有的 items.json，不重新拉列表"
    )
    parser.add_argument(
        "--full", action="store_true", help="全量：完整翻列表、清理已下架词条、不限详情条数"
    )
    parser.add_argument(
        "--max-pages", type=int, default=0, help="增量模式下最多翻多少页（0 = 不限制）"
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    log(f"数据目录：{data_dir}")

    prev = load_prev_items(data_dir)
    incremental = (not args.full) and bool(prev) and (not args.reuse_items)
    if args.full:
        log(f"全量模式：完整翻列表，已有列表 {len(prev)} 条")
    elif prev:
        log(f"增量模式：已有列表 {len(prev)} 条，遇到未变的整页即停止")
    else:
        log("没有旧列表，本次按全量处理")

    if args.reuse_items and prev:
        items = prev
        log(f"复用已有列表：{len(items)} 条")
        stats: dict = {}
    else:
        items, stats = fetch_list(args.sleep, prev, incremental, args.max_pages)
        if not items:
            log("没取到完整列表，保留现有数据并退出")
            return 1
        if incremental and prev:
            items = merge_prev(items, prev)
            log(f"合并旧列表后共 {len(items)} 条")
        if prev and len(items) < len(prev):
            log(f"新列表仅 {len(items)} 条，少于已有 {len(prev)} 条；疑似 API 截断，拒绝覆盖")
            return 2

    titles = write_index(data_dir, items)
    if args.list_only:
        return 0

    # updated_at 变过的词条要重新抓详情
    prev_map = {str(it.get("slug")): _updated_at(it) for it in prev}
    refresh = {
        str(it.get("slug"))
        for it in items
        if str(it.get("slug")) in prev_map and prev_map[str(it.get("slug"))] != _updated_at(it)
    }
    if refresh:
        log(f"需刷新详情 {len(refresh)} 条（updated_at 已变更）")
    remaining = fetch_details(data_dir, titles, args.sleep, args.limit, refresh)
    if remaining and args.limit and remaining > args.limit:
        log(f"还剩 {remaining - args.limit} 条详情未抓，下次继续（增量队列）")
    write_index(data_dir, items)

    # 重建 SQLite（插件优先读它）
    pack = Path(__file__).with_name("memewiki_pack.py")
    if pack.exists():
        log("重建 SQLite 词库…")
        rc = subprocess.run(  # noqa: S603 - bundled packer and current Python only
            [
                sys.executable,
                str(pack),
                "--source",
                str(data_dir),
                "--out",
                str(data_dir / "memes.db"),
            ],
        ).returncode
        if rc != 0:
            log(f"错误：pack_data 退出码 {rc}，旧 memes.db 已保留")
            return rc

    log("同步完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
