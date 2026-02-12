"""
Stable bootstrap entrypoint.

This file should remain minimal and delegate runtime behavior to `main_runtime.py`.
"""
from __future__ import annotations

import asyncio
import os

# Set thread limits BEFORE any heavy imports.
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["NUMBA_NUM_THREADS"] = "1"

from main_runtime import main as runtime_main


if __name__ == "__main__":
    try:
        asyncio.run(runtime_main())
    except KeyboardInterrupt:
        pass
