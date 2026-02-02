"""
Sync service - handles propagation of moderation actions across linked servers.

Downstream (parent → children): Automatic, respects sync settings
Upstream (child → parent): Requires approval via buttons
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional, Set

import discord

from core.constants import K
from core.config import ConfigError, load_guild_config
from core.link_storage import get_link_storage
from core.sync_protection import get_sync_protection
from core.utils import utcnow, dt_to_iso

logger = logging.getLogger("discbot.sync")

# Action types
ActionType = Literal["ban", "unban", "kick", "mute", "unmute", "warning"]


@dataclass
class SyncAction:
    """Represents a moderation action to sync."""
    action_type: ActionType
    user_id: int
    reason: str
    mod_id: int
    origin_guild_id: int
    origin_guild_name: str
    timestamp: str
    duration: Optional[int] = None  # For mutes, in seconds


class SyncService:
    """Service for syncing moderation actions across linked servers."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._propagation_lock = asyncio.Lock()

    def _sync_setting_key(self, action_type: ActionType) -> str:
        if action_type in ("unban", "unmute"):
            base_action = action_type.replace("un", "", 1)
            return f"sync_{base_action}s"
        return f"sync_{action_type}s"

    def _action_to_dict(
        self,
        action: SyncAction,
        visited: Optional[Set[int]] = None,
    ) -> Dict[str, Any]:
        data = {
            "action_type": action.action_type,
            "user_id": action.user_id,
            "reason": action.reason,
            "mod_id": action.mod_id,
            "origin_guild_id": action.origin_guild_id,
            "origin_guild_name": action.origin_guild_name,
            "timestamp": action.timestamp,
            "duration": action.duration,
        }
        if visited is not None:
            data["visited"] = list(visited)
        return data

    def _action_from_dict(self, data: Dict[str, Any]) -> SyncAction:
        return SyncAction(
            action_type=data.get("action_type"),
            user_id=int(data.get("user_id", 0)),
            reason=data.get("reason", ""),
            mod_id=int(data.get("mod_id", 0)),
            origin_guild_id=int(data.get("origin_guild_id", 0)),
            origin_guild_name=data.get("origin_guild_name", ""),
            timestamp=data.get("timestamp", ""),
            duration=data.get("duration"),
        )

    def _format_burst_reason(
        self,
        count: int,
        max_actions: int,
        window_seconds: int,
    ) -> str:
        if window_seconds % 60 == 0:
            window_label = f"{window_seconds // 60}m"
        else:
            window_label = f"{window_seconds}s"
        return f"{count} actions in {window_label} (limit {max_actions})"

    async def _get_upstream_context(
        self,
        child_guild_id: int,
        parent_guild_id: int,
    ) -> Optional[tuple[Dict[str, Any], discord.Guild, discord.TextChannel]]:
        storage = await get_link_storage()

        # Get child link from parent's perspective
        child_link = await storage.get_child(parent_guild_id, child_guild_id)
        if not child_link:
            return None

        if not child_link.get("accept_upstream", False):
            return None
        if child_link.get("trust_level") != "trusted":
            return None

        approval_channel_id = child_link.get("approval_channel_id")
        if not approval_channel_id:
            return None

        parent_guild = self.bot.get_guild(parent_guild_id)
        if not parent_guild:
            return None

        channel = parent_guild.get_channel(int(approval_channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            return None

        return child_link, parent_guild, channel

    async def _get_protection_channel(
        self,
        guild: discord.Guild,
    ) -> Optional[discord.TextChannel]:
        channel_id = None

        state = getattr(self.bot, "guild_states", {}).get(guild.id)
        if state is not None and hasattr(state, "config"):
            channel_id = state.config.get(K.ACTION_LOG_CHANNEL_ID)

        if not channel_id:
            try:
                config = await load_guild_config(guild.id)
                channel_id = config.get(K.ACTION_LOG_CHANNEL_ID)
            except ConfigError:
                channel_id = None

        if channel_id:
            channel = guild.get_channel(int(channel_id))
            if channel and isinstance(channel, discord.TextChannel):
                member = guild.me or (self.bot.user and guild.get_member(self.bot.user.id))
                if member and channel.permissions_for(member).send_messages:
                    return channel

        member = guild.me or (self.bot.user and guild.get_member(self.bot.user.id))
        for channel in guild.text_channels:
            if member is None or channel.permissions_for(member).send_messages:
                return channel

        return None

    async def _send_protection_request(
        self,
        from_guild: discord.Guild,
        to_guild: discord.Guild,
        channel: discord.TextChannel,
        action: SyncAction,
        count: int,
        max_actions: int,
        window_seconds: int,
        direction_label: str,
    ) -> Optional[int]:
        burst_reason = self._format_burst_reason(count, max_actions, window_seconds)
        reason_text = action.reason or "No reason provided"
        if len(reason_text) > 900:
            reason_text = reason_text[:900] + "..."

        embed = discord.Embed(
            title="Sync Protection Triggered",
            description=(
                f"Syncing **{direction_label}** actions from **{from_guild.name}** "
                "is paused due to a burst of moderation activity."
            ),
            color=0xE67E22,
        )

        embed.add_field(name="Burst", value=burst_reason, inline=True)
        embed.add_field(name="Action Type", value=action.action_type.upper(), inline=True)
        embed.add_field(name="Origin Server", value=action.origin_guild_name, inline=True)
        embed.add_field(name="Reason", value=reason_text, inline=False)
        embed.add_field(
            name="Approval",
            value=(
                "Approve to resume syncing (apply queued actions).\n"
                "Decline to block until unlinked/reset."
            ),
            inline=False,
        )
        embed.set_footer(text=f"From Guild ID: {from_guild.id}")

        try:
            view = _build_decision_view(SYNC_PROTECTION_BUTTON_PREFIX)
            msg = await channel.send(embed=embed, view=view)
            return msg.id
        except discord.HTTPException as exc:
            logger.error("Failed to send protection request in guild %s: %s", to_guild.id, exc)
            return None

    async def propagate_downstream(
        self,
        origin_guild_id: int,
        action: SyncAction,
        visited: Optional[Set[int]] = None,
    ) -> List[int]:
        """
        Propagate an action downstream to all children.

        Args:
            origin_guild_id: The guild where the action originated
            action: The action to propagate
            visited: Set of already-visited guild IDs (to prevent cycles)

        Returns:
            List of guild IDs that received the action
        """
        if visited is None:
            visited = set()

        # Prevent cycles
        if origin_guild_id in visited:
            return []

        visited.add(origin_guild_id)

        storage = await get_link_storage()
        children = await storage.get_children(origin_guild_id)

        if not children:
            return []

        protection = await get_sync_protection()
        propagated_to: List[int] = []

        for child in children:
            child_guild_id = int(child.get("guild_id", 0))
            if child_guild_id <= 0:
                continue

            if child_guild_id in visited:
                continue

            # Check if this action type should be synced
            sync_key = self._sync_setting_key(action.action_type)
            if not child.get(sync_key, True):
                logger.debug(
                    "Skipping sync to %s: %s disabled",
                    child_guild_id, sync_key
                )
                continue

            # Check sync protection for this link
            allowed, reason = await protection.is_sync_allowed(origin_guild_id, child_guild_id)
            if not allowed:
                cb = await protection.get_circuit_state(origin_guild_id, child_guild_id)
                if cb.state == "pending_approval":
                    await protection.queue_action(
                        origin_guild_id,
                        child_guild_id,
                        self._action_to_dict(action, visited=visited),
                    )
                if reason:
                    logger.info("Sync blocked to %s: %s", child_guild_id, reason)
                continue

            # Get the guild
            guild = self.bot.get_guild(child_guild_id)
            if not guild:
                logger.warning("Cannot sync to guild %s: not accessible", child_guild_id)
                continue

            # Execute the action
            success = await self._execute_action(guild, action)
            if success:
                propagated_to.append(child_guild_id)
                logger.info(
                    "Synced %s to guild %s (user %s)",
                    action.action_type, child_guild_id, action.user_id
                )

            # Recursively propagate to children of this child
            nested = await self.propagate_downstream(child_guild_id, action, visited)
            propagated_to.extend(nested)

        return propagated_to

    async def _execute_action(
        self,
        guild: discord.Guild,
        action: SyncAction,
    ) -> bool:
        """
        Execute a moderation action in a guild.

        Returns True if successful.
        """
        try:
            reason = f"[Synced from {action.origin_guild_name}] {action.reason}"

            if action.action_type == "ban":
                await guild.ban(
                    discord.Object(id=action.user_id),
                    reason=reason,
                    delete_message_days=0,
                )

            elif action.action_type == "unban":
                await guild.unban(
                    discord.Object(id=action.user_id),
                    reason=reason,
                )

            elif action.action_type == "kick":
                member = guild.get_member(action.user_id)
                if member:
                    await member.kick(reason=reason)
                else:
                    logger.debug("Cannot kick user %s: not in guild %s", action.user_id, guild.id)
                    return False

            elif action.action_type == "mute":
                member = guild.get_member(action.user_id)
                if member:
                    duration = timedelta(seconds=action.duration) if action.duration else timedelta(hours=1)
                    await member.timeout(duration, reason=reason)
                else:
                    logger.debug("Cannot mute user %s: not in guild %s", action.user_id, guild.id)
                    return False

            elif action.action_type == "unmute":
                member = guild.get_member(action.user_id)
                if member:
                    await member.timeout(None, reason=reason)
                else:
                    logger.debug("Cannot unmute user %s: not in guild %s", action.user_id, guild.id)
                    return False

            elif action.action_type == "warning":
                # Warnings are stored locally, just log
                logger.info(
                    "Warning synced for user %s in guild %s: %s",
                    action.user_id, guild.id, action.reason
                )
                # Could store in moderation_storage if desired

            return True

        except discord.Forbidden:
            logger.warning(
                "Cannot execute %s in guild %s: missing permissions",
                action.action_type, guild.id
            )
            return False
        except discord.NotFound:
            logger.debug(
                "Cannot execute %s in guild %s: user not found",
                action.action_type, guild.id
            )
            return False
        except discord.HTTPException as e:
            logger.error(
                "Failed to execute %s in guild %s: %s",
                action.action_type, guild.id, e
            )
            return False

    async def request_upstream_approval(
        self,
        child_guild_id: int,
        parent_guild_id: int,
        action: SyncAction,
    ) -> Optional[int]:
        """
        Request approval from a parent for a child's action.

        Returns the message ID of the approval request, or None if failed.
        """
        context = await self._get_upstream_context(child_guild_id, parent_guild_id)
        if not context:
            logger.debug("Upstream not available for child %s -> parent %s", child_guild_id, parent_guild_id)
            return None

        _child_link, parent_guild, channel = context

        # Check sync protection for this link
        protection = await get_sync_protection()
        allowed, reason = await protection.is_sync_allowed(child_guild_id, parent_guild_id)
        if not allowed:
            cb = await protection.get_circuit_state(child_guild_id, parent_guild_id)
            if cb.state == "pending_approval":
                await protection.queue_action(
                    child_guild_id,
                    parent_guild_id,
                    self._action_to_dict(action),
                )
            if reason:
                logger.info("Upstream sync blocked to %s: %s", parent_guild_id, reason)
            return None

        # Build approval embed
        embed = discord.Embed(
            title="Upstream Action Request",
            description=f"A child server wants to sync a **{action.action_type}**.",
            color=0xFF9900,
        )

        embed.add_field(
            name="From Server",
            value=action.origin_guild_name,
            inline=True,
        )

        # Get user info
        try:
            user = await self.bot.fetch_user(action.user_id)
            user_display = f"{user.name} ({user.id})"
        except discord.NotFound:
            user_display = str(action.user_id)

        embed.add_field(
            name="Target User",
            value=user_display,
            inline=True,
        )

        embed.add_field(
            name="Action",
            value=action.action_type.upper(),
            inline=True,
        )

        embed.add_field(
            name="Reason",
            value=action.reason or "No reason provided",
            inline=False,
        )

        embed.add_field(
            name="Approval",
            value=(
                "Approve to apply here and cascade to children.\n"
                "Decline to reject this action."
            ),
            inline=False,
        )

        embed.set_footer(text=f"Child Guild ID: {child_guild_id}")

        try:
            view = _build_decision_view(SYNC_APPROVAL_BUTTON_PREFIX)
            msg = await channel.send(embed=embed, view=view)

            # Store pending approval
            from core.approval_handler import get_approval_handler
            handler = await get_approval_handler()
            await handler.add_pending_approval(
                message_id=msg.id,
                parent_guild_id=parent_guild_id,
                child_guild_id=child_guild_id,
                action=action,
            )

            logger.info(
                "Upstream approval requested in guild %s for action from %s",
                parent_guild_id, child_guild_id
            )

            return msg.id

        except discord.HTTPException as e:
            logger.error("Failed to send approval request: %s", e)
            return None

    async def handle_approval(
        self,
        parent_guild_id: int,
        action: SyncAction,
        approved: bool,
    ) -> None:
        """
        Handle an approved or declined upstream action.

        If approved:
        - Execute in parent guild
        - Cascade to parent's other children
        - Continue upstream if parent has parents
        """
        if not approved:
            logger.info(
                "Upstream action declined in guild %s: %s for user %s",
                parent_guild_id, action.action_type, action.user_id
            )
            return

        parent_guild = self.bot.get_guild(parent_guild_id)
        if not parent_guild:
            logger.warning("Cannot access parent guild %s for approval", parent_guild_id)
            return

        # Execute in parent guild
        success = await self._execute_action(parent_guild, action)
        if not success:
            logger.warning(
                "Failed to execute approved action in guild %s",
                parent_guild_id
            )
            return

        logger.info(
            "Approved upstream action executed in guild %s: %s for user %s",
            parent_guild_id, action.action_type, action.user_id
        )

        # Check if we should cascade to siblings
        storage = await get_link_storage()
        child_link = await storage.get_child(parent_guild_id, action.origin_guild_id)

        if child_link and child_link.get("auto_cascade", True):
            # Propagate to all children (except the origin)
            children = await storage.get_children(parent_guild_id)
            for child in children:
                child_id = int(child.get("guild_id", 0))
                if child_id == action.origin_guild_id:
                    continue  # Skip the origin

                await self.propagate_downstream(
                    parent_guild_id,
                    action,
                    visited={action.origin_guild_id},
                )

        # Continue upstream if parent has parents with accept_upstream enabled
        parents = await storage.get_parents(parent_guild_id)
        for parent in parents:
            grandparent_id = int(parent.get("guild_id", 0))

            # Check if grandparent accepts upstream
            grandparent_child = await storage.get_child(grandparent_id, parent_guild_id)
            if grandparent_child and grandparent_child.get("accept_upstream"):
                # Request approval from grandparent
                await self.request_upstream_approval(
                    child_guild_id=parent_guild_id,
                    parent_guild_id=grandparent_id,
                    action=action,
                )

    async def handle_burst_downstream(
        self,
        origin_guild: discord.Guild,
        action: SyncAction,
        count: int,
        max_actions: int,
        window_seconds: int,
    ) -> None:
        """Trip protection for all downstream links from the origin guild."""
        storage = await get_link_storage()
        protection = await get_sync_protection()

        children = await storage.get_children(origin_guild.id)
        if not children:
            return

        for child in children:
            child_guild_id = int(child.get("guild_id", 0))
            if child_guild_id <= 0:
                continue

            sync_key = self._sync_setting_key(action.action_type)
            if not child.get(sync_key, True):
                continue

            cb = await protection.get_circuit_state(origin_guild.id, child_guild_id)
            if cb.state == "open":
                continue

            if cb.state != "pending_approval":
                reason = self._format_burst_reason(count, max_actions, window_seconds)
                await protection.trip_circuit(origin_guild.id, child_guild_id, reason=reason)

            if cb.approval_message_id:
                continue

            child_guild = self.bot.get_guild(child_guild_id)
            if not child_guild:
                logger.warning("Cannot request protection in guild %s: not accessible", child_guild_id)
                continue

            channel = await self._get_protection_channel(child_guild)
            if not channel:
                logger.warning("No protection channel found in guild %s", child_guild_id)
                continue

            msg_id = await self._send_protection_request(
                origin_guild,
                child_guild,
                channel,
                action,
                count,
                max_actions,
                window_seconds,
                direction_label="downstream",
            )
            if msg_id:
                await protection.set_approval_message_id(origin_guild.id, child_guild_id, msg_id)

    async def handle_burst_upstream(
        self,
        child_guild: discord.Guild,
        parent_guild_id: int,
        action: SyncAction,
        count: int,
        max_actions: int,
        window_seconds: int,
    ) -> None:
        """Trip protection for an upstream link from child to parent."""
        protection = await get_sync_protection()
        context = await self._get_upstream_context(child_guild.id, parent_guild_id)
        if not context:
            return

        _child_link, parent_guild, channel = context

        cb = await protection.get_circuit_state(child_guild.id, parent_guild_id)
        if cb.state == "open":
            return

        if cb.state != "pending_approval":
            reason = self._format_burst_reason(count, max_actions, window_seconds)
            await protection.trip_circuit(child_guild.id, parent_guild_id, reason=reason)

        if not cb.approval_message_id:
            msg_id = await self._send_protection_request(
                child_guild,
                parent_guild,
                channel,
                action,
                count,
                max_actions,
                window_seconds,
                direction_label="upstream",
            )
            if msg_id:
                await protection.set_approval_message_id(child_guild.id, parent_guild_id, msg_id)

        # Queue this action while protection is pending
        await protection.queue_action(
            child_guild.id,
            parent_guild_id,
            self._action_to_dict(action),
        )

    async def handle_protection_approval(
        self,
        from_guild_id: int,
        to_guild_id: int,
        approved: bool,
    ) -> None:
        """Handle approval or decline for a sync protection request."""
        protection = await get_sync_protection()

        if not approved:
            await protection.decline_circuit(from_guild_id, to_guild_id)
            logger.info("Protection declined for %s -> %s", from_guild_id, to_guild_id)
            return

        queued = await protection.approve_circuit(
            from_guild_id,
            to_guild_id,
            apply_queued=True,
        )

        if not queued:
            return

        storage = await get_link_storage()
        child_link = await storage.get_child(from_guild_id, to_guild_id)
        parent_link = await storage.get_parent(from_guild_id, to_guild_id)

        if child_link:
            # Downstream: apply queued actions to the child
            target_guild = self.bot.get_guild(to_guild_id)
            if not target_guild:
                logger.warning("Cannot apply queued actions to guild %s: not accessible", to_guild_id)
                return

            for data in queued:
                action = self._action_from_dict(data)
                sync_key = self._sync_setting_key(action.action_type)
                if not child_link.get(sync_key, True):
                    continue

                await self._execute_action(target_guild, action)
                visited = set(data.get("visited", [])) if isinstance(data, dict) else set()
                await self.propagate_downstream(to_guild_id, action, visited=visited)

            return

        if parent_link:
            # Upstream: re-request approvals for queued actions
            for data in queued:
                action = self._action_from_dict(data)
                await self.request_upstream_approval(
                    child_guild_id=from_guild_id,
                    parent_guild_id=to_guild_id,
                    action=action,
                )
            return

        logger.warning(
            "Protection approved but no link found for %s -> %s",
            from_guild_id,
            to_guild_id,
        )


# Button handling for approvals/protection
SYNC_APPROVAL_BUTTON_PREFIX = "sync_approval:"
SYNC_PROTECTION_BUTTON_PREFIX = "sync_protection:"


def _build_decision_view(prefix: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.green,
            custom_id=f"{prefix}approve",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Decline",
            style=discord.ButtonStyle.red,
            custom_id=f"{prefix}decline",
        )
    )
    return view


def _normalize_status_title(title: Optional[str], status: str) -> str:
    base = title or ""
    for prefix in ("APPROVED - ", "DECLINED - "):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    base = base.strip()
    if base:
        return f"{status} - {base}"
    return status


async def _update_decision_message(
    message: discord.Message,
    approved: bool,
) -> None:
    status = "APPROVED" if approved else "DECLINED"
    embed = message.embeds[0] if message.embeds else None
    if embed:
        embed.color = 0x00FF00 if approved else 0xFF0000
        embed.title = _normalize_status_title(embed.title, status)
        await message.edit(embed=embed, view=None)
        return
    await message.edit(content=status, view=None)


async def _handle_sync_approval_button(interaction: discord.Interaction) -> bool:
    if not interaction.data:
        return False

    custom_id = interaction.data.get("custom_id", "")
    if not isinstance(custom_id, str) or not custom_id.startswith(SYNC_APPROVAL_BUTTON_PREFIX):
        return False

    decision = custom_id[len(SYNC_APPROVAL_BUTTON_PREFIX):]
    if decision not in ("approve", "decline"):
        return False

    if not interaction.guild or not interaction.message:
        await interaction.response.send_message(
            "This approval can only be used in a server.",
            ephemeral=True,
        )
        return True

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.guild_permissions.administrator:
        await interaction.response.send_message(
            "You need Administrator permission to approve or decline.",
            ephemeral=True,
        )
        return True

    from core.approval_handler import get_approval_handler

    handler = await get_approval_handler()
    approval_info = await handler.consume_pending_approval(
        parent_guild_id=interaction.guild.id,
        message_id=interaction.message.id,
    )

    if not approval_info:
        await interaction.response.send_message(
            "This approval is no longer pending.",
            ephemeral=True,
        )
        return True

    approved = decision == "approve"
    action = handler.info_to_sync_action(approval_info)

    service = get_sync_service(interaction.client)
    await service.handle_approval(interaction.guild.id, action, approved)

    await _update_decision_message(interaction.message, approved)

    await interaction.response.send_message(
        "Approved." if approved else "Declined.",
        ephemeral=True,
    )
    return True


async def _handle_sync_protection_button(interaction: discord.Interaction) -> bool:
    if not interaction.data:
        return False

    custom_id = interaction.data.get("custom_id", "")
    if not isinstance(custom_id, str) or not custom_id.startswith(SYNC_PROTECTION_BUTTON_PREFIX):
        return False

    decision = custom_id[len(SYNC_PROTECTION_BUTTON_PREFIX):]
    if decision not in ("approve", "decline"):
        return False

    if not interaction.guild or not interaction.message:
        await interaction.response.send_message(
            "This action can only be used in a server.",
            ephemeral=True,
        )
        return True

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.guild_permissions.administrator:
        await interaction.response.send_message(
            "You need Administrator permission to approve or decline.",
            ephemeral=True,
        )
        return True

    from core.sync_protection import get_sync_protection

    protection = await get_sync_protection()
    match = await protection.find_circuit_by_message_id(
        message_id=interaction.message.id,
        to_guild_id=interaction.guild.id,
    )

    if not match:
        await interaction.response.send_message(
            "This approval is no longer pending.",
            ephemeral=True,
        )
        return True

    from_guild_id, to_guild_id, _cb = match
    approved = decision == "approve"

    service = get_sync_service(interaction.client)
    await service.handle_protection_approval(from_guild_id, to_guild_id, approved)

    await _update_decision_message(interaction.message, approved)

    await interaction.response.send_message(
        "Approved." if approved else "Declined.",
        ephemeral=True,
    )
    return True


def setup_sync_interactions() -> None:
    from core.interactions import register_component_handler

    register_component_handler(SYNC_APPROVAL_BUTTON_PREFIX, _handle_sync_approval_button)
    register_component_handler(SYNC_PROTECTION_BUTTON_PREFIX, _handle_sync_protection_button)


# Global singleton
_sync_service: Optional[SyncService] = None


def get_sync_service(bot: discord.Client) -> SyncService:
    """Get or create the global SyncService instance."""
    global _sync_service
    if _sync_service is None:
        _sync_service = SyncService(bot)
    return _sync_service


async def sync_action_downstream(
    bot: discord.Client,
    origin_guild: discord.Guild,
    action_type: ActionType,
    user_id: int,
    reason: str,
    mod_id: int,
    duration: Optional[int] = None,
) -> List[int]:
    """
    Convenience function to sync an action downstream.

    Returns list of guild IDs that received the action.
    """
    service = get_sync_service(bot)

    action = SyncAction(
        action_type=action_type,
        user_id=user_id,
        reason=reason,
        mod_id=mod_id,
        origin_guild_id=origin_guild.id,
        origin_guild_name=origin_guild.name,
        timestamp=dt_to_iso(utcnow()),
        duration=duration,
    )

    protection = await get_sync_protection()
    await protection.record_action(origin_guild.id, action_type, user_id)
    is_burst, count, max_actions = await protection.check_burst(origin_guild.id)
    if is_burst:
        window_seconds, _ = await protection.get_guild_thresholds(origin_guild.id)
        await service.handle_burst_downstream(origin_guild, action, count, max_actions, window_seconds)

    return await service.propagate_downstream(origin_guild.id, action)


async def request_upstream(
    bot: discord.Client,
    origin_guild: discord.Guild,
    action_type: ActionType,
    user_id: int,
    reason: str,
    mod_id: int,
    duration: Optional[int] = None,
    record_action: bool = True,
) -> bool:
    """
    Convenience function to request upstream approval for an action.

    Returns True if at least one approval request was sent.

    record_action=False skips recording for burst detection (use if already recorded).
    """
    service = get_sync_service(bot)
    storage = await get_link_storage()

    action = SyncAction(
        action_type=action_type,
        user_id=user_id,
        reason=reason,
        mod_id=mod_id,
        origin_guild_id=origin_guild.id,
        origin_guild_name=origin_guild.name,
        timestamp=dt_to_iso(utcnow()),
        duration=duration,
    )

    protection = await get_sync_protection()
    if record_action:
        await protection.record_action(origin_guild.id, action_type, user_id)
    is_burst, count, max_actions = await protection.check_burst(origin_guild.id)
    window_seconds, _ = await protection.get_guild_thresholds(origin_guild.id)

    # Get all parents
    parents = await storage.get_parents(origin_guild.id)
    sent_any = False

    for parent in parents:
        parent_id = int(parent.get("guild_id", 0))
        if parent_id <= 0:
            continue

        if is_burst:
            await service.handle_burst_upstream(
                origin_guild,
                parent_id,
                action,
                count,
                max_actions,
                window_seconds,
            )
            continue

        msg_id = await service.request_upstream_approval(
            child_guild_id=origin_guild.id,
            parent_guild_id=parent_id,
            action=action,
        )
        if msg_id:
            sent_any = True

    return sent_any
