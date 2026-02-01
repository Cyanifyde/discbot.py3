"""
Core interaction handling - routes interactions to appropriate handlers.

This module provides a central place to register and dispatch interaction handlers
(buttons, select menus, modals, etc.) so they can be used across multiple modules.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

import discord

logger = logging.getLogger("discbot.interactions")

# Type alias for interaction handlers
InteractionHandler = Callable[[discord.Interaction], Coroutine[Any, Any, bool]]

# Registry of component handlers (buttons, select menus, etc.)
# Key is a prefix that the custom_id must start with
_COMPONENT_HANDLERS: Dict[str, InteractionHandler] = {}


def register_component_handler(prefix: str, handler: InteractionHandler) -> None:
    """
    Register a handler for component interactions (buttons, selects, etc.).
    
    Args:
        prefix: The custom_id prefix this handler responds to
        handler: Async function that takes an Interaction and returns True if handled
    """
    _COMPONENT_HANDLERS[prefix] = handler
    logger.debug("Registered component handler for prefix: %s", prefix)


def unregister_component_handler(prefix: str) -> None:
    """Unregister a component handler."""
    _COMPONENT_HANDLERS.pop(prefix, None)


async def handle_interaction(interaction: discord.Interaction) -> bool:
    """
    Route an interaction to the appropriate handler.
    
    Returns True if the interaction was handled, False otherwise.
    """
    # Handle component interactions (buttons, select menus)
    if interaction.type == discord.InteractionType.component:
        return await _handle_component(interaction)
    
    # Future: handle modal submissions, autocomplete, etc.
    
    return False


async def _handle_component(interaction: discord.Interaction) -> bool:
    """Handle a component interaction by finding the right handler."""
    if not interaction.data:
        return False
    
    custom_id = interaction.data.get("custom_id", "")
    if not isinstance(custom_id, str):
        return False
    
    # Find handler by prefix match
    for prefix, handler in _COMPONENT_HANDLERS.items():
        if custom_id.startswith(prefix):
            try:
                return await handler(interaction)
            except Exception as e:
                logger.error("Error in component handler for %s: %s", prefix, e)
                # Try to respond with error if not already responded
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "An error occurred. Please try again later.",
                            ephemeral=True,
                        )
                except Exception:
                    pass
                return True  # Mark as handled even on error
    
    return False
