"""
Auto-responder system - split into focused modules.

This package handles automatic responses to message triggers.
"""
from .engine import AutoResponderEngine, handle_auto_responder
from .matching import match_trigger, passes_filters
from .config_loader import load_guild_config, TriggerSpec

__all__ = [
    "AutoResponderEngine",
    "handle_auto_responder",
    "match_trigger",
    "passes_filters",
    "load_guild_config",
    "TriggerSpec",
]
