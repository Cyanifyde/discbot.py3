"""
Enforcement service - handles role removal and unverified role assignment.

This service contains the business logic for enforcement actions,
separated from Discord API calls for easier testing.
"""
from __future__ import annotations

import logging
from typing import Any

import discord

from core.constants import K
from core.types import EnforcementResult

logger = logging.getLogger("discbot.enforcement")


class EnforcementService:
    """
    Handles enforcement actions like role removal and unverified role assignment.
    
    Consolidates the duplicated enforcement logic from bot.py into a single place.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._unverified_role_id = config.get(K.UNVERIFIED_ROLE_ID)

    def update_config(self, config: dict[str, Any]) -> None:
        """Update configuration."""
        self.config = config
        self._unverified_role_id = config.get(K.UNVERIFIED_ROLE_ID)

    def get_removable_roles(
        self,
        member: discord.Member,
        bot_top_role: discord.Role | None,
    ) -> list[discord.Role]:
        """
        Get list of roles that can be removed from a member.
        
        Excludes:
        - Default @everyone role
        - Managed roles (bot roles, integrations)
        - Roles at or above bot's highest role
        """
        removable = []
        for role in member.roles:
            if role.is_default():
                continue
            if role.managed:
                continue
            if bot_top_role and role >= bot_top_role:
                continue
            removable.append(role)
        return removable

    async def remove_roles(
        self,
        member: discord.Member,
        bot_top_role: discord.Role | None,
        reason: str,
    ) -> int:
        """
        Remove all removable roles from a member.
        
        Returns the number of roles removed.
        """
        removable = self.get_removable_roles(member, bot_top_role)
        if not removable:
            return 0
        
        try:
            await member.remove_roles(*removable, reason=reason, atomic=True)
            return len(removable)
        except discord.HTTPException as e:
            logger.warning("Failed to remove roles from %s: %s", member.id, e)
            return 0

    async def add_unverified_role(
        self,
        member: discord.Member,
        bot_top_role: discord.Role | None,
        reason: str,
    ) -> bool:
        """
        Add the unverified role to a member.
        
        Returns True if successful.
        """
        if not self._unverified_role_id:
            return False
        
        guild = member.guild
        role = guild.get_role(int(self._unverified_role_id))
        
        if not role:
            logger.warning("Unverified role %s not found in guild %s", self._unverified_role_id, guild.id)
            return False
        
        if bot_top_role and role >= bot_top_role:
            logger.warning("Cannot assign unverified role %s - at or above bot's role", role.id)
            return False
        
        try:
            await member.add_roles(role, reason=reason, atomic=True)
            return True
        except discord.HTTPException as e:
            logger.warning("Failed to add unverified role to %s: %s", member.id, e)
            return False

    async def enforce_member(
        self,
        member: discord.Member,
        bot_top_role: discord.Role | None,
        reason: str,
        delete_message: discord.Message | None = None,
    ) -> EnforcementResult:
        """
        Full enforcement action: remove roles, add unverified, optionally delete message.
        
        This is the single source of truth for enforcement logic.
        """
        result = EnforcementResult()
        
        # Remove roles
        result.roles_removed = await self.remove_roles(member, bot_top_role, reason)
        
        # Add unverified role
        result.unverified_added = await self.add_unverified_role(member, bot_top_role, reason)
        
        # Delete message if provided
        if delete_message:
            try:
                await delete_message.delete()
                result.message_deleted = True
            except discord.HTTPException as e:
                logger.warning("Failed to delete message %s: %s", delete_message.id, e)
        
        return result

    def format_action_log(
        self,
        member: discord.Member,
        result: EnforcementResult,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Format an action log message."""
        parts = [
            f"user_id={member.id}",
            f"action={action}",
            f"roles_removed_count={result.roles_removed}",
            f"unverified_added={'yes' if result.unverified_added else 'no'}",
        ]
        
        if extra:
            for key, value in extra.items():
                parts.append(f"{key}={value}")
        
        return " ".join(parts)
