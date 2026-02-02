"""
Utility module - bookmarks, AFK, personal notes, aliases, and data export.

Provides utility commands for user convenience and productivity.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from core.utility_storage import UtilityStore, GuildUtilityStore
from core.types import Bookmark
from core.utils import utcnow, dt_to_iso, iso_to_dt, parse_duration_extended

logger = logging.getLogger("discbot.utility")

MODULE_NAME = "utility"


def setup_utility() -> None:
    """Register help information for the utility module."""
    help_system.register_module(
        name="Utility",
        description="Utility commands for bookmarks, AFK, notes, and more.",
        help_command="utility help",
        commands=[
            ("bookmark help", "Bookmark commands"),
            ("afk help", "AFK status commands"),
            ("note help", "Personal notes commands"),
            ("alias help", "Command alias commands (mod only)"),
            ("export help", "User data export command"),
            ("utility help", "Show this help message"),
        ],
    )

    help_system.register_module(
        name="Bookmarks",
        description="Bookmark messages, optionally with notes and delayed delivery.",
        help_command="bookmark help",
        commands=[
            ("bookmark [message_link] [note]", "Bookmark a message"),
            ("bookmark list", "List bookmarks"),
            ("bookmark view <id>", "View a bookmark"),
            ("bookmark remove <id>", "Remove bookmark"),
            ("bookmark clear", "Remove all your bookmarks"),
            ("bookmark delay <message_link> <time>", "Delayed delivery bookmark"),
            ("bookmark emoji set <emoji> [dm|channel]", "Set reaction emoji for instant bookmark"),
            ("bookmark emoji delay <emoji> <time> [dm|channel]", "Set reaction emoji for delayed bookmark"),
            ("bookmark emoji remove <emoji>", "Remove reaction emoji bookmark"),
            ("bookmark emoji list", "List reaction emoji bookmarks"),
            ("bookmark help", "Show this help message"),
        ],
        group="Utility",
        hidden=True,
    )

    help_system.register_module(
        name="AFK",
        description="Set an AFK message and automatically clear it when you speak.",
        help_command="afk help",
        commands=[
            ("afk [message]", "Set AFK status"),
            ("afk off", "Clear AFK status"),
            ("afk status [@user]", "Show AFK status"),
            ("afk help", "Show this help message"),
        ],
        group="Utility",
        hidden=True,
    )

    help_system.register_module(
        name="Notes",
        description="Personal notes stored by the bot (private to you).",
        help_command="note help",
        commands=[
            ("note add <content>", "Add personal note"),
            ("notes", "List personal notes"),
            ("note view <id>", "View a note"),
            ("note edit <id> <text>", "Edit a note"),
            ("note remove <id>", "Remove note"),
            ("note help", "Show this help message"),
        ],
        group="Utility",
        hidden=True,
    )

    help_system.register_module(
        name="Aliases",
        description="Create server-specific command aliases (mods).",
        help_command="alias help",
        commands=[
            ("alias add <shortcut> <full_command>", "Add command alias (mod)"),
            ("alias remove <shortcut>", "Remove alias (mod)"),
            ("alias list", "List aliases"),
            ("alias help", "Show this help message"),
        ],
        group="Utility",
        hidden=True,
    )

    help_system.register_module(
        name="Export",
        description="Export all your stored user data as JSON.",
        help_command="export help",
        commands=[
            ("export", "Export all user data as JSON"),
            ("export help", "Show this help message"),
        ],
        group="Utility",
        hidden=True,
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

    # Per-subcommand help
    if len(parts) >= 2 and parts[1].lower() == "help":
        target_map = {
            "bookmark": "Bookmarks",
            "afk": "AFK",
            "note": "Notes",
            "alias": "Aliases",
            "export": "Export",
        }
        if command in target_map:
            embed = help_system.get_module_help(target_map[command])
            if embed:
                await message.reply(embed=embed)
            else:
                await message.reply(" Help information not available.")
            return True

    # Route to handlers
    if command == "utility":
        if len(parts) >= 2 and parts[1].lower() == "help":
            await _handle_utility_help(message)
            return True
        return False
    if command == "bookmark":
        await _handle_bookmark(message, parts, bot)
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


async def _handle_utility_help(message: discord.Message) -> None:
    """Handle 'utility help' command."""
    embed = help_system.get_module_help("Utility")
    if embed:
        await message.reply(embed=embed)
    else:
        await message.reply(" Help information not available.")


# ─── Bookmark Handlers ────────────────────────────────────────────────────────


async def _handle_bookmark(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle bookmark commands."""
    if len(parts) < 2:
        # No subcommand, bookmark current message
        await _handle_bookmark_add(message, None, None)
        return

    subcommand = parts[1].lower()

    if subcommand == "list":
        await _handle_bookmark_list(message)
    elif subcommand == "view":
        await _handle_bookmark_view(message, parts)
    elif subcommand == "remove":
        await _handle_bookmark_remove(message, parts)
    elif subcommand == "clear":
        await _handle_bookmark_clear(message)
    elif subcommand == "delay":
        await _handle_bookmark_delay(message, parts, bot)
    elif subcommand == "emoji":
        await _handle_bookmark_emoji(message, parts)
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

