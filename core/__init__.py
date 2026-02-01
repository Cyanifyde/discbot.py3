"""
Core utilities and infrastructure for the Discord bot.

This package contains:
- config: Configuration loading and validation
- constants: Configuration keys and enums
- hashes: Hash loading utilities
- io_utils: File I/O helpers
- modules_config: Module enable/disable config
- paths: Path resolution
- queueing: Job queue system
- storage: Persistent storage for user records
- types: Dataclasses and type definitions
- utils: General utilities
"""
from .constants import ConfigKey, K, JobSource, MatchMode
from .types import (
    AttachmentInfo,
    LinkedMessage,
    ScanJob,
    EnforcementResult,
)

__all__ = [
    # Constants
    "ConfigKey",
    "K",
    "JobSource",
    "MatchMode",
    # Types
    "AttachmentInfo",
    "LinkedMessage",
    "ScanJob",
    "EnforcementResult",
]
