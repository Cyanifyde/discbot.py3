from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple


async def read_json(path: Path, default: Any = None) -> Any:
    def _read() -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return default

    return await asyncio.to_thread(_read)


async def write_json_atomic(path: Path, data: Any) -> None:
    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True, indent=2)
        os.replace(tmp_path, path)

    await asyncio.to_thread(_write)


async def read_text(path: Path) -> Optional[str]:
    def _read() -> Optional[str]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    return await asyncio.to_thread(_read)


async def append_text(path: Path, text: str) -> None:
    def _append() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    await asyncio.to_thread(_append)


async def get_file_size(path: Path) -> int:
    def _size() -> int:
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0

    return await asyncio.to_thread(_size)


async def read_queue_lines(path: Path, offset: int, max_lines: int = 50) -> List[Tuple[str, int]]:
    def _read() -> List[Tuple[str, int]]:
        if not path.exists():
            return []
        results: List[Tuple[str, int]] = []
        with path.open("rb") as handle:
            handle.seek(offset)
            while len(results) < max_lines:
                line = handle.readline()
                if not line:
                    break
                end_offset = handle.tell()
                try:
                    results.append((line.decode("utf-8"), end_offset))
                except UnicodeDecodeError:
                    results.append(("", end_offset))
        return results

    return await asyncio.to_thread(_read)


async def rewrite_queue_file(src: Path, offset: int) -> None:
    def _rewrite() -> None:
        if not src.exists():
            return
        tmp_path = src.with_suffix(".tmp")
        with src.open("rb") as reader, tmp_path.open("wb") as writer:
            reader.seek(offset)
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
        os.replace(tmp_path, src)

    await asyncio.to_thread(_rewrite)
