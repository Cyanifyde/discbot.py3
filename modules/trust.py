"""
Trust module - trust score calculation, vouching system, and reputation management.

Provides commands for viewing trust scores, vouching for users, and checking trust history.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Optional

import discord

from core.help_system import help_system
from core.trust_storage import TrustStore
from core.permissions import can_use_command, is_module_enabled
from core.types import Vouch
from core.utils import utcnow, dt_to_iso, iso_to_dt
from services.trust_service import get_trust_service

logger = logging.getLogger("discbot.trust")

MODULE_NAME = "trust"

# Vouch cooldown: 30 days
VOUCH_COOLDOWN_DAYS = 30


def setup_trust() -> None:
    """Register help information for the trust module."""
    help_system.register_module(
        name="Trust",
        description="Trust score system for reputation management and vouching.",
        help_command="trust help",
        commands=[
            ("trust score [@user]", "View trust score breakdown for yourself or another user"),
            ("trust history [@user]", "View trust events history"),
            ("vouch @user <proof_url>", "Vouch for another user with transaction proof"),
            ("vouch list [@user]", "View vouches received by a user"),
            ("vouch given [@user]", "View vouches given by a user"),
            ("vouch verify <vouch_id>", "Verify a vouch (mod only)"),
            ("vouch remove <vouch_id>", "Request removal of a vouch (mod only)"),
            ("trust help", "Show this help message"),
        ],
    )


async def handle_trust_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle trust-related commands.

    Returns True if command was handled, False otherwise.
    """
    if not message.guild:
        return False

    # Check if module is enabled
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False

    content = message.content.strip()
    parts = content.split(maxsplit=2)

    if len(parts) < 2:
        return False

    command = parts[0].lower()
    subcommand = parts[1].lower()

    # Trust score commands
    if command == "trust":
        if subcommand == "score":
            await _handle_trust_score(message, parts, bot)
            return True
        elif subcommand == "history":
            await _handle_trust_history(message, parts, bot)
            return True
        elif subcommand == "help":
            await _handle_trust_help(message)
            return True

    # Vouch commands
    elif command == "vouch":
        if subcommand == "list":
            await _handle_vouch_list(message, parts)
            return True
        elif subcommand == "given":
            await _handle_vouch_given(message, parts)
            return True
        elif subcommand == "verify":
            await _handle_vouch_verify(message, parts, bot)
            return True
        elif subcommand == "remove":
            await _handle_vouch_remove(message, parts)
            return True
        elif len(parts) >= 3:
            # vouch @user <proof_url>
            await _handle_vouch_create(message, parts)
            return True

    return False


