"""
Portfolio module - portfolio management for artists.

Provides commands for managing artwork portfolios with categories, privacy, and ordering.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from services.portfolio_service import portfolio_service

logger = logging.getLogger("discbot.portfolio")

MODULE_NAME = "portfolio"

# URL validation regex (Discord CDN or common image hosts)
URL_PATTERN = re.compile(
    r'https?://(?:'
    r'cdn\.discordapp\.com|'
    r'media\.discordapp\.net|'
    r'imgur\.com|'
    r'i\.imgur\.com|'
    r'[^\s]+)'
    r'[^\s]*\.(?:png|jpg|jpeg|gif|webp)',
    re.IGNORECASE
)


def setup_portfolio() -> None:
    """Register help information for the portfolio module."""
    help_system.register_module(
        name="Portfolio",
        description="Portfolio management for artists to showcase their work.",
        help_command="portfolio help",
        commands=[
            ("portfolio add <url> [title]", "Add a new entry to your portfolio"),
            ("portfolio remove <id>", "Remove an entry from your portfolio"),
            ("portfolio category <id> <category>", "Set category for an entry"),
            ("portfolio tag <id> <tags...>", "Add tags to an entry"),
            ("portfolio feature <id>", "Set entry as featured"),
            ("portfolio privacy <id> <public|federation|private>", "Set entry privacy"),
            ("portfolio view [@user] [category]", "View portfolio"),
            ("portfolio reorder <id> <position>", "Change entry display order"),
            ("portfolio beforeafter <before_url> <after_url> [title]", "Add before/after entry"),
            ("portfolio batch <url1> <url2> ...", "Add multiple entries at once"),
            ("portfolio stats", "View portfolio statistics"),
            ("portfolio help", "Show this help message"),
        ],
    )


async def handle_portfolio_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle portfolio-related commands.

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

    if command != "portfolio":
        return False

    # Route to handlers
    if subcommand == "add":
        await _handle_add(message, parts)
        return True
    elif subcommand == "remove":
        await _handle_remove(message, parts)
        return True
    elif subcommand == "category":
        await _handle_category(message, parts)
        return True
    elif subcommand == "tag":
        await _handle_tag(message, parts)
        return True
    elif subcommand == "feature":
        await _handle_feature(message, parts)
        return True
    elif subcommand == "privacy":
        await _handle_privacy(message, parts)
        return True
    elif subcommand == "view":
        await _handle_view(message, parts, bot)
        return True
    elif subcommand == "reorder":
        await _handle_reorder(message, parts)
        return True
    elif subcommand == "beforeafter":
        await _handle_beforeafter(message, parts)
        return True
    elif subcommand == "batch":
        await _handle_batch(message, parts)
        return True
    elif subcommand == "stats":
        await _handle_stats(message)
        return True
    elif subcommand == "help":
        await _handle_help(message)
        return True

    return False


# ─── Command Handlers ─────────────────────────────────────────────────────────


async def _handle_add(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio add <url> [title]' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio add <url> [title]`")
        return

    args = parts[2].split(maxsplit=1)
    url = args[0]
    title = args[1] if len(args) > 1 else "Untitled"

    # Validate URL
    if not URL_PATTERN.match(url):
        await message.reply(" Invalid image URL. Please provide a valid image link.")
        return

    user_id = message.author.id

    entry = await portfolio_service.add_entry(user_id, url, title)

    await message.reply(
        f" Added to your portfolio!\n"
        f"**ID:** `{entry.id[:8]}`\n"
        f"**Title:** {title}\n"
        f"**Category:** {entry.category}"
    )


async def _handle_remove(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio remove <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio remove <id>`")
        return

    entry_id = parts[2]
    user_id = message.author.id

    # Find entry by partial ID
    entries = await portfolio_service.get_portfolio(user_id)
    matching = [e for e in entries if e.id.startswith(entry_id)]

    if not matching:
        await message.reply(f" No entry found with ID starting with `{entry_id}`")
        return

    entry = matching[0]

    success = await portfolio_service.remove_entry(user_id, entry.id)

    if success:
        await message.reply(f" Removed **{entry.title}** from your portfolio")
    else:
        await message.reply(" Failed to remove entry")


async def _handle_category(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio category <id> <category>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio category <id> <category>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `portfolio category <id> <category>`")
        return

    entry_id = args[0]
    category = args[1]

    user_id = message.author.id

    # Find entry
    entries = await portfolio_service.get_portfolio(user_id)
    matching = [e for e in entries if e.id.startswith(entry_id)]

    if not matching:
        await message.reply(f" No entry found with ID starting with `{entry_id}`")
        return

    entry = matching[0]

    # Add category if it doesn't exist
    await portfolio_service.add_category(user_id, category)

    success = await portfolio_service.update_entry(user_id, entry.id, {"category": category})

    if success:
        await message.reply(f" Set category to **{category}**")
    else:
        await message.reply(" Failed to update category")


async def _handle_tag(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio tag <id> <tags...>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio tag <id> <tags...>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `portfolio tag <id> <tags...>`")
        return

    entry_id = args[0]
    tags_str = args[1]
    tags = [t.strip() for t in tags_str.split(",")]

    user_id = message.author.id

    # Find entry
    entries = await portfolio_service.get_portfolio(user_id)
    matching = [e for e in entries if e.id.startswith(entry_id)]

    if not matching:
        await message.reply(f" No entry found with ID starting with `{entry_id}`")
        return

    entry = matching[0]

    success = await portfolio_service.update_entry(user_id, entry.id, {"tags": tags})

    if success:
        await message.reply(f" Tags set: {', '.join(tags)}")
    else:
        await message.reply(" Failed to set tags")


async def _handle_feature(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio feature <id>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio feature <id>`")
        return

    entry_id = parts[2]
    user_id = message.author.id

    # Find entry
    entries = await portfolio_service.get_portfolio(user_id)
    matching = [e for e in entries if e.id.startswith(entry_id)]

    if not matching:
        await message.reply(f" No entry found with ID starting with `{entry_id}`")
        return

    entry = matching[0]

    success = await portfolio_service.set_featured(user_id, entry.id)

    if success:
        await message.reply(f" Featured **{entry.title}**")
    else:
        await message.reply(" Failed to set featured entry")


async def _handle_privacy(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio privacy <id> <public|federation|private>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio privacy <id> <public|federation|private>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `portfolio privacy <id> <public|federation|private>`")
        return

    entry_id = args[0]
    privacy = args[1].lower()

    if privacy not in ["public", "federation", "private"]:
        await message.reply(" Privacy must be: `public`, `federation`, or `private`")
        return

    user_id = message.author.id

    # Find entry
    entries = await portfolio_service.get_portfolio(user_id)
    matching = [e for e in entries if e.id.startswith(entry_id)]

    if not matching:
        await message.reply(f" No entry found with ID starting with `{entry_id}`")
        return

    entry = matching[0]

    success = await portfolio_service.set_entry_privacy(user_id, entry.id, privacy)

    if success:
        await message.reply(f" Privacy set to **{privacy}**")
    else:
        await message.reply(" Failed to set privacy")


async def _handle_view(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Handle 'portfolio view [@user] [category]' command."""
    target_user = message.author
    category_filter = None

    if len(parts) >= 3:
        args = parts[2].split()
        # Check if first arg is a mention
        if message.mentions:
            target_user = message.mentions[0]
            if len(args) > 1:
                category_filter = args[1]
        else:
            category_filter = args[0]

    viewer_id = message.author.id

    # Get portfolio
    if category_filter:
        entries = await portfolio_service.get_portfolio_by_category(
            target_user.id,
            category_filter,
            viewer_id
        )
    else:
        entries = await portfolio_service.get_portfolio(target_user.id, viewer_id)

    if not entries:
        if target_user.id == viewer_id:
            await message.reply(" Your portfolio is empty. Use `portfolio add <url> [title]` to add entries.")
        else:
            await message.reply(f" {target_user.display_name}'s portfolio is empty.")
        return

    # Build embed
    title = f"{target_user.display_name}'s Portfolio"
    if category_filter:
        title += f" - {category_filter}"

    embed = discord.Embed(
        title=title,
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow(),
    )

    # Show first 10 entries
    for entry in entries[:10]:
        privacy_emoji = {"public": "", "federation": "", "private": ""}
        privacy_str = privacy_emoji.get(entry.privacy, "")

        value = f"**Category:** {entry.category}"
        if entry.tags:
            value += f"\n**Tags:** {', '.join(entry.tags[:5])}"
        if entry.featured:
            value += "\n **Featured**"
        value += f"\n**Views:** {entry.views} {privacy_str}"

        embed.add_field(
            name=f"{entry.title} (`{entry.id[:8]}`)",
            value=value,
            inline=False,
        )

        # Set thumbnail to featured entry
        if entry.featured:
            embed.set_thumbnail(url=entry.image_url)

    # Add footer with stats
    stats = await portfolio_service.get_stats(target_user.id)
    embed.set_footer(text=f"Total entries: {stats['total_entries']} | Total views: {stats['total_views']}")

    await message.reply(embed=embed)


