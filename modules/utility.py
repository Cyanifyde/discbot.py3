"""
Utility module - bookmarks, AFK, personal notes, aliases, and data export.

Provides utility commands for user convenience and productivity.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from core.utility_storage import UtilityStore, GuildUtilityStore
from core.types import Bookmark
from core.utils import utcnow, dt_to_iso, parse_duration_extended

logger = logging.getLogger("discbot.utility")

MODULE_NAME = "utility"


def setup_utility() -> None:
    """Register help information for the utility module."""
    help_system.register_module(
        name="Utility",
        description="Utility commands for bookmarks, AFK, notes, and more.",
        help_command="utility help",
        commands=[
            ("bookmark [message_link] [note]", "Bookmark a message"),
            ("bookmark list", "List bookmarks"),
            ("bookmark remove <id>", "Remove bookmark"),
            ("bookmark delay <message_link> <time>", "Delayed delivery bookmark"),
            ("afk [message]", "Set AFK status"),
            ("afk off", "Clear AFK status"),
            ("note add <content>", "Add personal note"),
            ("notes", "List personal notes"),
            ("note remove <id>", "Remove note"),
            ("alias add <shortcut> <full_command>", "Add command alias (mod)"),
            ("alias remove <shortcut>", "Remove alias (mod)"),
            ("alias list", "List aliases"),
            ("export", "Export all user data as JSON"),
        ],
    )


async def handle_utility_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle utility-related commands.

    Returns True if command was handled, False otherwise.
    """
    if not message.guild:
        return False

    # Check if module is enabled
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False

    content = message.content.strip()
    parts = content.split(maxsplit=2)

    if len(parts) < 1:
        return False

    command = parts[0].lower()

    # Route to handlers
    if command == "bookmark":
        await _handle_bookmark(message, parts)
        return True
    elif command == "afk":
        await _handle_afk(message, parts)
        return True
    elif command == "note":
        await _handle_note(message, parts)
        return True
    elif command == "notes":
        await _handle_notes_list(message)
        return True
    elif command == "alias":
        await _handle_alias(message, parts)
        return True
    elif command == "export":
        await _handle_export(message)
        return True

    return False


# ─── Bookmark Handlers ────────────────────────────────────────────────────────


async def _handle_bookmark(message: discord.Message, parts: list[str]) -> None:
    """Handle bookmark commands."""
    if len(parts) < 2:
        # No subcommand, bookmark current message
        await _handle_bookmark_add(message, None, None)
        return

    subcommand = parts[1].lower()

    if subcommand == "list":
        await _handle_bookmark_list(message)
    elif subcommand == "remove":
        await _handle_bookmark_remove(message, parts)
    elif subcommand == "delay":
        await _handle_bookmark_delay(message, parts)
    else:
        # Assume it's a message link
        note = parts[2] if len(parts) > 2 else None
        await _handle_bookmark_add(message, parts[1], note)


async def _handle_bookmark_add(
    message: discord.Message,
    message_link: Optional[str],
    note: Optional[str],
) -> None:
    """Add a bookmark."""
    store = UtilityStore(message.author.id)
    await store.initialize()

    # If no link provided, bookmark the message being replied to
    if not message_link and message.reference:
        ref_msg = await message.channel.fetch_message(message.reference.message_id)
        message_link = ref_msg.jump_url

    if not message_link:
        await message.reply(" Please provide a message link or reply to a message to bookmark.")
        return

    bookmark = Bookmark(
        id=str(uuid.uuid4()),
        user_id=message.author.id,
        guild_id=message.guild.id,
        channel_id=message.channel.id,
        message_id=0,  # Will be parsed from link
        message_link=message_link,
        note=note or "",
        created_at=dt_to_iso(utcnow()),
    )

    await store.add_bookmark(bookmark)

    await message.reply(f" Bookmark saved! ID: `{bookmark.id[:8]}`")


