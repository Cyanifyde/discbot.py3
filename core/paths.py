"""
Path resolution utilities.

Provides base directory and path resolution for the project.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

BASE_DIR = Path(__file__).resolve().parent.parent


def resolve_repo_path(path: Union[str, Path]) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or candidate.drive:
        return candidate
    return BASE_DIR / candidate