async def _handle_reorder(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio reorder <id> <position>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio reorder <id> <position>`")
        return

    args = parts[2].split()
    if len(args) < 2:
        await message.reply(" Usage: `portfolio reorder <id> <position>`")
        return

    entry_id = args[0]
    try:
        position = int(args[1])
    except ValueError:
        await message.reply(" Position must be a number")
        return

    user_id = message.author.id

    # Find entry
    entries = await portfolio_service.get_portfolio(user_id)
    matching = [e for e in entries if e.id.startswith(entry_id)]

    if not matching:
        await message.reply(f" No entry found with ID starting with `{entry_id}`")
        return

    entry = matching[0]

    success = await portfolio_service.reorder(user_id, entry.id, position)

    if success:
        await message.reply(f" Moved **{entry.title}** to position **{position}**")
    else:
        await message.reply(" Failed to reorder entry")


async def _handle_beforeafter(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio beforeafter <before_url> <after_url> [title]' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio beforeafter <before_url> <after_url> [title]`")
        return

    args = parts[2].split(maxsplit=2)
    if len(args) < 2:
        await message.reply(" Usage: `portfolio beforeafter <before_url> <after_url> [title]`")
        return

    before_url = args[0]
    after_url = args[1]
    title = args[2] if len(args) > 2 else "Before & After"

    # Validate URLs
    if not URL_PATTERN.match(before_url) or not URL_PATTERN.match(after_url):
        await message.reply(" Invalid image URLs. Please provide valid image links.")
        return

    user_id = message.author.id

    entry = await portfolio_service.add_before_after(
        user_id, before_url, after_url, title
    )

    await message.reply(
        f" Added before/after entry!\n"
        f"**ID:** `{entry.id[:8]}`\n"
        f"**Title:** {title}"
    )


async def _handle_batch(message: discord.Message, parts: list[str]) -> None:
    """Handle 'portfolio batch <url1> <url2> ...' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio batch <url1> <url2> ...`")
        return

    urls = parts[2].split()

    # Validate URLs
    valid_urls = [url for url in urls if URL_PATTERN.match(url)]

    if not valid_urls:
        await message.reply(" No valid image URLs found.")
        return

    user_id = message.author.id

    entries = await portfolio_service.batch_add(user_id, valid_urls)

    await message.reply(
        f" Added **{len(entries)}** entries to your portfolio!\n"
        f"Use `portfolio view` to see them."
    )


async def _handle_stats(message: discord.Message) -> None:
    """Handle 'portfolio stats' command."""
    user_id = message.author.id

    stats = await portfolio_service.get_stats(user_id)

    embed = discord.Embed(
        title="Portfolio Statistics",
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Total Entries",
        value=str(stats["total_entries"]),
        inline=True,
    )

    embed.add_field(
        name="Total Views",
        value=str(stats["total_views"]),
        inline=True,
    )

    embed.add_field(
        name="Featured",
        value=str(stats["featured_count"]),
        inline=True,
    )

    # Category breakdown
    if stats["categories"]:
        cat_str = "\n".join(
            f"• **{cat}:** {count}"
            for cat, count in stats["categories"].items()
        )
        embed.add_field(
            name="By Category",
            value=cat_str or "None",
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_help(message: discord.Message) -> None:
    """Handle 'portfolio help' command."""
    help_text = help_system.get_module_help("Portfolio")
    if help_text:
        await message.reply(embed=help_text)
    else:
        await message.reply(" Help information not available.")
