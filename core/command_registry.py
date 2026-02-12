"""
Central command registry - routes text commands to module handlers.

Replaces the hardcoded if/elif dispatch in client.py with a
registration-based approach. Each module registers its command roots
(trigger words) and handler function. The registry dispatches incoming
messages to the right handler based on the first token.

This does NOT replace:
- help_system.py (modules still register help separately in setup_*)
- permissions.py (each handler still checks its own permissions)

It only centralizes command ROUTING.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable, Optional

if TYPE_CHECKING:
    import discord
    from bot.client import DiscBot

logger = logging.getLogger("discbot.registry")


@dataclass
class CommandRoute:
    """A single command route mapping roots to a handler."""

    name: str  # Identifier for logging (e.g. "moderation")
    roots: list[str]  # Trigger words (e.g. ["moderation", "warn", "kick"])
    handler: Callable  # async (message, bot) -> bool  OR  async (message) -> bool
    needs_bot: bool = True  # True = handler(message, bot); False = handler(message)
    is_fallback: bool = False  # True = only try if no named route matched
    priority: int = 0  # Lower = tried first when multiple routes share a root


class CommandRegistry:
    """
    Central command router.

    - ``register()`` adds a route.
    - ``dispatch()`` looks up the first token and calls the matching handler(s).
    """

    def __init__(self) -> None:
        # root_word -> list of CommandRoute, ordered by priority then insertion
        self._root_map: dict[str, list[CommandRoute]] = {}
        self._fallbacks: list[CommandRoute] = []
        self._routes: dict[str, CommandRoute] = {}  # name -> route

    # ── Registration ──────────────────────────────────────────────────

    def register(self, route: CommandRoute) -> None:
        """Register a command route."""
        self._routes[route.name] = route

        if route.is_fallback:
            self._fallbacks.append(route)
            self._fallbacks.sort(key=lambda r: r.priority)
            return

        for root in route.roots:
            root_lower = root.lower()
            bucket = self._root_map.setdefault(root_lower, [])
            bucket.append(route)
            bucket.sort(key=lambda r: r.priority)

    # ── Dispatch ──────────────────────────────────────────────────────

    async def dispatch(
        self,
        message: "discord.Message",
        bot: "DiscBot",
    ) -> bool:
        """
        Route *message* to the first matching handler.

        Returns True if a handler claimed the message, False otherwise.
        """
        content = (message.content or "").strip()
        if not content:
            return False

        cmd0 = content.split()[0].lower()

        # Try named routes that match this root
        routes = self._root_map.get(cmd0)
        if routes:
            for route in routes:
                handled = await self._try_handler(route, message, bot)
                if handled:
                    return True

        # Try fallback routes
        for route in self._fallbacks:
            handled = await self._try_handler(route, message, bot)
            if handled:
                return True

        return False

    # ── Internal ──────────────────────────────────────────────────────

    async def _try_handler(
        self,
        route: CommandRoute,
        message: "discord.Message",
        bot: "DiscBot",
    ) -> bool:
        """Call a single handler with error wrapping. Returns True if handled."""
        from core.error_handler import handle_command_error

        try:
            if route.needs_bot:
                result = await route.handler(message, bot)
            else:
                result = await route.handler(message)
            return bool(result)
        except Exception as e:
            await handle_command_error(e, message)
            return True  # Swallow: error was reported to user


# Module-level singleton so modules can import and register at setup time.
command_registry = CommandRegistry()