async def _handle_trust_score(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle 'trust score [@user]' command."""
    # Get target user
    target_user = message.author
    if len(parts) >= 3 and message.mentions:
        target_user = message.mentions[0]

    try:
        service = get_trust_service(bot)
        score = await service.get_score(target_user.id, message.guild.id)

        if score is None:
            # Calculate for the first time
            score = await service.calculate_score(target_user.id, message.guild.id)

        # Build embed
        embed = discord.Embed(
            title=f"Trust Score: {target_user.display_name}",
            description=f"**Total Score:** {score.total_score:.1f}/100\n**Tier:** {score.tier.replace('_', ' ').title()}",
            color=_get_tier_color(score.tier),
            timestamp=discord.utils.utcnow(),
        )

        # Add component scores
        embed.add_field(
            name="Score Breakdown",
            value=(
                f"**Children Count:** {score.children_count_score:.1f}/100 (15% weight)\n"
                f"**Upflow Status:** {score.upflow_status_score:.1f}/100 (20% weight)\n"
                f"**Vouches:** {score.vouches_score:.1f}/100 (25% weight)\n"
                f"**Link Age:** {score.link_age_score:.1f}/100 (15% weight)\n"
                f"**Approval Rate:** {score.approval_rate_score:.1f}/100 (25% weight)"
            ),
            inline=False,
        )

        # Add permissions info
        permissions = []
        if service.check_action_permission(score.total_score, "cross_server_sync"):
            permissions.append(" Cross-server sync")
        else:
            permissions.append(" Cross-server sync (requires 50+)")

        if service.check_action_permission(score.total_score, "vouch_others"):
            permissions.append(" Vouch for others")
        else:
            permissions.append(" Vouch for others (requires 60+)")

        if service.check_action_permission(score.total_score, "mediate_disputes"):
            permissions.append(" Mediate disputes")
        else:
            permissions.append(" Mediate disputes (requires 80+)")

        embed.add_field(
            name="Permissions",
            value="\n".join(permissions),
            inline=False,
        )

        embed.set_footer(text=f"Last updated: {score.last_updated}")

        await message.channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Error handling trust score command: {e}", exc_info=True)
        await message.channel.send(" Error retrieving trust score.")


async def _handle_trust_history(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle 'trust history [@user]' command."""
    # Get target user
    target_user = message.author
    if len(parts) >= 3 and message.mentions:
        target_user = message.mentions[0]

    # Check permissions - only mods or self can view history
    if target_user.id != message.author.id:
        if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "trust history"):
            await message.channel.send(" You don't have permission to view other users' trust history.")
            return

    try:
        store = TrustStore(message.guild.id)
        await store.initialize()

        events = await store.get_events(target_user.id)

        if not events:
            await message.channel.send(f"No trust events found for {target_user.display_name}.")
            return

        # Sort by timestamp descending (most recent first)
        events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

        # Build embed
        embed = discord.Embed(
            title=f"Trust History: {target_user.display_name}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        # Show last 10 events
        recent_events = events[:10]
        lines = []
        for event in recent_events:
            event_type = event.get("event_type", "unknown")
            positive = event.get("positive", False)
            weight = event.get("weight", 0)
            timestamp = event.get("timestamp", "")
            details = event.get("details", "")

            emoji = "" if positive else ""
            lines.append(f"{emoji} **{event_type}** (+{weight:.1f}) - {timestamp[:10]}")
            if details:
                lines.append(f"   _{details}_")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Showing {len(recent_events)} of {len(events)} events")

        await message.channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Error handling trust history command: {e}", exc_info=True)
        await message.channel.send(" Error retrieving trust history.")


async def _handle_vouch_create(message: discord.Message, parts: list[str]) -> None:
    """Handle 'vouch @user <proof_url>' command."""
    if not message.mentions:
        await message.channel.send(" Please mention a user to vouch for.")
        return

    if len(parts) < 3:
        await message.channel.send(" Usage: `vouch @user <proof_url>`")
        return

    target_user = message.mentions[0]
    proof_url = parts[2]

    # Can't vouch for yourself
    if target_user.id == message.author.id:
        await message.channel.send(" You cannot vouch for yourself.")
        return

    try:
        # Check trust score requirement
        service = get_trust_service(message.guild._state._get_client())
        author_score = await service.get_score(message.author.id, message.guild.id)

        if author_score is None or not service.check_action_permission(author_score.total_score, "vouch_others"):
            await message.channel.send(" You need a trust score of 60+ to vouch for others.")
            return

        # Check cooldown
        store = TrustStore(message.guild.id)
        await store.initialize()

        cooldown_expires = await store.check_vouch_cooldown(message.author.id, target_user.id)
        if cooldown_expires:
            try:
                expires_dt = iso_to_dt(cooldown_expires)
                if utcnow() < expires_dt:
                    days_left = (expires_dt - utcnow()).days
                    await message.channel.send(f" You can vouch for {target_user.display_name} again in {days_left} days.")
                    return
            except Exception:
                pass

        # Create vouch
        vouch = Vouch(
            id=str(uuid.uuid4()),
            from_user_id=message.author.id,
            to_user_id=target_user.id,
            guild_id=message.guild.id,
            proof_type="screenshot",  # Could be expanded later
            proof_url=proof_url,
            created_at=dt_to_iso(utcnow()),
        )

        await store.add_vouch(vouch)

        # Set cooldown
        cooldown_expires_dt = utcnow() + timedelta(days=VOUCH_COOLDOWN_DAYS)
        await store.set_vouch_cooldown(message.author.id, target_user.id, dt_to_iso(cooldown_expires_dt))

        # Check for mutual vouch
        existing_vouches = await store.get_vouches_given(target_user.id)
        mutual = any(v.to_user_id == message.author.id for v in existing_vouches)

        if mutual:
            # Mark both as mutual
            await store.update_vouch(vouch.id, {"mutual": True})
            # Find and update the reverse vouch
            for v in existing_vouches:
                if v.to_user_id == message.author.id:
                    await store.update_vouch(v.id, {"mutual": True})
                    break

        # Recalculate target's trust score
        await service.calculate_score(target_user.id, message.guild.id, store=store)

        # Send confirmation
        embed = discord.Embed(
            title=" Vouch Created",
            description=f"{message.author.mention} vouched for {target_user.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Proof", value=proof_url, inline=False)
        if mutual:
            embed.add_field(name="Mutual Vouch", value="This is a mutual vouch!", inline=False)
        embed.set_footer(text=f"Vouch ID: {vouch.id}")

        await message.channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Error creating vouch: {e}", exc_info=True)
        await message.channel.send(" Error creating vouch.")


async def _handle_vouch_list(message: discord.Message, parts: list[str]) -> None:
    """Handle 'vouch list [@user]' command."""
    target_user = message.author
    if len(parts) >= 3 and message.mentions:
        target_user = message.mentions[0]

    try:
        store = TrustStore(message.guild.id)
        await store.initialize()

        vouches = await store.get_vouches_for(target_user.id)

        if not vouches:
            await message.channel.send(f"No vouches found for {target_user.display_name}.")
            return

        # Build embed
        embed = discord.Embed(
            title=f"Vouches Received: {target_user.display_name}",
            description=f"Total: {len(vouches)} vouches",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        # Show recent vouches (last 5)
        recent = sorted(vouches, key=lambda v: v.created_at, reverse=True)[:5]
        for vouch in recent:
            from_user = message.guild.get_member(vouch.from_user_id)
            from_name = from_user.display_name if from_user else f"User {vouch.from_user_id}"

            mutual_text = " (Mutual)" if vouch.mutual else ""
            verified_text = " (verified)" if vouch.verified_by_mod else ""

            embed.add_field(
                name=f"{from_name}{mutual_text}{verified_text}",
                value=f"Type: {vouch.transaction_type}\nCreated: {vouch.created_at[:10]}",
                inline=True,
            )

        embed.set_footer(text=f"Showing {len(recent)} of {len(vouches)} vouches")

        await message.channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Error listing vouches: {e}", exc_info=True)
        await message.channel.send(" Error retrieving vouches.")


async def _handle_vouch_given(message: discord.Message, parts: list[str]) -> None:
    """Handle 'vouch given [@user]' command."""
    target_user = message.author
    if len(parts) >= 3 and message.mentions:
        target_user = message.mentions[0]

    try:
        store = TrustStore(message.guild.id)
        await store.initialize()

        vouches = await store.get_vouches_given(target_user.id)

        if not vouches:
            await message.channel.send(f"No vouches given by {target_user.display_name}.")
            return

        # Build embed
        embed = discord.Embed(
            title=f"Vouches Given: {target_user.display_name}",
            description=f"Total: {len(vouches)} vouches",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        # Show recent vouches (last 5)
        recent = sorted(vouches, key=lambda v: v.created_at, reverse=True)[:5]
        for vouch in recent:
            to_user = message.guild.get_member(vouch.to_user_id)
            to_name = to_user.display_name if to_user else f"User {vouch.to_user_id}"

            mutual_text = " (Mutual)" if vouch.mutual else ""
            verified_text = " (verified)" if vouch.verified_by_mod else ""

            embed.add_field(
                name=f"{to_name}{mutual_text}{verified_text}",
                value=f"Type: {vouch.transaction_type}\nCreated: {vouch.created_at[:10]}",
                inline=True,
            )

        embed.set_footer(text=f"Showing {len(recent)} of {len(vouches)} vouches")

        await message.channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Error listing given vouches: {e}", exc_info=True)
        await message.channel.send(" Error retrieving vouches.")


async def _handle_vouch_verify(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle 'vouch verify <vouch_id>' command (mod only)."""
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "vouch verify"):
        await message.channel.send(" You don't have permission to verify vouches.")
        return

    if len(parts) < 3:
        await message.channel.send(" Usage: `vouch verify <vouch_id>`")
        return

    vouch_id = parts[2]

    try:
        store = TrustStore(message.guild.id)
        await store.initialize()

        vouch = await store.get_vouch(vouch_id)
        if not vouch:
            await message.channel.send(f" Vouch `{vouch_id}` not found.")
            return

        if vouch.verified_by_mod is not None:
            await message.channel.send(f" Vouch `{vouch_id}` is already verified.")
            return

        await store.update_vouch(
            vouch_id,
            {"verified_by_mod": message.author.id, "verified_at": dt_to_iso(utcnow())},
        )

        service = get_trust_service(bot)
        await service.calculate_score(vouch.to_user_id, message.guild.id, store=store)

        to_user = message.guild.get_member(vouch.to_user_id)
        to_mention = to_user.mention if to_user else f"`{vouch.to_user_id}`"

        embed = discord.Embed(
            title=" Vouch Verified",
            description=f"Vouch `{vouch_id}` verified for {to_mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Verified By", value=message.author.mention, inline=False)
        await message.channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Error verifying vouch: {e}", exc_info=True)
        await message.channel.send(" Error verifying vouch.")


async def _handle_vouch_remove(message: discord.Message, parts: list[str]) -> None:
    """Handle 'vouch remove <vouch_id>' command (mod only)."""
    # Check permissions
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "vouch remove"):
        await message.channel.send(" You don't have permission to remove vouches.")
        return

    if len(parts) < 3:
        await message.channel.send(" Usage: `vouch remove <vouch_id>`")
        return

    vouch_id = parts[2]

    try:
        store = TrustStore(message.guild.id)
        await store.initialize()

        success = await store.remove_vouch(vouch_id)

        if success:
            await message.channel.send(f" Vouch `{vouch_id}` removed.")
        else:
            await message.channel.send(f" Vouch `{vouch_id}` not found.")

    except Exception as e:
        logger.error(f"Error removing vouch: {e}", exc_info=True)
        await message.channel.send(" Error removing vouch.")


async def _handle_trust_help(message: discord.Message) -> None:
    """Handle 'trust help' command."""
    embed = help_system.get_module_help("Trust")
    if embed:
        await message.channel.send(embed=embed)
    else:
        await message.channel.send(" Help information not available.")


def _get_tier_color(tier: str) -> discord.Color:
    """Get embed color based on trust tier."""
    colors = {
        "untrusted": discord.Color.red(),
        "neutral": discord.Color.light_gray(),
        "trusted": discord.Color.green(),
        "highly_trusted": discord.Color.gold(),
    }
    return colors.get(tier, discord.Color.blue())
