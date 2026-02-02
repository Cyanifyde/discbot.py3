"""
Art search module - channel-restricted image search for a user's posted art.

Feature goals:
- Only searches in configured channels (per guild)
- Filters out GIFs
- Returns paginated message links
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import discord

from core.art_search_storage import ArtSearchStore
from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled

logger = logging.getLogger("discbot.art_search")

MODULE_NAME = "artsearch"
RESULTS_PER_PAGE = 5
HISTORY_LIMIT_PER_CHANNEL = 1000


@dataclass
class _ArtHit:
    channel_id: int
    message_id: int
    author_id: int
    created_at_iso: str
    attachment_url: str


def setup_art_search() -> None:
    help_system.register_module(
        name="Art Search",
        description="Search for images posted by a user in approved channels.",
        help_command="art help",
        commands=[
            ("art search @user [page]", "Search a user's images (filters GIFs)"),
            ("art channels", "List configured search channels"),
            ("art channels add <channel_id>", "Add a channel to the search allowlist (mod only)"),
            ("art channels remove <channel_id>", "Remove a channel from the search allowlist (mod only)"),
            ("art help", "Show this help message"),
        ],
    )


async def handle_art_search_command(message: discord.Message, bot: discord.Client) -> bool:
    if not message.guild:
        return False

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False

    content = (message.content or "").strip()
    if not content:
        return False

    parts = content.split()
    if not parts or parts[0].lower() != "art":
        return False

    if len(parts) == 1:
        return False

    sub = parts[1].lower()
    if sub == "help":
        await _cmd_help(message)
        return True

    if sub == "channels":
        await _cmd_channels(message, parts[2:])
        return True

    if sub == "search":
        await _cmd_search(message, parts[2:])
        return True

    return False


async def _cmd_help(message: discord.Message) -> None:
    embed = help_system.get_module_help("Art Search")
    if embed:
        await message.channel.send(embed=embed)
    else:
        await message.channel.send(" Usage: `art search @user [page]`")


async def _cmd_channels(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return

    store = ArtSearchStore(message.guild.id)
    await store.initialize()

    if not args:
        channels = await store.list_channels()
        if not channels:
            await message.channel.send(
                "No art-search channels configured.\n"
                "Mods can add one with: `art channels add <channel_id>`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        lines = ["**Art search channels:**"] + [f"- `{cid}`" for cid in channels]
        await message.channel.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())
        return

    action = args[0].lower()
    if action not in {"add", "remove"}:
        await message.channel.send(" Usage: `art channels [add|remove] <channel_id>`")
        return

    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "art channels"):
        await message.channel.send(" You don't have permission to manage art-search channels.")
        return

    if len(args) < 2 or not args[1].isdigit():
        await message.channel.send(" Usage: `art channels [add|remove] <channel_id>`")
        return

    channel_id = int(args[1])
    if action == "add":
        await store.add_channel(channel_id)
        await message.channel.send(f" Added `{channel_id}` to art-search channels.")
        return

    ok = await store.remove_channel(channel_id)
    if ok:
        await message.channel.send(f" Removed `{channel_id}` from art-search channels.")
    else:
        await message.channel.send(f" `{channel_id}` was not configured.")


async def _cmd_search(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return

    if not args or not message.mentions:
        await message.channel.send(" Usage: `art search @user [page]`")
        return

    target = message.mentions[0]
    page = 1
    if len(args) >= 2 and args[1].isdigit():
        page = max(1, int(args[1]))

    store = ArtSearchStore(message.guild.id)
    await store.initialize()
    channel_ids = await store.list_channels()
    if not channel_ids:
        await message.channel.send(
            "Art search is channel-restricted but no channels are configured.\n"
            "Mods: `art channels add <channel_id>`",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    hits = await _scan_for_user_images(message.guild, channel_ids, target.id, needed=page * RESULTS_PER_PAGE)
    if not hits:
        await message.channel.send(f"No recent images found for {target.mention}.", allowed_mentions=discord.AllowedMentions.none())
        return

    start = (page - 1) * RESULTS_PER_PAGE
    end = start + RESULTS_PER_PAGE
    page_hits = hits[start:end]
    if not page_hits:
        await message.channel.send("No more results.", allowed_mentions=discord.AllowedMentions.none())
        return

    embed = discord.Embed(
        title=f"Art Search: {target.display_name}",
        description=f"Page {page} (showing {len(page_hits)} results)",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    for idx, hit in enumerate(page_hits, start=1 + start):
        link = f"https://discord.com/channels/{message.guild.id}/{hit.channel_id}/{hit.message_id}"
        embed.add_field(
            name=f"Result #{idx}",
            value=f"{link}\n{hit.attachment_url}",
            inline=False,
        )

    await message.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def _scan_for_user_images(
    guild: discord.Guild,
    channel_ids: list[int],
    user_id: int,
    needed: int,
) -> list[_ArtHit]:
    hits: list[_ArtHit] = []

    for cid in channel_ids:
        if len(hits) >= needed:
            break

        channel = guild.get_channel(cid)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue

        try:
            async for msg in channel.history(limit=HISTORY_LIMIT_PER_CHANNEL, oldest_first=False):
                if msg.author.id != user_id:
                    continue

                att = _pick_image_attachment(msg)
                if not att:
                    continue

                hits.append(
                    _ArtHit(
                        channel_id=msg.channel.id,
                        message_id=msg.id,
                        author_id=msg.author.id,
                        created_at_iso=msg.created_at.isoformat(),
                        attachment_url=att.url,
                    )
                )
                if len(hits) >= needed:
                    break
        except Exception as e:
            logger.warning("Art search scan failed in channel %s: %s", cid, e)
            continue

    hits.sort(key=lambda h: h.created_at_iso, reverse=True)
    return hits


def _pick_image_attachment(msg: discord.Message) -> Optional[discord.Attachment]:
    for att in msg.attachments:
        ctype = (att.content_type or "").lower()
        filename = (att.filename or "").lower()

        is_image = ctype.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        if not is_image:
            continue
        if filename.endswith(".gif") or ctype == "image/gif":
            continue
        return att
    return None

