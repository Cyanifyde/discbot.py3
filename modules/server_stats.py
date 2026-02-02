"""
Server Stats module - displays server statistics and information.
"""
from __future__ import annotations

import logging
import os
import platform
from datetime import datetime

import discord

from core.help_system import help_system
from core.permissions import is_module_enabled

logger = logging.getLogger("discbot.server_stats")

MODULE_NAME = "serverstats"


def setup_server_stats() -> None:
    """Register help information for the server stats module."""
    help_system.register_module(
        name="Server Stats",
        description="Display server statistics and information.",
        help_command="serverstats help",
        commands=[
            ("serverstats", "Show server overview and statistics"),
            ("serverstats help", "Show this help message"),
            ("botstatus", "Show bot status (CPU/RAM/uptime)"),
            ("botstatus help", "Show bot status help"),
        ]
    )


async def handle_serverstats_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle the serverstats command.

    Returns True if the command was handled.
    """
    content = (message.content or "").strip()
    content_lower = content.lower()

    if content_lower == "serverstats help":
        embed = help_system.get_module_help("Server Stats")
        if embed:
            await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.reply("Help not available.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        return True

    if content_lower == "botstatus help":
        await message.reply(
            "Usage: `botstatus`\nShows bot uptime and (if available) CPU/RAM usage charts.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if content_lower == "botstatus":
        await _handle_botstatus(message, bot)
        return True

    if content_lower != "serverstats":
        return False

    if not message.guild:
        return False

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.reply(
            "Server Stats module is disabled in this server.\n"
            "An administrator can enable it with `modules enable serverstats`",
            mention_author=False,
        )
        return True

    guild = message.guild

    # Count channels by type
    text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
    voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
    categories = len([c for c in guild.channels if isinstance(c, discord.CategoryChannel)])

    # Count members
    total_members = guild.member_count or 0

    # Try to get online count (requires presence intent)
    online_count = 0
    try:
        online_count = sum(
            1 for m in guild.members
            if m.status != discord.Status.offline
        )
    except Exception:
        pass

    # Boost info
    boost_level = guild.premium_tier
    boost_count = guild.premium_subscription_count or 0

    # Created date
    created_at = guild.created_at
    created_str = created_at.strftime("%B %d, %Y")

    # Build embed
    embed = discord.Embed(
        title=f"Server Stats for {guild.name}",
        color=0x5865F2,
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # Members
    member_value = f"{total_members:,}"
    if online_count > 0:
        member_value += f" ({online_count:,} online)"
    embed.add_field(name="Members", value=member_value, inline=True)

    # Channels
    channel_value = f"{text_channels} text, {voice_channels} voice"
    if categories > 0:
        channel_value += f", {categories} categories"
    embed.add_field(name="Channels", value=channel_value, inline=True)

    # Roles
    embed.add_field(name="Roles", value=str(len(guild.roles) - 1), inline=True)  # -1 for @everyone

    # Created
    embed.add_field(name="Created", value=created_str, inline=True)

    # Boost
    boost_value = f"Level {boost_level}"
    if boost_count > 0:
        boost_value += f" ({boost_count} boosts)"
    embed.add_field(name="Boost Level", value=boost_value, inline=True)

    # Owner
    if guild.owner:
        owner = guild.owner
        # Don't ping the owner.
        value = f"{owner.name}#{owner.discriminator} (`{owner.id}`)"
        embed.add_field(name="Owner", value=value, inline=False)

    # Emojis and Stickers
    emoji_count = len(guild.emojis)
    sticker_count = len(guild.stickers)
    if emoji_count > 0 or sticker_count > 0:
        extra_value = f"{emoji_count} emojis, {sticker_count} stickers"
        embed.add_field(name="Custom", value=extra_value, inline=True)

    # Verification level
    verification_levels = {
        discord.VerificationLevel.none: "None",
        discord.VerificationLevel.low: "Low",
        discord.VerificationLevel.medium: "Medium",
        discord.VerificationLevel.high: "High",
        discord.VerificationLevel.highest: "Highest",
    }
    embed.add_field(
        name="Verification",
        value=verification_levels.get(guild.verification_level, "Unknown"),
        inline=True,
    )

    embed.set_footer(text=f"Server ID: {guild.id}")

    await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
    return True


def _bar(percent: float, *, width: int = 16) -> str:
    p = max(0.0, min(100.0, float(percent)))
    filled = int(round((p / 100.0) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {p:0.1f}%"


async def _handle_botstatus(message: discord.Message, bot: discord.Client) -> None:
    """Show bot process/system status."""
    if not message.guild:
        return

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.reply(
            "Server Stats module is disabled in this server.\n"
            "An administrator can enable it with `modules enable serverstats`",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    started_at = getattr(bot, "started_at", None)
    now = discord.utils.utcnow()
    uptime_str = "Unknown"
    if started_at:
        try:
            delta = now - started_at
            secs = int(delta.total_seconds())
            days, rem = divmod(secs, 86400)
            hours, rem = divmod(rem, 3600)
            mins, _ = divmod(rem, 60)
            uptime_str = f"{days}d {hours}h {mins}m"
        except Exception:
            pass

    latency_ms = float(getattr(bot, "latency", 0.0) or 0.0) * 1000.0

    embed = discord.Embed(
        title="Bot Status",
        color=0x5865F2,
        timestamp=now,
    )
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    embed.add_field(name="Latency", value=f"{latency_ms:0.0f} ms", inline=True)
    embed.add_field(name="Guilds", value=str(len(getattr(bot, "guilds", []) or [])), inline=True)

    try:
        import psutil  # type: ignore

        proc = psutil.Process(os.getpid())
        proc_cpu = proc.cpu_percent(interval=0.2)
        sys_cpu = psutil.cpu_percent(interval=0.2)
        vm = psutil.virtual_memory()
        proc_mem_mb = proc.memory_info().rss / (1024 * 1024)

        embed.add_field(name="CPU (System)", value=f"```{_bar(sys_cpu)}```", inline=False)
        embed.add_field(name="CPU (Bot)", value=f"```{_bar(proc_cpu)}```", inline=False)
        embed.add_field(
            name="RAM (System)",
            value=f"```{_bar(vm.percent)}\n{vm.used/(1024**3):0.2f} / {vm.total/(1024**3):0.2f} GB```",
            inline=False,
        )
        embed.add_field(name="RAM (Bot)", value=f"{proc_mem_mb:0.1f} MB RSS", inline=True)
    except Exception:
        embed.add_field(name="CPU/RAM", value="Install `psutil` for CPU/RAM charts.", inline=False)

    embed.add_field(name="Python", value=platform.python_version(), inline=True)
    embed.add_field(name="discord.py", value=getattr(discord, "__version__", "unknown"), inline=True)
    embed.add_field(name="Platform", value=f"{platform.system()} {platform.release()}", inline=True)

    await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