async def _handle_bookmark_view(message: discord.Message, parts: list[str]) -> None:
    """View a bookmark."""
    if len(parts) < 3:
        await message.reply(" Usage: `bookmark view <id>`")
        return
    bookmark_id = parts[2].strip()
    store = UtilityStore(message.author.id)
    await store.initialize()
    bookmarks = await store.get_bookmarks()
    matching = [b for b in bookmarks if b.id.startswith(bookmark_id)]
    if not matching:
        await message.reply(f" No bookmark found with ID starting with `{bookmark_id}`")
        return
    b = matching[0]
    link = f"https://discord.com/channels/{b.guild_id}/{b.channel_id}/{b.message_id}" if b.guild_id and b.channel_id and b.message_id else ""
    embed = discord.Embed(
        title=f"Bookmark `{b.id[:8]}`",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if b.note:
        embed.description = b.note[:3500]
    if link:
        embed.add_field(name="Message", value=link, inline=False)
    if b.deliver_at:
        embed.add_field(name="Deliver At", value=b.deliver_at, inline=True)
    embed.add_field(name="Delivered", value=str(bool(b.delivered)), inline=True)
    await message.reply(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def _handle_bookmark_clear(message: discord.Message) -> None:
    """Clear all bookmarks."""
    store = UtilityStore(message.author.id)
    await store.initialize()
    count = await store.clear_bookmarks()
    await message.reply(f" Cleared {count} bookmark(s).")


async def _handle_bookmark_delay(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
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
    _schedule_delayed_bookmark(bot, bookmark, store=store)

    await message.reply(
        f" Bookmark scheduled for delivery on **{deliver_at.strftime('%Y-%m-%d %H:%M')}**"
    )


async def _handle_bookmark_emoji(message: discord.Message, parts: list[str]) -> None:
    """Handle bookmark emoji commands."""
    if len(parts) < 3:
        await message.reply(
            " Usage: `bookmark emoji <set|delay|remove|list> ...`"
        )
        return

    subparts = parts[2].split(maxsplit=1)
    action = subparts[0].lower()
    rest = subparts[1] if len(subparts) > 1 else ""

    if action == "list":
        await _handle_bookmark_emoji_list(message)
        return

    if action == "remove":
        if not rest:
            await message.reply(" Usage: `bookmark emoji remove <emoji>`")
            return
        await _handle_bookmark_emoji_remove(message, rest.strip())
        return

    if action == "set":
        if not rest:
            await message.reply(" Usage: `bookmark emoji set <emoji> [dm|channel]`")
            return
        await _handle_bookmark_emoji_set(message, rest.strip())
        return

    if action == "delay":
        if not rest:
            await message.reply(" Usage: `bookmark emoji delay <emoji> <time> [dm|channel]`")
            return
        await _handle_bookmark_emoji_delay(message, rest.strip())
        return

    await message.reply(" Usage: `bookmark emoji <set|delay|remove|list> ...`")


async def _handle_bookmark_emoji_set(message: discord.Message, args: str) -> None:
    """Set reaction emoji for instant bookmark."""
    parts = args.split()
    if not parts:
        await message.reply(" Usage: `bookmark emoji set <emoji> [dm|channel]`")
        return

    emoji_key = parts[0]
    method = parts[1].lower() if len(parts) > 1 else "dm"
    if method not in ("dm", "channel"):
        await message.reply(" Delivery method must be `dm` or `channel`.")
        return

    store = UtilityStore(message.author.id)
    await store.initialize()
    await store.set_emoji_setting(
        emoji_key,
        {"type": "instant", "method": method, "delay_seconds": None},
    )

    await message.reply(f" Emoji bookmark set for {emoji_key} ({method}).")


async def _handle_bookmark_emoji_delay(message: discord.Message, args: str) -> None:
    """Set reaction emoji for delayed bookmark."""
    parts = args.split(maxsplit=2)
    if len(parts) < 2:
        await message.reply(" Usage: `bookmark emoji delay <emoji> <time> [dm|channel]`")
        return

    emoji_key = parts[0]
    time_str = parts[1]
    method = parts[2].lower() if len(parts) > 2 else "dm"
    if method not in ("dm", "channel"):
        await message.reply(" Delivery method must be `dm` or `channel`.")
        return

    duration = parse_duration_extended(time_str)
    if not duration:
        await message.reply(" Invalid time format. Try: `3d`, `2w`, `1mo`")
        return

    store = UtilityStore(message.author.id)
    await store.initialize()
    await store.set_emoji_setting(
        emoji_key,
        {"type": "delay", "method": method, "delay_seconds": int(duration.total_seconds())},
    )

    await message.reply(
        f" Emoji bookmark delay set for {emoji_key} ({time_str}, {method})."
    )


async def _handle_bookmark_emoji_remove(message: discord.Message, emoji_key: str) -> None:
    """Remove reaction emoji bookmark."""
    store = UtilityStore(message.author.id)
    await store.initialize()
    removed = await store.remove_emoji_setting(emoji_key)
    if removed:
        await message.reply(f" Emoji bookmark removed for {emoji_key}.")
    else:
        await message.reply(f" No emoji bookmark found for {emoji_key}.")


async def _handle_bookmark_emoji_list(message: discord.Message) -> None:
    """List reaction emoji bookmark settings."""
    store = UtilityStore(message.author.id)
    await store.initialize()
    settings = await store.get_emoji_settings()
    if not settings:
        await message.reply(" No emoji bookmarks configured.")
        return

    lines = []
    for emoji_key, cfg in settings.items():
        cfg_type = cfg.get("type", "instant")
        method = cfg.get("method", "dm")
        delay = cfg.get("delay_seconds")
        if cfg_type == "delay" and delay:
            lines.append(f"{emoji_key} → delay {int(delay)}s ({method})")
        else:
            lines.append(f"{emoji_key} → instant ({method})")

    await message.reply(" Emoji bookmarks:\n" + "\n".join(lines))


async def handle_bookmark_reaction(
    payload: discord.RawReactionActionEvent,
    bot: discord.Client,
) -> None:
    """Handle reaction-based bookmarks."""
    if payload.guild_id is None or payload.user_id is None:
        return
    if bot.user and payload.user_id == bot.user.id:
        return
    if not await is_module_enabled(payload.guild_id, MODULE_NAME):
        return

    store = UtilityStore(payload.user_id)
    await store.initialize()
    settings = await store.get_emoji_settings()
    emoji_key = str(payload.emoji)
    config = settings.get(emoji_key)
    if not config:
        return

    method = config.get("method", "dm")
    delay_seconds = config.get("delay_seconds")
    now = utcnow()

    message_link = f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}"
    deliver_at = None
    if delay_seconds:
        deliver_at = dt_to_iso(now + timedelta(seconds=delay_seconds))

    bookmark = Bookmark(
        id=str(uuid.uuid4()),
        user_id=payload.user_id,
        guild_id=payload.guild_id,
        channel_id=payload.channel_id,
        message_id=payload.message_id,
        message_link=message_link,
        created_at=dt_to_iso(now),
        deliver_at=deliver_at,
        delivery_method=method,
        notify_channel_id=payload.channel_id if method == "channel" else None,
    )

    await store.add_bookmark(bookmark)

    if deliver_at:
        _schedule_delayed_bookmark(bot, bookmark, store=store)
        return

    delivered, permanent_fail = await _deliver_bookmark_now(bot, bookmark)
    if delivered:
        # If delivered to DMs, remove it from storage to avoid clutter.
        if bookmark.delivery_method == "dm":
            await store.remove_bookmark(bookmark.id)
        else:
            await store.mark_delivered(bookmark.id)
    elif permanent_fail:
        # If it can never be delivered (e.g., user blocks DMs), clean it up.
        await store.remove_bookmark(bookmark.id)


async def _deliver_bookmark_now(bot: discord.Client, bookmark: Bookmark) -> tuple[bool, bool]:
    """
    Deliver a bookmark based on delivery method.

    Returns:
        (delivered, permanent_fail)
    """
    message = await _try_fetch_bookmarked_message(bot, bookmark)
    text = _format_bookmark_delivery_text(bookmark, message)

    try:
        if bookmark.delivery_method == "channel":
            channel = bot.get_channel(bookmark.notify_channel_id or bookmark.channel_id)
            if channel and hasattr(channel, "send"):
                await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
                return True, False
            return False, True

        user = await bot.fetch_user(bookmark.user_id)
        if user:
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
            return True, False
        return False, True
    except discord.Forbidden:
        return False, True
    except discord.HTTPException:
        return False, False


async def _try_fetch_bookmarked_message(
    bot: discord.Client,
    bookmark: Bookmark,
) -> Optional[discord.Message]:
    """Best-effort fetch of the bookmarked message for richer delivery text."""
    channel = bot.get_channel(bookmark.channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(bookmark.channel_id)
        except Exception:
            return None

    fetch_message = getattr(channel, "fetch_message", None)
    if not callable(fetch_message):
        return None

    try:
        return await fetch_message(bookmark.message_id)
    except Exception:
        return None


def _format_bookmark_delivery_text(bookmark: Bookmark, message: Optional[discord.Message]) -> str:
    """Format bookmark delivery text similar to AFK recap style."""
    header = f"<@{bookmark.user_id}> bookmarked:"

    if message is None:
        # Fallback if we can't fetch the message.
        base = f"{header} {bookmark.message_link}"
        if bookmark.note:
            base += f"\nNote: {bookmark.note}"
        return base

    author = getattr(message.author, "display_name", "Unknown")
    channel_name = getattr(getattr(message, "channel", None), "name", "")
    content = (message.content or "").strip()
    attachments = list(getattr(message, "attachments", []) or [])
    if not content and attachments:
        content = "(Attachment only)"

    # Keep within Discord 2000-char limit with room for link/note.
    if len(content) > 900:
        content = content[:897] + "..."

    context = f"In #{channel_name}" if channel_name else ""
    lines = [header, f"From {author}. {context}".strip(), content, bookmark.message_link]

    if attachments:
        # Include up to 3 attachment URLs.
        att_lines = [a.url for a in attachments[:3] if getattr(a, "url", None)]
        if att_lines:
            lines.append("\n".join(att_lines))
    if bookmark.note:
        note = bookmark.note if len(bookmark.note) <= 400 else bookmark.note[:397] + "..."
        lines.append(f"Note: {note}")
    return "\n".join([l for l in lines if l])


def _schedule_delayed_bookmark(
    bot: discord.Client,
    bookmark: Bookmark,
    *,
    store: Optional[UtilityStore] = None,
) -> None:
    """Schedule one-shot delivery for a delayed bookmark (no polling)."""
    deliver_at = iso_to_dt(bookmark.deliver_at)
    if deliver_at is None:
        return

    scheduled: set[str] = getattr(bot, "_scheduled_bookmarks", set())
    tasks: dict[str, asyncio.Task] = getattr(bot, "_scheduled_bookmark_tasks", {})
    setattr(bot, "_scheduled_bookmarks", scheduled)
    setattr(bot, "_scheduled_bookmark_tasks", tasks)

    if bookmark.id in scheduled:
        return
    scheduled.add(bookmark.id)

    async def _runner() -> None:
        try:
            now = utcnow()
            delay = (deliver_at - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            delivered, permanent_fail = await _deliver_bookmark_now(bot, bookmark)
            user_store = store or UtilityStore(bookmark.user_id)
            await user_store.initialize()
            if delivered or permanent_fail:
                await user_store.remove_bookmark(bookmark.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Best effort cleanup so stuck deliveries don't churn CPU.
            try:
                user_store = store or UtilityStore(bookmark.user_id)
                await user_store.initialize()
                await user_store.remove_bookmark(bookmark.id)
            except Exception:
                pass
        finally:
            scheduled.discard(bookmark.id)
            tasks.pop(bookmark.id, None)

    tasks[bookmark.id] = asyncio.create_task(_runner())


async def schedule_existing_delayed_bookmarks(bot: discord.Client) -> int:
    """
    One-time startup catchup: schedule future delayed bookmarks and deliver overdue ones.

    Returns number of overdue bookmarks delivered.
    """
    sent = 0
    from core.paths import BASE_DIR

    base = BASE_DIR / "data" / "utility"
    if not base.exists():
        return 0

    for user_dir in base.iterdir():
        if not user_dir.is_dir():
            continue
        try:
            user_id = int(user_dir.name)
        except ValueError:
            continue

        store = UtilityStore(user_id)
        await store.initialize()
        bookmarks = await store.get_bookmarks()
        for bookmark in bookmarks:
            if bookmark.delivered:
                continue
            if not bookmark.deliver_at:
                continue
            deliver_at = iso_to_dt(bookmark.deliver_at)
            if deliver_at is None:
                continue
            if deliver_at <= utcnow():
                delivered, permanent_fail = await _deliver_bookmark_now(bot, bookmark)
                if delivered or permanent_fail:
                    await store.remove_bookmark(bookmark.id)
                    if delivered:
                        sent += 1
            else:
                _schedule_delayed_bookmark(bot, bookmark, store=store)

    return sent


# ─── AFK Handlers ─────────────────────────────────────────────────────────────


async def _handle_afk(message: discord.Message, parts: list[str]) -> None:
    """Handle AFK commands."""
    # Status lookup (self or mentioned user)
    if len(parts) > 1 and parts[1].lower() == "status":
        target = message.mentions[0] if message.mentions else message.author
        target_store = UtilityStore(target.id)
        await target_store.initialize()
        is_afk, afk_message = await target_store.is_afk()
        if not is_afk:
            await message.reply(f" {target.mention} is not AFK.")
            return
        msg = f"{target.mention} is AFK."
        if afk_message:
            msg += f" Message: {afk_message}"
        await message.reply(msg)
        return

    store = UtilityStore(message.author.id)
    await store.initialize()

    if len(parts) > 1 and parts[1].lower() == "off":
        # Clear AFK
        result = await store.clear_afk()

        if result["was_afk"]:
            mentions = result["mentions"]
            if mentions:
                lines: list[str] = []
                for m in mentions[:10]:
                    author = m.get("author", "Someone")
                    content = m.get("content", "")
                    guild_id = m.get("guild_id")
                    channel_id = m.get("channel_id")
                    message_id = m.get("message_id")
                    link = ""
                    if guild_id and channel_id and message_id:
                        link = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
                    snippet = content if len(content) <= 120 else f"{content[:117]}..."
                    if link:
                        lines.append(f"• {author}: {snippet}\n  {link}")
                    else:
                        lines.append(f"• {author}: {snippet}")

                dm_text = (
                    f"You were mentioned **{len(mentions)}** times while AFK:\n"
                    + "\n".join(lines)
                )
                try:
                    await message.author.send(dm_text)
                    await message.reply(" Welcome back! I sent your AFK recap in DMs.")
                except discord.Forbidden:
                    await message.reply(
                        " Welcome back! I couldn't DM you your AFK recap. Please enable DMs."
                    )
            else:
                await message.reply(" Welcome back!")
        else:
            await message.reply(" You weren't AFK")
        return

    # Set AFK
    afk_message = " ".join(parts[1:]).strip() if len(parts) > 1 else None
    await store.set_afk(afk_message)

    status_text = f" AFK set"
    if afk_message:
        status_text += f": {afk_message}"

    await message.reply(status_text)


# ─── Note Handlers ────────────────────────────────────────────────────────────


async def _handle_note(message: discord.Message, parts: list[str]) -> None:
    """Handle note commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `note <add|view|edit|remove>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "add":
        await _handle_note_add(message, parts)
    elif subcommand == "view":
        await _handle_note_view(message, parts)
    elif subcommand == "edit":
        await _handle_note_edit(message, parts)
    elif subcommand == "remove":
        await _handle_note_remove(message, parts)
    else:
        await message.reply(" Usage: `note <add|view|edit|remove>`")


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


async def _handle_note_view(message: discord.Message, parts: list[str]) -> None:
    """View a note."""
    if len(parts) < 3:
        await message.reply(" Usage: `note view <id>`")
        return
    note_id = parts[2].strip()
    store = UtilityStore(message.author.id)
    await store.initialize()
    notes = await store.get_notes()
    matching = [n for n in notes if isinstance(n, dict) and str(n.get("id", "")).startswith(note_id)]
    if not matching:
        await message.reply(f" No note found with ID starting with `{note_id}`")
        return
    note = matching[0]
    content = (note.get("content") or "").strip()
    embed = discord.Embed(
        title=f"Note `{str(note.get('id',''))[:8]}`",
        description=content[:3500] or "—",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    await message.reply(embed=embed)


async def _handle_note_edit(message: discord.Message, parts: list[str]) -> None:
    """Edit a note."""
    if len(parts) < 3:
        await message.reply(" Usage: `note edit <id> <text>`")
        return
    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `note edit <id> <text>`")
        return
    note_id_prefix = args[0]
    new_text = args[1].strip()
    if not new_text:
        await message.reply(" Please provide new text.")
        return

    store = UtilityStore(message.author.id)
    await store.initialize()
    notes = await store.get_notes()
    matching = [n for n in notes if isinstance(n, dict) and str(n.get("id", "")).startswith(note_id_prefix)]
    if not matching:
        await message.reply(f" No note found with ID starting with `{note_id_prefix}`")
        return
    note = matching[0]
    ok = await store.update_note(note.get("id", ""), new_text)
    if not ok:
        await message.reply(" Failed to update note.")
        return
    await message.reply(f" Note updated: `{str(note.get('id',''))[:8]}`")


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
    import io
    file = discord.File(
        fp=io.BytesIO(json_data.encode()),
        filename=f"user_data_{user_id}.json"
    )

    await message.reply(
        " Your data export is ready!",
        file=file
    )
