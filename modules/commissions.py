"""
Commission module - commission tracking, queue management, and client handling.

Provides commands for artists to manage their commission queues, waitlists, and client relationships.
"""
from __future__ import annotations

import logging
import json
import io
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from core.utils import parse_deadline, format_commission_status, dt_to_iso, utcnow, iso_to_dt
from services.commission_service import commission_service
from classes.profile import get_profile

logger = logging.getLogger("discbot.commissions")

MODULE_NAME = "commissions"


def setup_commissions() -> None:
    """Register help information for the commissions module."""
    help_system.register_module(
        name="Commissions",
        description="Commission tracking and queue management for artists.",
        help_command="commission help",
        commands=[
            ("commission create @client [details]", "Create a new commission"),
            ("commission stage <id> <stage>", "Advance commission to a new stage"),
            ("commission list [status]", "List active commissions"),
            ("commission status [@user]", "View commission status widget"),
            ("commission waitlist", "View waitlist entries"),
            ("commission waitlist add @client [notes]", "Add client to waitlist"),
            ("commission waitlist remove <id>", "Remove entry from waitlist"),
            ("commission slots <count>", "Set total commission slots"),
            ("commission autoclose <on|off>", "Toggle auto-close when slots full"),
            ("commission stages set <stage1, stage2, ...>", "Set custom stages"),
            ("commission deadline <id> <date>", "Set commission deadline"),
            ("commission tag <id> <tags...>", "Add tags to commission"),
            ("commission revision <id>", "Log a revision request"),
            ("commission blacklist add @user <reason>", "Blacklist a client"),
            ("commission blacklist remove @user", "Remove from blacklist"),
            ("commission blacklist list", "View blacklisted clients"),
            ("commission invoice <id>", "Show invoice (embed)"),
            ("commission contract <id>", "Show contract terms (embed)"),
            ("commission tos set <url>", "Set Terms of Service URL (for contract embed)"),
            ("commission tos clear", "Clear Terms of Service URL"),
            ("commission tos view", "View Terms of Service URL"),
            ("commission payment confirm <id>", "Confirm payment received"),
            ("commission archive <id>", "Archive a commission (moves to history)"),
            ("commission export <id>", "Export a commission as JSON"),
            ("commission summary", "View commission statistics"),
            ("commission quickadd @client <price> <type> [deadline]", "Quick-add commission"),
            ("commission search <query>", "Search commissions by tag or client"),
            ("commission help", "Show this help message"),
        ],
    )


async def handle_commission_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle commission-related commands.

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

    if command != "commission":
        return False

    # Route to handlers
    if subcommand == "create":
        await _handle_create(message, parts, bot)
        return True
    elif subcommand == "stage":
        await _handle_stage(message, parts)
        return True
    elif subcommand == "list":
        await _handle_list(message, parts)
        return True
    elif subcommand == "status":
        await _handle_status(message, parts, bot)
        return True
    elif subcommand == "waitlist":
        await _handle_waitlist(message, parts)
        return True
    elif subcommand == "slots":
        await _handle_slots(message, parts)
        return True
    elif subcommand == "autoclose":
        await _handle_autoclose(message, parts)
        return True
    elif subcommand == "stages":
        await _handle_stages(message, parts)
        return True
    elif subcommand == "deadline":
        await _handle_deadline(message, parts)
        return True
    elif subcommand == "tag":
        await _handle_tag(message, parts)
        return True
    elif subcommand == "revision":
        await _handle_revision(message, parts)
        return True
    elif subcommand == "blacklist":
        await _handle_blacklist(message, parts)
        return True
    elif subcommand == "invoice":
        await _handle_invoice(message, parts, bot)
        return True
    elif subcommand == "contract":
        await _handle_contract(message, parts, bot)
        return True
    elif subcommand == "tos":
        await _handle_tos(message, parts)
        return True
    elif subcommand == "payment":
        await _handle_payment(message, parts)
        return True
    elif subcommand == "archive":
        await _handle_archive(message, parts)
        return True
    elif subcommand == "export":
        await _handle_export(message, parts)
        return True
    elif subcommand == "summary":
        await _handle_summary(message)
        return True
    elif subcommand == "quickadd":
        await _handle_quickadd(message, parts, bot)
        return True
    elif subcommand == "search":
        await _handle_search(message, parts)
        return True
    elif subcommand == "help":
        await _handle_help(message)
        return True

    return False


# ─── Command Handlers ─────────────────────────────────────────────────────────


