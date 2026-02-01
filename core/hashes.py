from __future__ import annotations

from typing import Any, Dict

from .io_utils import read_text
from .paths import resolve_repo_path
from .utils import is_safe_relative_path, is_sha256_hex


async def load_hashes(config: Dict[str, Any]) -> set[str]:
    hashes: set[str] = set()
    files = config.get("hashes_files", [])
    for path_str in files:
        if not isinstance(path_str, str) or not is_safe_relative_path(path_str):
            continue
        path = resolve_repo_path(path_str)
        content = await read_text(path)
        if not content:
            continue
        for line in content.splitlines():
            line = line.strip().lower()
            if is_sha256_hex(line):
                hashes.add(line)
    for item in config.get("extra_hashes", []):
        if isinstance(item, str) and is_sha256_hex(item):
            hashes.add(item.lower())
    return hashes
