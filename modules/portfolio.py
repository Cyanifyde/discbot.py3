"""
Portfolio module - portfolio management for artists.

Provides commands for managing artwork portfolios with categories, privacy, and ordering.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
from typing import Optional

import aiohttp
import discord
from PIL import Image

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from services.portfolio_service import portfolio_service

logger = logging.getLogger("discbot.portfolio")

MODULE_NAME = "portfolio"

# URL validation regex - accept any valid HTTP/HTTPS URL with common image extensions
# or CDN URLs without extensions
URL_PATTERN = re.compile(
    r'https?://[^\s]+',
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
            ("portfolio privacy <id> <public|private>", "Set entry privacy"),
            ("portfolio view [@user] [category]", "View portfolio"),
            ("portfolio reorder <id> <position>", "Change entry display order"),
            ("portfolio beforeafter <before_url> <after_url> [title]", "Add before/after entry"),
            ("portfolio batch <url1> <url2> ...", "Add multiple entries at once"),
            ("portfolio stats", "View portfolio statistics"),
            ("ratecard help", "Show rate card commands"),
            ("portfolio help", "Show this help message"),
        ],
    )

    help_system.register_module(
        name="Rate Card",
        description="Rate card generator and settings (part of Portfolio).",
        help_command="ratecard help",
        commands=[
            ("ratecard", "Show your rate card (embed)"),
            ("ratecard set <name> <price> [desc]", "Set a rate (e.g. 'Sketch' 25)"),
            ("ratecard remove <name>", "Remove a rate"),
            ("ratecard list", "List all your rates"),
            ("ratecard title <title>", "Set rate card title"),
            ("ratecard subtitle <text>", "Set rate card subtitle"),
            ("ratecard status <open|closed>", "Set commission status"),
            ("ratecard currency <symbol>", "Set currency symbol (default: $)"),
            ("ratecard image <rate> + attach", "Add image to rate (webp converted)"),
            ("ratecard image <rate> remove", "Remove image from rate"),
            ("ratecard template <style>", "Embed themes: minimal, colorful, detailed, professional"),
            ("ratecard help", "Show this help message"),
        ],
        group="Portfolio",
        hidden=True,
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

    if len(parts) < 1:
        return False

    command = parts[0].lower()

    # Handle ratecard command separately
    if command == "ratecard":
        await _handle_ratecard_command(message, parts)
        return True

    if len(parts) < 2:
        return False

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
    """Handle 'portfolio privacy <id> <public|private>' command."""
    if len(parts) < 3:
        await message.reply(" Usage: `portfolio privacy <id> <public|private>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `portfolio privacy <id> <public|private>`")
        return

    entry_id = args[0]
    privacy = args[1].lower()

    if privacy == "federation":
        privacy = "private"
    if privacy not in ["public", "private"]:
        await message.reply(" Privacy must be: `public` or `private`")
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
        privacy_emoji = {"public": "", "private": ""}
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


# ─── Rate Card Commands ───────────────────────────────────────────────────────


async def _handle_ratecard_command(message: discord.Message, parts: list[str]) -> None:
    """Route ratecard subcommands."""
    if len(parts) < 2:
        # No subcommand, show rate card
        await _generate_ratecard(message, None)
        return

    subcommand = parts[1].lower()

    if subcommand == "set":
        await _handle_ratecard_set(message, parts)
    elif subcommand == "remove":
        await _handle_ratecard_remove(message, parts)
    elif subcommand == "list":
        await _handle_ratecard_list(message)
    elif subcommand == "title":
        await _handle_ratecard_setting(message, parts, "title")
    elif subcommand == "subtitle":
        await _handle_ratecard_setting(message, parts, "subtitle")
    elif subcommand == "status":
        await _handle_ratecard_status(message, parts)
    elif subcommand == "currency":
        await _handle_ratecard_setting(message, parts, "currency")
    elif subcommand == "template":
        await _handle_ratecard_template(message, parts)
    elif subcommand == "image":
        await _handle_ratecard_image(message, parts)
    elif subcommand == "help":
        help_text = help_system.get_module_help("Rate Card")
        if help_text:
            await message.reply(embed=help_text)
        else:
            await message.reply(" Help information not available.")
    else:
        # Unknown subcommand, generate rate card
        await _generate_ratecard(message, subcommand)


async def _generate_ratecard(message: discord.Message, template: Optional[str] = None) -> None:
    """Generate and send rate card embed."""
    user_id = message.author.id

    # Get rates and settings
    rates = await portfolio_service.get_rates(user_id)
    settings = await portfolio_service.get_rate_card_settings(user_id)

    if not rates:
        await message.reply(
            "You haven't set any rates yet!\n"
            "Use `ratecard set <name> <price>` to add rates.\n"
            "Example: `ratecard set Sketch 25`"
        )
        return

    # Determine template
    valid_templates = ["minimal", "colorful", "detailed", "professional"]
    if template is None:
        template = settings.get("template", "minimal")
    if template not in valid_templates:
        template = settings.get("template", "minimal")

    theme_colors = {
        "minimal": discord.Color.dark_grey(),
        "colorful": discord.Color.magenta(),
        "detailed": discord.Color.blue(),
        "professional": discord.Color.dark_blue(),
    }

    title = (settings.get("title") or "Commission Rates").strip()
    subtitle = (settings.get("subtitle") or "").strip()
    status = (settings.get("status") or "open").strip()
    currency = (settings.get("currency") or "$").strip()

    embed = discord.Embed(
        title=title,
        description=subtitle if subtitle else None,
        color=theme_colors.get(template, discord.Color.dark_grey()),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Currency", value=currency, inline=True)
    embed.add_field(name="Theme", value=template, inline=True)

    # Add rates (Discord limit: 25 fields)
    added = 0
    for rate_name in sorted(rates.keys(), key=lambda s: s.lower()):
        if added >= 22:
            break

        data = rates.get(rate_name)
        price = 0.0
        desc = ""
        if isinstance(data, dict):
            try:
                price = float(data.get("price", 0) or 0)
            except Exception:
                price = 0.0
            desc = (data.get("description") or "").strip()
        else:
            try:
                price = float(data or 0)
            except Exception:
                price = 0.0

        value = desc if desc else "—"
        embed.add_field(
            name=f"{rate_name} — {currency}{price:.2f}",
            value=value[:1024],
            inline=False,
        )
        added += 1

    remaining = max(0, len(rates) - added)
    if remaining:
        embed.add_field(name="More", value=f"+{remaining} more rate(s) not shown.", inline=False)

    # If any rate has an attached image (stored as data URI), attach the first one as a thumbnail.
    def _data_uri_to_bytes(data_uri: str) -> Optional[bytes]:
        if not isinstance(data_uri, str):
            return None
        if "base64," not in data_uri:
            return None
        try:
            b64 = data_uri.split("base64,", 1)[1]
            return base64.b64decode(b64)
        except Exception:
            return None

    image_bytes = None
    image_filename = None
    for rate_name in sorted(rates.keys(), key=lambda s: s.lower()):
        data = rates.get(rate_name)
        if isinstance(data, dict) and data.get("image"):
            image_bytes = _data_uri_to_bytes(data["image"])
            if image_bytes:
                safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", rate_name)[:30] or "rate"
                image_filename = f"rate_{safe_name}.webp"
                embed.set_thumbnail(url=f"attachment://{image_filename}")
            break

    if image_bytes and image_filename:
        file = discord.File(fp=io.BytesIO(image_bytes), filename=image_filename)
        await message.reply(embed=embed, file=file)
    else:
        await message.reply(embed=embed)


async def _handle_ratecard_set(message: discord.Message, parts: list[str]) -> None:
    """Handle 'ratecard set <name> <price> [description]'."""
    if len(parts) < 3:
        await message.reply("Usage: `ratecard set <name> <price> [description]`\nExample: `ratecard set Sketch 25 Quick sketch commission`")
        return

    args = parts[2].split(maxsplit=2)
    if len(args) < 2:
        await message.reply("Usage: `ratecard set <name> <price> [description]`")
        return

    name = args[0]
    try:
        price = float(args[1].replace("$", "").replace(",", ""))
    except ValueError:
        await message.reply("Invalid price. Please enter a number (e.g., 25 or 25.50)")
        return

    description = args[2] if len(args) > 2 else ""

    await portfolio_service.set_rate(message.author.id, name, price, description)
    settings = await portfolio_service.get_rate_card_settings(message.author.id)
    currency = settings.get("currency", "$")

    await message.reply(f"Rate set: **{name}** - {currency}{price:.2f}")


async def _handle_ratecard_remove(message: discord.Message, parts: list[str]) -> None:
    """Handle 'ratecard remove <name>'."""
    if len(parts) < 3:
        await message.reply("Usage: `ratecard remove <name>`")
        return

    name = parts[2].strip()
    success = await portfolio_service.remove_rate(message.author.id, name)

    if success:
        await message.reply(f"Removed rate: **{name}**")
    else:
        await message.reply(f"Rate not found: **{name}**")


async def _handle_ratecard_list(message: discord.Message) -> None:
    """Handle 'ratecard list' - show all rates."""
    user_id = message.author.id

    rates = await portfolio_service.get_rates(user_id)
    settings = await portfolio_service.get_rate_card_settings(user_id)

    if not rates:
        await message.reply(
            "No rates set. Use `ratecard set <name> <price>` to add rates.\n"
            "Example: `ratecard set Sketch 25`"
        )
        return

    currency = settings.get("currency", "$")
    title = settings.get("title", "Commission Rates")
    status = settings.get("status", "open")

    embed = discord.Embed(
        title=title,
        description=f"Status: **{status.upper()}**",
        color=discord.Color.green() if status == "open" else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    for name, data in rates.items():
        if isinstance(data, dict):
            price = data.get("price", 0)
            desc = data.get("description", "")
            value = f"{currency}{price:.2f}"
            if desc:
                value += f"\n*{desc}*"
        else:
            value = f"{currency}{data:.2f}"

        embed.add_field(name=name, value=value, inline=True)

    embed.set_footer(text="Use 'ratecard' to generate an image")
    await message.reply(embed=embed)


async def _handle_ratecard_setting(message: discord.Message, parts: list[str], setting: str) -> None:
    """Handle ratecard title/subtitle/currency settings."""
    if len(parts) < 3:
        await message.reply(f"Usage: `ratecard {setting} <value>`")
        return

    value = parts[2].strip()

    await portfolio_service.update_rate_card_settings(message.author.id, {setting: value})
    await message.reply(f"Rate card {setting} set to: **{value}**")


async def _handle_ratecard_status(message: discord.Message, parts: list[str]) -> None:
    """Handle 'ratecard status <open|closed>'."""
    if len(parts) < 3:
        await message.reply("Usage: `ratecard status <open|closed>`")
        return

    status = parts[2].strip().lower()
    if status not in ["open", "closed"]:
        await message.reply("Status must be `open` or `closed`")
        return

    await portfolio_service.update_rate_card_settings(message.author.id, {"status": status})
    await message.reply(f"Commission status set to: **{status.upper()}**")


async def _handle_ratecard_template(message: discord.Message, parts: list[str]) -> None:
    """Handle 'ratecard template <style>'."""
    valid_templates = ["minimal", "colorful", "detailed", "professional"]

    if len(parts) < 3:
        await message.reply(f"Usage: `ratecard template <style>`\nStyles: {', '.join(valid_templates)}")
        return

    template = parts[2].strip().lower()
    if template not in valid_templates:
        await message.reply(f"Invalid template. Choose from: {', '.join(valid_templates)}")
        return

    await portfolio_service.update_rate_card_settings(message.author.id, {"template": template})
    await message.reply(f"Default template set to: **{template}**")


async def _handle_ratecard_image(message: discord.Message, parts: list[str]) -> None:
    """Handle 'ratecard image <rate_name> [remove]' - set or remove image for a specific rate."""
    user_id = message.author.id

    # Need at least the rate name
    if len(parts) < 3 and not message.attachments:
        rates = await portfolio_service.get_rates(user_id)
        if rates:
            rate_names = ", ".join(rates.keys())
            await message.reply(
                f"Usage: `ratecard image <rate_name>` + attach an image\n"
                f"To remove: `ratecard image <rate_name> remove`\n"
                f"Your rates: {rate_names}"
            )
        else:
            await message.reply(
                "You need to set rates first with `ratecard set <name> <price>`"
            )
        return

    rate_name = parts[2].strip() if len(parts) >= 3 else ""

    if not rate_name:
        await message.reply("Please specify a rate name: `ratecard image <rate_name>`")
        return

    # Check if this rate exists
    rates = await portfolio_service.get_rates(user_id)
    if rate_name.lower() not in [r.lower() for r in rates.keys()]:
        # Check for remove command on showcase image
        if rate_name.lower() in ["remove", "clear"]:
            await portfolio_service.update_rate_card_settings(user_id, {"image": None})
            await message.reply("Showcase image removed.")
            return
        await message.reply(
            f"Rate '{rate_name}' not found.\n"
            f"Your rates: {', '.join(rates.keys())}"
        )
        return

    # Find exact rate name (case-insensitive match)
    actual_rate_name = next((r for r in rates.keys() if r.lower() == rate_name.lower()), rate_name)

    # Check for remove subcommand
    if len(parts) >= 4 and parts[3].strip().lower() in ["remove", "clear"]:
        success = await portfolio_service.remove_rate_image(user_id, actual_rate_name)
        if success:
            await message.reply(f"Image removed from **{actual_rate_name}**")
        else:
            await message.reply(f"Failed to remove image from **{actual_rate_name}**")
        return

    # Need an attachment
    if not message.attachments:
        await message.reply(
            f"Please attach an image to set for **{actual_rate_name}**\n"
            f"Usage: `ratecard image {actual_rate_name}` + attach image"
        )
        return

    attachment = message.attachments[0]
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        await message.reply("Attached file is not an image.")
        return

    # Download and convert image to webp
    try:
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(attachment.url) as resp:
                if resp.status != 200:
                    await message.reply("Failed to download image.")
                    return
                
                # Check content length before downloading
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB limit
                    await message.reply("Image is too large (max 10MB).")
                    return
                
                # Download in chunks with size limit
                chunks = []
                total_size = 0
                max_size = 10 * 1024 * 1024
                async for chunk in resp.content.iter_chunked(8192):
                    total_size += len(chunk)
                    if total_size > max_size:
                        await message.reply("Image is too large (max 10MB).")
                        return
                    chunks.append(chunk)
                image_data = b"".join(chunks)

        # Offload CPU-intensive PIL processing to thread
        def process_image(data: bytes) -> bytes:
            img = Image.open(io.BytesIO(data))

            # Convert to RGB if necessary (for transparency handling)
            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if too large (max 400px on longest side for rate thumbnails)
            max_size = 400
            if img.width > max_size or img.height > max_size:
                ratio = min(max_size / img.width, max_size / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # Save as webp with compression
            webp_buffer = io.BytesIO()
            img.save(webp_buffer, format="WEBP", quality=80, method=6)
            return webp_buffer.getvalue()
        
        webp_data = await asyncio.to_thread(process_image, image_data)

        # Convert to base64 data URI for embedding in HTML
        webp_base64 = base64.b64encode(webp_data).decode("utf-8")
        data_uri = f"data:image/webp;base64,{webp_base64}"

        # Save to rate
        success = await portfolio_service.set_rate_image(user_id, actual_rate_name, data_uri)

        if success:
            size_kb = len(webp_data) / 1024
            await message.reply(
                f"Image set for **{actual_rate_name}**\n"
                f"Converted to WebP ({size_kb:.1f} KB)"
            )
        else:
            await message.reply(f"Failed to set image for **{actual_rate_name}**")

    except Exception as e:
        logger.error("Failed to process rate image: %s", e)
        await message.reply("Failed to process image. Please try a different image.")