async def _handle_bookmark_list(message: discord.Message) -> None:
    """List bookmarks."""
    store = UtilityStore(message.author.id)
    await store.initialize()

    bookmarks = await store.get_bookmarks()

    if not bookmarks:
        await message.reply(" You have no bookmarks.")
        return

    embed = discord.Embed(
        title="Your Bookmarks",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for bookmark in bookmarks[:10]:  # Limit to 10
        value = f"[Jump to message]({bookmark.message_link})"
        if bookmark.note:
            value += f"\n**Note:** {bookmark.note[:100]}"

        embed.add_field(
            name=f"`{bookmark.id[:8]}`",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_bookmark_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove a bookmark."""
    if len(parts) < 3:
        await message.reply(" Usage: `bookmark remove <id>`")
        return

    bookmark_id = parts[2]

    store = UtilityStore(message.author.id)
    await store.initialize()

    # Find bookmark by partial ID
    bookmarks = await store.get_bookmarks()
    matching = [b for b in bookmarks if b.id.startswith(bookmark_id)]

    if not matching:
        await message.reply(f" No bookmark found with ID starting with `{bookmark_id}`")
        return

    bookmark = matching[0]
    success = await store.remove_bookmark(bookmark.id)

    if success:
        await message.reply(" Bookmark removed")
    else:
        await message.reply(" Failed to remove bookmark")


async def _handle_bookmark_delay(message: discord.Message, parts: list[str]) -> None:
    """Schedule a delayed bookmark delivery."""
    if len(parts) < 4:
        await message.reply(" Usage: `bookmark delay <message_link> <time>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `bookmark delay <message_link> <time>`")
        return

    message_link = args[0]
    time_str = args[1]

    # Parse duration
    duration = parse_duration_extended(time_str)
    if not duration:
        await message.reply(" Invalid time format. Try: `3d`, `2w`, `1mo`")
        return

    deliver_at = utcnow() + duration

    store = UtilityStore(message.author.id)
    await store.initialize()

    bookmark = Bookmark(
        id=str(uuid.uuid4()),
        user_id=message.author.id,
        guild_id=message.guild.id,
        channel_id=message.channel.id,
        message_id=0,
        message_link=message_link,
        created_at=dt_to_iso(utcnow()),
        deliver_at=dt_to_iso(deliver_at),
    )

    await store.add_bookmark(bookmark)

    await message.reply(
        f" Bookmark scheduled for delivery on **{deliver_at.strftime('%Y-%m-%d %H:%M')}**"
    )


# ─── AFK Handlers ─────────────────────────────────────────────────────────────


async def _handle_afk(message: discord.Message, parts: list[str]) -> None:
    """Handle AFK commands."""
    store = UtilityStore(message.author.id)
    await store.initialize()

    if len(parts) > 1 and parts[1].lower() == "off":
        # Clear AFK
        result = await store.clear_afk()

        if result["was_afk"]:
            mentions = result["mentions"]
            if mentions:
                mention_str = "\n".join(
                    f"• {m.get('author')}: {m.get('content', '')[:50]}"
                    for m in mentions[:10]
                )
                await message.reply(
                    f" Welcome back! You were mentioned **{len(mentions)}** times:\n{mention_str}"
                )
            else:
                await message.reply(" Welcome back!")
        else:
            await message.reply(" You weren't AFK")
        return

    # Set AFK
    afk_message = parts[1] if len(parts) > 1 else None
    await store.set_afk(afk_message)

    status_text = f" AFK set"
    if afk_message:
        status_text += f": {afk_message}"

    await message.reply(status_text)


# ─── Note Handlers ────────────────────────────────────────────────────────────


async def _handle_note(message: discord.Message, parts: list[str]) -> None:
    """Handle note commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `note <add|remove>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "add":
        await _handle_note_add(message, parts)
    elif subcommand == "remove":
        await _handle_note_remove(message, parts)
    else:
        await message.reply(" Usage: `note <add|remove>`")


async def _handle_note_add(message: discord.Message, parts: list[str]) -> None:
    """Add a personal note."""
    if len(parts) < 3:
        await message.reply(" Usage: `note add <content>`")
        return

    content = parts[2]

    store = UtilityStore(message.author.id)
    await store.initialize()

    note = await store.add_note(content)

    await message.reply(f" Note saved! ID: `{note['id'][:8]}`")


async def _handle_notes_list(message: discord.Message) -> None:
    """List personal notes."""
    store = UtilityStore(message.author.id)
    await store.initialize()

    notes = await store.get_notes()

    if not notes:
        await message.reply(" You have no notes.")
        return

    embed = discord.Embed(
        title="Your Notes",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )

    for note in notes[:10]:  # Limit to 10
        content = note.get("content", "")[:100]

        embed.add_field(
            name=f"`{note['id'][:8]}` - {note['created_at'][:10]}",
            value=content,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_note_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove a note."""
    if len(parts) < 3:
        await message.reply(" Usage: `note remove <id>`")
        return

    note_id = parts[2]

    store = UtilityStore(message.author.id)
    await store.initialize()

    # Find note by partial ID
    notes = await store.get_notes()
    matching = [n for n in notes if n["id"].startswith(note_id)]

    if not matching:
        await message.reply(f" No note found with ID starting with `{note_id}`")
        return

    note = matching[0]
    success = await store.remove_note(note["id"])

    if success:
        await message.reply(" Note removed")
    else:
        await message.reply(" Failed to remove note")


# ─── Alias Handlers ───────────────────────────────────────────────────────────


async def _handle_alias(message: discord.Message, parts: list[str]) -> None:
    """Handle alias commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `alias <add|remove|list>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "add":
        await _handle_alias_add(message, parts)
    elif subcommand == "remove":
        await _handle_alias_remove(message, parts)
    elif subcommand == "list":
        await _handle_alias_list(message)
    else:
        await message.reply(" Usage: `alias <add|remove|list>`")


async def _handle_alias_add(message: discord.Message, parts: list[str]) -> None:
    """Add a command alias."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to add aliases.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `alias add <shortcut> <full_command>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `alias add <shortcut> <full_command>`")
        return

    shortcut = args[0]
    full_command = args[1]

    store = GuildUtilityStore(message.guild.id)
    await store.initialize()

    await store.add_alias(shortcut, full_command)

    await message.reply(f" Alias added: `{shortcut}` → `{full_command}`")


async def _handle_alias_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove an alias."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to remove aliases.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `alias remove <shortcut>`")
        return

    shortcut = parts[2]

    store = GuildUtilityStore(message.guild.id)
    await store.initialize()

    success = await store.remove_alias(shortcut)

    if success:
        await message.reply(f" Alias `{shortcut}` removed")
    else:
        await message.reply(f" Alias `{shortcut}` not found")


async def _handle_alias_list(message: discord.Message) -> None:
    """List all aliases."""
    store = GuildUtilityStore(message.guild.id)
    await store.initialize()

    aliases = await store.get_all_aliases()

    if not aliases:
        await message.reply(" No aliases configured for this server.")
        return

    embed = discord.Embed(
        title="Command Aliases",
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow(),
    )

    for shortcut, full_command in list(aliases.items())[:20]:  # Limit to 20
        embed.add_field(
            name=shortcut,
            value=full_command,
            inline=False,
        )

    await message.reply(embed=embed)


# ─── Export Handler ───────────────────────────────────────────────────────────


async def _handle_export(message: discord.Message) -> None:
    """Export all user data as JSON."""
    from classes.profile import get_profile
    from services.portfolio_service import portfolio_service
    from services.commission_service import commission_service

    user_id = message.author.id
    guild_id = message.guild.id

    # Collect all user data
    export_data = {
        "user_id": user_id,
        "exported_at": dt_to_iso(utcnow()),
        "data": {}
    }

    # Profile
    try:
        profile = await get_profile(user_id, guild_id)
        export_data["data"]["profile"] = profile
    except Exception:
        pass

    # Portfolio
    try:
        portfolio = await portfolio_service.get_portfolio(user_id)
        export_data["data"]["portfolio"] = [e.to_dict() for e in portfolio]
    except Exception:
        pass

    # Commissions
    try:
        commissions = await commission_service.get_active_commissions(user_id, guild_id)
        export_data["data"]["commissions"] = [c.to_dict() for c in commissions]
    except Exception:
        pass

    # Bookmarks
    try:
        store = UtilityStore(user_id)
        await store.initialize()
        bookmarks = await store.get_bookmarks()
        export_data["data"]["bookmarks"] = [b.to_dict() for b in bookmarks]
    except Exception:
        pass

    # Notes
    try:
        notes = await store.get_notes()
        export_data["data"]["notes"] = notes
    except Exception:
        pass

    # Convert to JSON
    json_data = json.dumps(export_data, indent=2)

    # Send as file
    file = discord.File(
        fp=json_data.encode(),
        filename=f"user_data_{user_id}.json"
    )

    await message.reply(
        " Your data export is ready!",
        file=file
    )