async def _handle_create(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'commission create @client [details]' command."""
    if not message.mentions:
        await message.reply(" Please mention a client to create a commission for.")
        return

    client = message.mentions[0]
    artist_id = message.author.id
    guild_id = message.guild.id

    # Check blacklist
    is_blacklisted = await commission_service.check_blacklist(artist_id, guild_id, client.id)
    if is_blacklisted:
        await message.reply(f" {client.mention} is blacklisted. Remove them from your blacklist first.")
        return

    # Create commission with default values
    commission = await commission_service.create_commission(
        artist_id=artist_id,
        client_id=client.id,
        guild_id=guild_id,
    )

    await message.reply(
        f" Commission created!\n"
        f"**ID:** `{commission.id[:8]}`\n"
        f"**Client:** {client.mention}\n"
        f"**Stage:** {commission.stage}\n\n"
        f"Use `commission stage {commission.id[:8]} <stage>` to advance it."
    )


async def _handle_stage(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission stage <id> <stage>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission stage <id> <stage>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `commission stage <id> <stage>`")
        return

    commission_id = args[0]
    new_stage = args[1]

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission by partial ID
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    # Get valid stages
    stages = await commission_service.get_custom_stages(artist_id, guild_id)

    if new_stage not in stages:
        await message.reply(
            f" Invalid stage. Valid stages:\n" + ", ".join(f"`{s}`" for s in stages)
        )
        return

    success = await commission_service.advance_stage(
        artist_id, guild_id, commission.id, new_stage, message.author.id
    )

    if success:
        status = format_commission_status({"stage": new_stage, "payment_status": commission.payment_status})
        await message.reply(f" Commission advanced to **{new_stage}**\n{status}")
    else:
        await message.reply(" Failed to advance commission.")


async def _handle_list(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission list [status]' command."""
    artist_id = message.author.id
    guild_id = message.guild.id

    commissions = await commission_service.get_active_commissions(artist_id, guild_id)

    if not commissions:
        await message.reply(" You have no active commissions.")
        return

    # Build embed
    embed = discord.Embed(
        title=f"Active Commissions ({len(commissions)})",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for commission in commissions[:10]:  # Limit to 10
        client = await message.guild.fetch_member(commission.client_id)
        client_name = client.display_name if client else f"User {commission.client_id}"

        status = format_commission_status({
            "stage": commission.stage,
            "payment_status": commission.payment_status
        })

        value = f"**Client:** {client_name}\n**Status:** {status}"
        if commission.deadline:
            value += f"\n**Deadline:** {commission.deadline[:10]}"

        embed.add_field(
            name=f"`{commission.id[:8]}` - ${commission.price:.2f}",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_status(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'commission status [@user]' command."""
    target_user = message.author
    if len(parts) >= 3 and message.mentions:
        target_user = message.mentions[0]

    guild_id = message.guild.id

    # Get commission stats
    stats = await commission_service.get_commission_stats(target_user.id, guild_id)

    embed = discord.Embed(
        title=f"{target_user.display_name}'s Commission Status",
        color=discord.Color.green() if stats["slots_available"] > 0 else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    status_text = " OPEN" if stats["slots_available"] > 0 else " CLOSED"
    embed.add_field(
        name="Status",
        value=status_text,
        inline=True,
    )

    embed.add_field(
        name="Slots",
        value=f"{stats['slots_available']}/{stats['slots_total']} available",
        inline=True,
    )

    embed.add_field(
        name="Queue",
        value=f"{stats['active_count']} active",
        inline=True,
    )

    embed.add_field(
        name="Waitlist",
        value=f"{stats['waitlist_count']} waiting",
        inline=True,
    )

    embed.add_field(
        name="Completed",
        value=f"{stats['completed_count']} total",
        inline=True,
    )

    await message.reply(embed=embed)


async def _handle_waitlist(message: discord.Message, parts: list[str]) -> None:
    """Handle waitlist commands."""
    if len(parts) < 3:
        # Show waitlist
        await _handle_waitlist_view(message)
        return

    args = parts[2].split(maxsplit=1)
    action = args[0].lower()

    if action == "add":
        await _handle_waitlist_add(message, args)
    elif action == "remove":
        await _handle_waitlist_remove(message, args)
    else:
        await _handle_waitlist_view(message)


async def _handle_waitlist_view(message: discord.Message) -> None:
    """View waitlist entries."""
    artist_id = message.author.id
    guild_id = message.guild.id

    entries = await commission_service.get_waitlist(artist_id, guild_id)

    if not entries:
        await message.reply(" Your waitlist is empty.")
        return

    embed = discord.Embed(
        title=f"Waitlist ({len(entries)})",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )

    for entry in entries[:10]:  # Limit to 10
        client = await message.guild.fetch_member(entry.client_id)
        client_name = client.display_name if client else f"User {entry.client_id}"

        value = f"**Position:** {entry.position}"
        if entry.notes:
            value += f"\n**Notes:** {entry.notes[:100]}"

        embed.add_field(
            name=f"{client_name}",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_waitlist_add(message: discord.Message, args: list[str]) -> None:
    """Add client to waitlist."""
    if not message.mentions:
        await message.reply(" Please mention a client to add to the waitlist.")
        return

    client = message.mentions[0]
    notes = args[1] if len(args) > 1 else ""

    artist_id = message.author.id
    guild_id = message.guild.id

    entry = await commission_service.add_to_waitlist(artist_id, client.id, guild_id, notes)

    await message.reply(
        f" Added {client.mention} to waitlist at position **{entry.position}**"
    )


async def _handle_waitlist_remove(message: discord.Message, args: list[str]) -> None:
    """Remove entry from waitlist."""
    if len(args) < 2:
        await message.reply(" Usage: `commission waitlist remove <position>`")
        return

    try:
        position = int(args[1])
    except ValueError:
        await message.reply(" Position must be a number.")
        return

    artist_id = message.author.id
    guild_id = message.guild.id

    entries = await commission_service.get_waitlist(artist_id, guild_id)
    if position < 1 or position > len(entries):
        await message.reply(f" Invalid position. Waitlist has {len(entries)} entries.")
        return

    entry = entries[position - 1]
    removed = await commission_service.remove_from_waitlist(artist_id, guild_id, entry.id)

    if removed:
        await message.reply(f" Removed entry from position **{position}**")
    else:
        await message.reply(" Failed to remove entry.")


async def _handle_slots(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission slots <count>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission slots <count>`")
        return

    try:
        count = int(parts[2])
    except ValueError:
        await message.reply(" Slot count must be a number.")
        return

    if count < 1 or count > 50:
        await message.reply(" Slot count must be between 1 and 50.")
        return

    artist_id = message.author.id
    guild_id = message.guild.id

    await commission_service.update_slots(artist_id, guild_id, count)

    await message.reply(f" Total commission slots set to **{count}**")


async def _handle_autoclose(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission autoclose <on|off>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission autoclose <on|off>`")
        return

    enabled = parts[2].lower() in ["on", "enable", "true", "yes"]

    artist_id = message.author.id
    guild_id = message.guild.id

    await commission_service.set_auto_close(artist_id, guild_id, enabled)

    status = "enabled" if enabled else "disabled"
    await message.reply(f" Auto-close **{status}**")


async def _handle_stages(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission stages set <stage1, stage2, ...>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission stages set <stage1, stage2, ...>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2 or args[0].lower() != "set":
        await message.reply(" Usage: `commission stages set <stage1, stage2, ...>`")
        return

    stages_str = args[1]
    stages = [s.strip() for s in stages_str.split(",")]

    if len(stages) < 3:
        await message.reply(" You must have at least 3 stages.")
        return

    artist_id = message.author.id
    guild_id = message.guild.id

    await commission_service.set_custom_stages(artist_id, guild_id, stages)

    await message.reply(
        f" Custom stages set:\n" + "\n".join(f"• {s}" for s in stages)
    )


async def _handle_deadline(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission deadline <id> <date>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission deadline <id> <date>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `commission deadline <id> <date>`")
        return

    commission_id = args[0]
    deadline_str = args[1]

    deadline_dt = parse_deadline(deadline_str)
    if not deadline_dt:
        await message.reply(
            " Invalid date format. Try:\n"
            "• ISO format: `2026-03-15`\n"
            "• Relative: `3d`, `2w`, `1mo`"
        )
        return

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission by partial ID
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    success = await commission_service.update_commission(
        artist_id, guild_id, commission.id, {"deadline": dt_to_iso(deadline_dt)}
    )

    if success:
        await message.reply(f" Deadline set to **{deadline_dt.strftime('%Y-%m-%d')}**")
    else:
        await message.reply(" Failed to set deadline.")


async def _handle_tag(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission tag <id> <tags...>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission tag <id> <tags...>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `commission tag <id> <tags...>`")
        return

    commission_id = args[0]
    tags_str = args[1]
    tags = [t.strip() for t in tags_str.split(",")]

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    success = await commission_service.update_commission(
        artist_id, guild_id, commission.id, {"tags": tags}
    )

    if success:
        await message.reply(f" Tags set: {', '.join(tags)}")
    else:
        await message.reply(" Failed to set tags.")


async def _handle_revision(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission revision <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission revision <id>`")
        return

    commission_id = parts[2]

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    success = await commission_service.add_revision(artist_id, guild_id, commission.id)

    if success:
        new_used = commission.revisions_used + 1
        await message.reply(
            f" Revision logged: **{new_used}/{commission.revisions_limit}** revisions used"
        )
    else:
        await message.reply(
            f" Revision limit reached! ({commission.revisions_limit}/{commission.revisions_limit})"
        )


async def _handle_blacklist(message: discord.Message, parts: list[str]) -> None:
    """Handle blacklist commands."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission blacklist <add|remove|list>`")
        return

    args = parts[2].split(maxsplit=1)
    action = args[0].lower()

    if action == "add":
        await _handle_blacklist_add(message, args)
    elif action == "remove":
        await _handle_blacklist_remove(message)
    elif action == "list":
        await _handle_blacklist_list(message)
    else:
        await message.reply(" Usage: `commission blacklist <add|remove|list>`")


async def _handle_blacklist_add(message: discord.Message, args: list[str]) -> None:
    """Add user to blacklist."""
    if not message.mentions:
        await message.reply(" Please mention a user to blacklist.")
        return

    user = message.mentions[0]
    reason = args[1] if len(args) > 1 else "No reason provided"

    artist_id = message.author.id
    guild_id = message.guild.id

    await commission_service.add_to_blacklist(artist_id, guild_id, user.id, reason)

    await message.reply(f" Added {user.mention} to your blacklist.")


async def _handle_blacklist_remove(message: discord.Message) -> None:
    """Remove user from blacklist."""
    if not message.mentions:
        await message.reply(" Please mention a user to remove from blacklist.")
        return

    user = message.mentions[0]

    artist_id = message.author.id
    guild_id = message.guild.id

    success = await commission_service.remove_from_blacklist(artist_id, guild_id, user.id)

    if success:
        await message.reply(f" Removed {user.mention} from your blacklist.")
    else:
        await message.reply(f" {user.mention} is not in your blacklist.")


async def _handle_blacklist_list(message: discord.Message) -> None:
    """List blacklisted users."""
    artist_id = message.author.id
    guild_id = message.guild.id

    blacklist = await commission_service.get_blacklist(artist_id, guild_id)

    if not blacklist:
        await message.reply(" Your blacklist is empty.")
        return

    embed = discord.Embed(
        title="Blacklisted Clients",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    for entry in blacklist[:10]:  # Limit to 10
        user_id = entry.get("user_id")
        reason = entry.get("reason", "No reason")

        embed.add_field(
            name=f"User {user_id}",
            value=f"**Reason:** {reason}",
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_invoice(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'commission invoice <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission invoice <id>`")
        return

    commission_id = parts[2]

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    # Get profile for artist info
    profile = await get_profile(artist_id, guild_id)

    try:
        embed = discord.Embed(
            title=f"Invoice • {commission.id[:8]}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Artist", value=f"<@{commission.artist_id}>", inline=True)
        embed.add_field(name="Client", value=f"<@{commission.client_id}>", inline=True)
        embed.add_field(name="Stage", value=commission.stage or "Unknown", inline=True)

        price_str = f"{commission.price:.2f} {commission.currency}" if commission.currency else f"{commission.price:.2f}"
        embed.add_field(name="Price", value=price_str, inline=True)
        embed.add_field(name="Payment", value=commission.payment_status or "pending", inline=True)
        embed.add_field(
            name="Revisions",
            value=f"{commission.revisions_used}/{commission.revisions_limit}",
            inline=True,
        )

        if commission.deadline:
            embed.add_field(name="Deadline", value=commission.deadline, inline=True)

        if commission.tags:
            tags = ", ".join(f"`{t}`" for t in commission.tags[:20])
            embed.add_field(name="Tags", value=tags, inline=False)

        notes = (commission.notes or "").strip()
        if notes:
            embed.description = notes[:3500] + ("…" if len(notes) > 3500 else "")

        contact = (profile or {}).get("contact_preference")
        if contact:
            embed.set_footer(text=f"Contact preference: {contact}")

        await message.reply(embed=embed)
    except Exception as e:
        logger.error("Failed to build invoice embed: %s", e)
        await message.reply(" Failed to show invoice.")


async def _handle_contract(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'commission contract <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission contract <id>`")
        return

    commission_id = parts[2]

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    # Get TOS URL
    store = commission_service._get_store(guild_id, artist_id)
    tos_url = await store.get_tos_url()

    terms = {
        "tos_url": tos_url,
        "revisions_limit": commission.revisions_limit,
        "payment_terms": "50% upfront, 50% on completion",
    }

    try:
        embed = discord.Embed(
            title=f"Contract Terms • {commission.id[:8]}",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Artist", value=f"<@{commission.artist_id}>", inline=True)
        embed.add_field(name="Client", value=f"<@{commission.client_id}>", inline=True)
        embed.add_field(name="Revisions", value=str(terms.get("revisions_limit", commission.revisions_limit)), inline=True)
        embed.add_field(name="Payment Terms", value=str(terms.get("payment_terms", "N/A")), inline=False)

        if tos_url:
            embed.add_field(name="TOS", value=tos_url, inline=False)
        else:
            embed.add_field(name="TOS", value="Not set. (Artist can set a TOS URL.)", inline=False)

        embed.description = (
            "This is a lightweight, in-chat contract summary. "
            "Use your server’s normal commissioning process for any additional terms."
        )
        await message.reply(embed=embed)
    except Exception as e:
        logger.error("Failed to build contract embed: %s", e)
        await message.reply(" Failed to show contract terms.")


async def _handle_tos(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission tos <set|clear|view>' commands."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission tos <set|clear|view> ...`")
        return

    args = parts[2].split(maxsplit=1)
    action = args[0].lower() if args else ""
    artist_id = message.author.id
    guild_id = message.guild.id

    store = commission_service._get_store(guild_id, artist_id)
    await store.initialize()

    if action == "view":
        url = await store.get_tos_url()
        if url:
            await message.reply(f" TOS URL: {url}")
        else:
            await message.reply(" No TOS URL is set.")
        return

    if action == "clear":
        await store.set_tos_url(None)
        await message.reply(" TOS URL cleared.")
        return

    if action == "set":
        if len(args) < 2:
            await message.reply(" Usage: `commission tos set <url>`")
            return
        url = args[1].strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            await message.reply(" Please provide a valid http(s) URL.")
            return
        await store.set_tos_url(url)
        await message.reply(" TOS URL set.")
        return

    await message.reply(" Usage: `commission tos <set|clear|view> ...`")


async def _find_commission_by_prefix(
    *,
    artist_id: int,
    guild_id: int,
    prefix: str,
    include_history: bool,
):
    prefix = (prefix or "").strip()
    if not prefix:
        return None, False
    active = await commission_service.get_active_commissions(artist_id, guild_id)
    match = [c for c in active if c.id.startswith(prefix)]
    if match:
        return match[0], False
    if not include_history:
        return None, False
    history = await commission_service.get_commission_history(artist_id, guild_id, limit=None)
    match_h = [c for c in history if c.id.startswith(prefix)]
    if match_h:
        return match_h[0], True
    return None, False


async def _handle_archive(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission archive <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission archive <id>`")
        return

    commission_id = parts[2].strip()
    artist_id = message.author.id
    guild_id = message.guild.id

    commission, in_history = await _find_commission_by_prefix(
        artist_id=artist_id,
        guild_id=guild_id,
        prefix=commission_id,
        include_history=False,
    )
    if not commission:
        await message.reply(f" No active commission found with ID starting with `{commission_id}`")
        return

    store = commission_service._get_store(guild_id, artist_id)
    removed = await store.remove_commission(commission.id, archive=True)
    if not removed:
        await message.reply(" Failed to archive commission.")
        return
    await message.reply(f" Commission archived: `{commission.id[:8]}`")


async def _handle_export(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission export <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission export <id>`")
        return

    commission_id = parts[2].strip()
    artist_id = message.author.id
    guild_id = message.guild.id

    commission, in_history = await _find_commission_by_prefix(
        artist_id=artist_id,
        guild_id=guild_id,
        prefix=commission_id,
        include_history=True,
    )
    if not commission:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    payload = commission.to_dict()
    payload["_export"] = {"source": "history" if in_history else "active"}
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    file = discord.File(fp=io.BytesIO(data), filename=f"commission_{commission.id[:8]}.json")
    await message.reply(" Exported commission JSON:", file=file)


async def _handle_payment(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission payment confirm <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission payment confirm <id>`")
        return

    args = parts[2].split()
    if len(args) < 2 or args[0].lower() != "confirm":
        await message.reply(" Usage: `commission payment confirm <id>`")
        return

    commission_id = args[1]

    artist_id = message.author.id
    guild_id = message.guild.id

    # Find commission
    commissions = await commission_service.get_active_commissions(artist_id, guild_id)
    matching = [c for c in commissions if c.id.startswith(commission_id)]

    if not matching:
        await message.reply(f" No commission found with ID starting with `{commission_id}`")
        return

    commission = matching[0]

    success = await commission_service.confirm_payment(
        artist_id, guild_id, commission.id, message.author.id
    )

    if success:
        await message.reply(" Payment confirmed! ")
    else:
        await message.reply(" Failed to confirm payment.")


async def _handle_summary(message: discord.Message) -> None:
    """Handle 'commission summary' command."""
    artist_id = message.author.id
    guild_id = message.guild.id

    stats = await commission_service.get_commission_stats(artist_id, guild_id)

    embed = discord.Embed(
        title="Commission Summary",
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Active",
        value=f"{stats['active_count']} commissions",
        inline=True,
    )

    embed.add_field(
        name="Completed",
        value=f"{stats['completed_count']} total",
        inline=True,
    )

    embed.add_field(
        name="Earnings",
        value=f"${stats['total_earnings']:.2f}",
        inline=True,
    )

    embed.add_field(
        name="Slots",
        value=f"{stats['slots_available']}/{stats['slots_total']} available",
        inline=True,
    )

    embed.add_field(
        name="Waitlist",
        value=f"{stats['waitlist_count']} waiting",
        inline=True,
    )

    await message.reply(embed=embed)


async def _handle_quickadd(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'commission quickadd @client <price> <type> [deadline]' command."""
    if not message.mentions:
        await message.reply(" Please mention a client.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `commission quickadd @client <price> <type> [deadline]`")
        return

    client = message.mentions[0]
    args = parts[2].split()

    if len(args) < 2:
        await message.reply(" Usage: `commission quickadd @client <price> <type> [deadline]`")
        return

    try:
        price = float(args[0].replace("$", ""))
    except ValueError:
        await message.reply(" Invalid price format.")
        return

    commission_type = args[1]
    deadline_str = args[2] if len(args) > 2 else None

    deadline_dt = None
    if deadline_str:
        deadline_dt = parse_deadline(deadline_str)

    artist_id = message.author.id
    guild_id = message.guild.id

    details = {
        "price": price,
        "tags": [commission_type],
        "deadline": dt_to_iso(deadline_dt) if deadline_dt else None,
    }

    commission = await commission_service.create_commission(
        artist_id=artist_id,
        client_id=client.id,
        guild_id=guild_id,
        details=details,
    )

    await message.reply(
        f" Quick commission created!\n"
        f"**ID:** `{commission.id[:8]}`\n"
        f"**Client:** {client.mention}\n"
        f"**Price:** ${price:.2f}\n"
        f"**Type:** {commission_type}"
    )


async def _handle_search(message: discord.Message, parts: list[str]) -> None:
    """Handle 'commission search <query>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `commission search <query>`")
        return

    query = parts[2].lower()

    artist_id = message.author.id
    guild_id = message.guild.id

    commissions = await commission_service.get_active_commissions(artist_id, guild_id)

    # Filter by tags or client ID
    matching = []
    for commission in commissions:
        if query in " ".join(commission.tags).lower():
            matching.append(commission)
        elif query in str(commission.client_id):
            matching.append(commission)

    if not matching:
        await message.reply(f" No commissions found matching '{query}'")
        return

    embed = discord.Embed(
        title=f"Search Results ({len(matching)})",
        description=f"Query: `{query}`",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for commission in matching[:10]:  # Limit to 10
        status = format_commission_status({
            "stage": commission.stage,
            "payment_status": commission.payment_status
        })

        value = f"**Status:** {status}\n**Tags:** {', '.join(commission.tags)}"

        embed.add_field(
            name=f"`{commission.id[:8]}` - ${commission.price:.2f}",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_help(message: discord.Message) -> None:
    """Handle 'commission help' command."""
    help_text = help_system.get_module_help("Commissions")
    if help_text:
        await message.reply(embed=help_text)
    else:
        await message.reply(" Help information not available.")
