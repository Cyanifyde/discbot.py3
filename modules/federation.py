"""
Federation module - multi-server federation management.

Provides commands for creating and managing cross-server federations.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from services.federation_service import federation_service

logger = logging.getLogger("discbot.federation")

MODULE_NAME = "federation"


def setup_federation() -> None:
    """Register help information for the federation module."""
    help_system.register_module(
        name="Federation",
        description="Multi-server federation system for cross-server collaboration.",
        help_command="federation help",
        commands=[
            ("federation create <name>", "Create new federation (parent)"),
            ("federation invite", "Generate invite key"),
            ("federation join <key>", "Join federation"),
            ("federation leave", "Leave federation"),
            ("federation members", "List members"),
            ("federation tier <guild_id> <tier>", "Set tier (admin)"),
            ("federation settings", "View/edit settings"),
            ("federation blocklist check <user>", "Check blocklist"),
            ("federation blocklist add <user> <reason> [evidence_url]", "Add to blocklist"),
            ("federation blocklist remove <user>", "Remove from blocklist"),
            ("federation vote start <topic>", "Start vote (admin)"),
            ("federation vote cast <vote_id> <option>", "Cast vote"),
            ("federation stats", "View statistics"),
            ("federation audit [query]", "View audit log"),
            ("federation announce <message>", "Push announcement (admin)"),
            ("federation help", "Show this help message"),
        ],
    )


async def handle_federation_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle federation-related commands.

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

    if command != "federation":
        return False

    # Route to handlers
    if subcommand == "create":
        await _handle_create(message, parts)
        return True
    elif subcommand == "invite":
        await _handle_invite(message)
        return True
    elif subcommand == "join":
        await _handle_join(message, parts)
        return True
    elif subcommand == "leave":
        await _handle_leave(message)
        return True
    elif subcommand == "members":
        await _handle_members(message)
        return True
    elif subcommand == "tier":
        await _handle_tier(message, parts)
        return True
    elif subcommand == "settings":
        await _handle_settings(message)
        return True
    elif subcommand == "blocklist":
        await _handle_blocklist(message, parts)
        return True
    elif subcommand == "vote":
        await _handle_vote(message, parts)
        return True
    elif subcommand == "stats":
        await _handle_stats(message)
        return True
    elif subcommand == "audit":
        await _handle_audit(message, parts)
        return True
    elif subcommand == "announce":
        await _handle_announce(message, parts, bot)
        return True
    elif subcommand == "help":
        await _handle_help(message)
        return True

    return False


# ─── Command Handlers ─────────────────────────────────────────────────────────


async def _handle_create(message: discord.Message, parts: list[str]) -> None:
    """Handle 'federation create <name>' command."""
    # Check admin permissions
    if not message.author.guild_permissions.administrator:
        await message.reply(" You need Administrator permission to create a federation.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `federation create <name>`")
        return

    name = parts[2]

    federation_id = await federation_service.create_federation(
        name,
        message.guild.id,
    )

    await message.reply(
        f" Federation **{name}** created!\n"
        f"**Federation ID:** `{federation_id[:8]}`\n\n"
        f"Use `federation invite` to generate invite keys for other servers."
    )


async def _handle_invite(message: discord.Message) -> None:
    """Handle 'federation invite' command."""
    # Check admin permissions
    if not message.author.guild_permissions.administrator:
        await message.reply(" You need Administrator permission to generate invites.")
        return

    # Find federation for this guild
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    # Find federation where this guild is parent or member
    federation_id = None
    for fed in all_feds:
        if fed["parent_guild_id"] == guild_id:
            federation_id = fed["id"]
            break

    if not federation_id:
        # Check if member
        for fed in all_feds:
            member = await federation_service.get_member(fed["id"], guild_id)
            if member and member.admin_enabled:
                federation_id = fed["id"]
                break

    if not federation_id:
        await message.reply(" This server is not part of a federation or lacks admin permissions.")
        return

    # Generate invite
    invite_key = await federation_service.create_invite(federation_id, tier="member")

    await message.reply(
        f" Invite key generated: `{invite_key}`\n\n"
        f"Other servers can join using: `federation join {invite_key}`"
    )


async def _handle_join(message: discord.Message, parts: list[str]) -> None:
    """Handle 'federation join <key>' command."""
    # Check admin permissions
    if not message.author.guild_permissions.administrator:
        await message.reply(" You need Administrator permission to join a federation.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `federation join <key>`")
        return

    invite_key = parts[2]

    # Try to find federation with this key
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        success = await federation_service.join_federation(
            message.guild.id,
            message.guild.name,
            fed["id"],
            invite_key,
        )

        if success:
            await message.reply(
                f" Joined federation **{fed['name']}**!\n\n"
                f"You can now participate in cross-server features."
            )
            return

    await message.reply(" Invalid invite key.")


async def _handle_leave(message: discord.Message) -> None:
    """Handle 'federation leave' command."""
    # Check admin permissions
    if not message.author.guild_permissions.administrator:
        await message.reply(" You need Administrator permission to leave a federation.")
        return

    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    # Find federation where this guild is a member
    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            success = await federation_service.leave_federation(guild_id, fed["id"])
            if success:
                await message.reply(f" Left federation **{fed['name']}**")
            else:
                await message.reply(" Failed to leave federation")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_members(message: discord.Message) -> None:
    """Handle 'federation members' command."""
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    # Find federation
    federation_id = None
    federation_name = None
    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            federation_id = fed["id"]
            federation_name = fed["name"]
            break

    if not federation_id:
        await message.reply(" This server is not part of any federation.")
        return

    # Get all members
    members = await federation_service.get_all_members(federation_id)

    embed = discord.Embed(
        title=f"Federation Members - {federation_name}",
        description=f"Total: {len(members)}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    # Show first 10 members
    for member in members[:10]:
        tier_emoji = {
            "observer": "",
            "member": "",
            "trusted": "",
            "core": "",
        }
        emoji = tier_emoji.get(member.tier, "")

        value = (
            f"**Tier:** {member.tier} {emoji}\n"
            f"**Joined:** {member.joined_at[:10]}\n"
            f"**Reputation:** {member.reputation:.1f}"
        )

        embed.add_field(
            name=member.guild_name,
            value=value,
            inline=True,
        )

    await message.reply(embed=embed)


async def _handle_tier(message: discord.Message, parts: list[str]) -> None:
    """Handle 'federation tier <guild_id> <tier>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `federation tier <guild_id> <tier>`")
        return

    args = parts[2].split()
    if len(args) < 2:
        await message.reply(" Usage: `federation tier <guild_id> <tier>`")
        return

    try:
        target_guild_id = int(args[0])
    except ValueError:
        await message.reply(" Invalid guild ID")
        return

    new_tier = args[1].lower()
    if new_tier not in ["observer", "member", "trusted", "core"]:
        await message.reply(" Invalid tier. Options: observer, member, trusted, core")
        return

    # Find federation
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            success = await federation_service.set_member_tier(
                fed["id"],
                target_guild_id,
                new_tier,
                guild_id,
            )

            if success:
                await message.reply(f" Set tier to **{new_tier}**")
            else:
                await message.reply(" Failed to set tier. You may lack admin permissions.")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_settings(message: discord.Message) -> None:
    """Handle 'federation settings' command."""
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    # Find federation
    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            embed = discord.Embed(
                title=f"Federation Settings - {fed['name']}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )

            settings = fed["settings"]

            embed.add_field(
                name="Voting Threshold",
                value=f"{settings['voting_threshold'] * 100:.0f}%",
                inline=True,
            )

            embed.add_field(
                name="Min Reputation to Join",
                value=str(settings["min_reputation_to_join"]),
                inline=True,
            )

            embed.add_field(
                name="Your Tier",
                value=member.tier,
                inline=True,
            )

            embed.add_field(
                name="Your Permissions",
                value=(
                    f"Sync Receive: {'' if member.sync_receive else ''}\n"
                    f"Sync Send: {'' if member.sync_send else ''}\n"
                    f"Vote: {'' if member.vote_enabled else ''}\n"
                    f"Admin: {'' if member.admin_enabled else ''}"
                ),
                inline=False,
            )

            await message.reply(embed=embed)
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_blocklist(message: discord.Message, parts: list[str]) -> None:
    """Handle blocklist commands."""
    if len(parts) < 3:
        await message.reply(" Usage: `federation blocklist <check|add|remove>`")
        return

    args = parts[2].split(maxsplit=1)
    action = args[0].lower()

    if action == "check":
        await _handle_blocklist_check(message, args)
    elif action == "add":
        await _handle_blocklist_add(message, args)
    elif action == "remove":
        await _handle_blocklist_remove(message, args)
    else:
        await message.reply(" Usage: `federation blocklist <check|add|remove>`")


async def _handle_blocklist_check(message: discord.Message, args: list[str]) -> None:
    """Check if user is on blocklist."""
    if not message.mentions:
        await message.reply(" Please mention a user to check")
        return

    user = message.mentions[0]
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            entry = await federation_service.check_blocklist(fed["id"], user.id)

            if entry:
                await message.reply(
                    f" {user.mention} is on the federation blocklist\n"
                    f"**Reason:** {entry['reason']}\n"
                    f"**Confirmations:** {len(entry['confirmations'])} servers"
                )
            else:
                await message.reply(f" {user.mention} is not on the blocklist")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_blocklist_add(message: discord.Message, args: list[str]) -> None:
    """Add user to blocklist."""
    if not message.mentions:
        await message.reply(" Please mention a user")
        return

    if len(args) < 2:
        await message.reply(" Usage: `federation blocklist add @user <reason> [evidence_url]`")
        return

    user = message.mentions[0]
    rest = args[1].strip()
    rest = re.sub(r"<@!?\d+>\s*", "", rest).strip()

    evidence_url = None
    tokens = rest.split()
    if tokens and tokens[-1].startswith(("http://", "https://")):
        evidence_url = tokens.pop(-1)

    reason = " ".join(tokens).strip() or "No reason provided"

    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            success = await federation_service.add_to_blocklist(
                fed["id"],
                user.id,
                reason,
                evidence_url or "No evidence URL provided",
                guild_id,
            )

            if success:
                await message.reply(f" Added {user.mention} to federation blocklist")
            else:
                await message.reply(" Failed to add to blocklist. You may lack permissions.")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_blocklist_remove(message: discord.Message, args: list[str]) -> None:
    """Remove user from blocklist."""
    if not message.mentions:
        await message.reply(" Please mention a user")
        return

    user = message.mentions[0]
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            success = await federation_service.remove_from_blocklist(
                fed["id"],
                user.id,
                guild_id,
            )

            if success:
                await message.reply(f" Removed {user.mention} from blocklist")
            else:
                await message.reply(" Failed to remove. You may lack admin permissions.")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_vote(message: discord.Message, parts: list[str]) -> None:
    """Handle vote commands."""
    if len(parts) < 3:
        await message.reply(" Usage: `federation vote <start|cast>`")
        return

    args = parts[2].split(maxsplit=1)
    action = args[0].lower()

    if action == "start":
        await _handle_vote_start(message, args)
    elif action == "cast":
        await _handle_vote_cast(message, args)
    else:
        await message.reply(" Usage: `federation vote <start|cast>`")


async def _handle_vote_start(message: discord.Message, args: list[str]) -> None:
    """Start a vote."""
    if len(args) < 2:
        await message.reply(" Usage: `federation vote start <topic>`")
        return

    topic = args[1]
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            vote_id = await federation_service.start_vote(
                fed["id"],
                topic,
                ["yes", "no"],
                72,  # 72 hours
                guild_id,
            )

            if vote_id:
                await message.reply(
                    f" Vote started!\n"
                    f"**Topic:** {topic}\n"
                    f"**Vote ID:** `{vote_id[:8]}`\n"
                    f"**Duration:** 72 hours\n\n"
                    f"Members can vote using: `federation vote cast {vote_id[:8]} <yes|no>`"
                )
            else:
                await message.reply(" Failed to start vote. You may lack admin permissions.")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_vote_cast(message: discord.Message, args: list[str]) -> None:
    """Cast a vote."""
    if len(args) < 2:
        await message.reply(" Usage: `federation vote cast <vote_id> <option>`")
        return

    vote_args = args[1].split()
    if len(vote_args) < 2:
        await message.reply(" Usage: `federation vote cast <vote_id> <option>`")
        return

    vote_id_partial = vote_args[0]
    option = vote_args[1]

    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            # Find vote by partial ID
            # For simplicity, using full ID for now
            success = await federation_service.cast_vote(
                fed["id"],
                vote_id_partial,
                guild_id,
                option,
            )

            if success:
                await message.reply(f" Vote cast: **{option}**")
            else:
                await message.reply(" Failed to cast vote. Check vote ID and permissions.")
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_stats(message: discord.Message) -> None:
    """Handle 'federation stats' command."""
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            stats = await federation_service.get_federation_stats(fed["id"])

            embed = discord.Embed(
                title=f"Federation Statistics - {fed['name']}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )

            embed.add_field(
                name="Total Members",
                value=str(stats["total_members"]),
                inline=True,
            )

            embed.add_field(
                name="Blocklist Entries",
                value=str(stats["blocklist_size"]),
                inline=True,
            )

            embed.add_field(
                name="Directory Entries",
                value=str(stats["directory_size"]),
                inline=True,
            )

            # Tier breakdown
            by_tier = stats.get("by_tier", {})
            if by_tier:
                tier_str = "\n".join(
                    f"• **{tier.title()}:** {count}"
                    for tier, count in by_tier.items()
                )
                embed.add_field(
                    name="By Tier",
                    value=tier_str,
                    inline=False,
                )

            await message.reply(embed=embed)
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_audit(message: discord.Message, parts: list[str]) -> None:
    """Handle 'federation audit [query]' command."""
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member:
            # Get audit log
            entries = await federation_service.get_audit_log(fed["id"], limit=10)

            embed = discord.Embed(
                title=f"Federation Audit Log - {fed['name']}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )

            for entry in entries:
                action = entry.get("action", "unknown")
                timestamp = entry.get("timestamp", "")[:16]
                details_str = ""

                details = entry.get("details", {})
                if details:
                    details_str = "\n".join(f"• {k}: {v}" for k, v in list(details.items())[:3])

                embed.add_field(
                    name=f"{action} ({timestamp})",
                    value=details_str or "No details",
                    inline=False,
                )

            await message.reply(embed=embed)
            return

    await message.reply(" This server is not part of any federation.")


async def _handle_announce(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'federation announce <message>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `federation announce <message>`")
        return

    announcement = parts[2]
    guild_id = message.guild.id
    all_feds = await federation_service.get_all_federations()

    for fed in all_feds:
        member = await federation_service.get_member(fed["id"], guild_id)
        if member and member.admin_enabled:
            # Propagate to all members
            recipients = await federation_service.propagate_action(
                fed["id"],
                "announcement",
                {"message": announcement},
                guild_id,
            )

            await message.reply(
                f" Announcement sent to **{len(recipients)}** servers"
            )
            return

    await message.reply(" This server is not part of any federation or lacks admin permissions.")


async def _handle_help(message: discord.Message) -> None:
    """Handle 'federation help' command."""
    help_text = help_system.get_module_help("Federation")
    if help_text:
        await message.reply(embed=help_text)
    else:
        await message.reply(" Help information not available.")
