"""Bot package - Discord client and guild state management.

Keep imports lazy so test discovery (python -m unittest) doesn't import discord-heavy
modules unless explicitly needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import DiscBot as DiscBot
    from .guild_state import GuildState as GuildState

__all__ = ["GuildState", "DiscBot"]


def __getattr__(name: str):
    if name == "DiscBot":
        from .client import DiscBot

        return DiscBot
    if name == "GuildState":
        from .guild_state import GuildState

        return GuildState
    raise AttributeError(name)
