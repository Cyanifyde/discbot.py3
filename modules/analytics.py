"""
Analytics module for viewing statistics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_module, is_module_enabled
from services.analytics_service import AnalyticsService


def setup_analytics() -> None:
    """Register analytics commands with the help system."""
    help_system.register_module(
        name="Analytics",
        description="View statistics and trends.",
        help_command="stats help",
        commands=[
            ("stats commissions [period]", "View commission statistics (period: month/year/all)"),
            ("stats profile", "View your profile statistics"),
            ("stats bot", "View bot statistics"),
            ("stats trends <metric>", "View trends for a metric (e.g., commissions)"),
        ],
    )


async def handle_analytics_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle stats commands.

    Args:
        message: Discord message object
        bot: Discord bot instance (unused)

    Returns:
        bool: True if command was handled
    """
    content = (message.content or "").strip()
    if not content.lower().startswith("stats"):
        return False

    args = content.lower().split()
    if not args or args[0] != "stats":
        return False

    if not message.guild:
        await message.reply("This command can only be used in servers.", mention_author=False)
        return True

    if not await is_module_enabled(message.guild.id, "analytics"):
        await message.reply(
            "Analytics module is disabled in this server.\n"
            "An administrator can enable it with `modules enable analytics`",
            mention_author=False,
        )
        return True

    if not isinstance(message.author, discord.Member):
        return True

    if not await can_use_module(message.author, "analytics"):
        await message.reply(
            "You don't have permission to use analytics commands in this server.\n"
            "An administrator can grant access with `modules allow analytics @YourRole`",
            mention_author=False,
        )
        return True

    analytics = AnalyticsService()

    if len(args) == 1 or args[1] == "help":
        embed = help_system.get_module_embed("Analytics")
        if embed is not None:
            await message.reply(embed=embed, mention_author=False)
        else:
            await message.reply("No help registered for Analytics.", mention_author=False)
        return True

    subcommand = args[1]

    if subcommand == "commissions":
        # Commission statistics
        period = args[2] if len(args) > 2 else "all"
        guild_id = message.guild.id if message.guild else None

        if not guild_id:
            await message.reply("This command must be used in a server.", mention_author=False)
            return True

        stats = analytics.get_commission_stats(guild_id, period)

        embed = discord.Embed(
            title=f"Commission Statistics — {period.capitalize()}",
            color=0x3498db,
            timestamp=datetime.now()
        )

        if period in ["month", "year"]:
            embed.add_field(name="Completed", value=str(stats.get("count", 0)), inline=True)
            embed.add_field(name="Total Value", value=f"${stats.get('value', 0):.2f}", inline=True)
        else:
            embed.add_field(name="Total Completed", value=str(stats.get("total_completed", 0)), inline=True)
            embed.add_field(name="Total Value", value=f"${stats.get('total_value', 0):.2f}", inline=True)
            embed.add_field(
                name="Avg Completion Time",
                value=f"{stats.get('avg_completion_time_hours', 0):.1f} hours",
                inline=True
            )

            # By type
            if stats.get("by_type"):
                type_text = "\n".join([
                    f"**{type_name}**: {data['count']} (${data['value']:.2f})"
                    for type_name, data in sorted(stats["by_type"].items(), key=lambda x: x[1]["count"], reverse=True)[:5]
                ])
                embed.add_field(name="Top Types", value=type_text or "No data", inline=False)

            # Recent months
            if stats.get("by_month"):
                month_items = sorted(stats["by_month"].items(), reverse=True)[:6]
                month_text = "\n".join([
                    f"**{month}**: {data['count']} (${data['value']:.2f})"
                    for month, data in month_items
                ])
                embed.add_field(name="Recent Months", value=month_text or "No data", inline=False)

        await message.reply(embed=embed, mention_author=False)
        return True

    elif subcommand == "profile":
        # Profile statistics
        user_id = message.author.id
        stats = analytics.get_profile_stats(user_id)

        embed = discord.Embed(
            title=f"Profile Statistics — {message.author.name}",
            color=0x3498db,
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)

        embed.add_field(name="Total Profile Views", value=str(stats.get("total_views", 0)), inline=True)

        # Recent weeks
        if stats.get("views_by_week"):
            week_items = sorted(stats["views_by_week"].items(), reverse=True)[:4]
            week_text = "\n".join([f"**{week}**: {count} views" for week, count in week_items])
            embed.add_field(name="Recent Weeks", value=week_text or "No data", inline=False)

        # Portfolio views
        if stats.get("portfolio_views"):
            portfolio_items = sorted(stats["portfolio_views"].items(), key=lambda x: x[1], reverse=True)[:5]
            portfolio_text = "\n".join([f"**Entry {entry_id[:8]}...**: {count} views" for entry_id, count in portfolio_items])
            embed.add_field(name="Top Portfolio Pieces", value=portfolio_text or "No data", inline=False)

        await message.reply(embed=embed, mention_author=False)
        return True

    elif subcommand == "bot":
        # Bot statistics
        stats = analytics.get_bot_stats()

        embed = discord.Embed(
            title="Bot Statistics",
            color=0x3498db,
            timestamp=datetime.now()
        )

        embed.add_field(name="Commands Run", value=f"{stats.get('total_commands_run', 0):,}", inline=True)
        embed.add_field(name="Messages Scanned", value=f"{stats.get('total_messages_scanned', 0):,}", inline=True)
        embed.add_field(name="Guilds Tracked", value=str(stats.get("guilds_tracked", 0)), inline=True)

        # Uptime
        uptime_seconds = stats.get("uptime_seconds", 0)
        days = uptime_seconds // 86400
        hours = (uptime_seconds % 86400) // 3600
        minutes = (uptime_seconds % 3600) // 60
        uptime_str = f"{days}d {hours}h {minutes}m"
        embed.add_field(name="Uptime", value=uptime_str, inline=True)

        await message.reply(embed=embed, mention_author=False)
        return True

    elif subcommand == "trends":
        # Trends for a metric
        if len(args) < 3:
            await message.reply(
                "Usage: `stats trends <metric>` (e.g., `stats trends commissions`)",
                mention_author=False,
            )
            return True

        metric = args[2]
        guild_id = message.guild.id if message.guild else None

        if not guild_id:
            await message.reply("This command must be used in a server.", mention_author=False)
            return True

        trends = analytics.calculate_trends(guild_id, metric)

        if not trends:
            await message.reply(f"No trend data available for metric: {metric}", mention_author=False)
            return True

        embed = discord.Embed(
            title=f"Trends — {metric.capitalize()}",
            color=0x3498db,
            timestamp=datetime.now()
        )

        # Show last 8 weeks
        recent_trends = trends[-8:]
        trend_text = "\n".join([
            f"**{t['week']}**: {t['count']} completed, ${t['total_value']:.2f} value, {t['avg_completion_time']:.1f}h avg"
            for t in recent_trends
        ])

        embed.add_field(name="Recent Weeks", value=trend_text or "No data", inline=False)

        # Calculate growth
        if len(recent_trends) >= 2:
            first_count = recent_trends[0]["count"]
            last_count = recent_trends[-1]["count"]
            if first_count > 0:
                growth = ((last_count - first_count) / first_count) * 100
                embed.add_field(
                    name="Growth (First to Last)",
                    value=f"{growth:+.1f}%",
                    inline=True
                )

        await message.reply(embed=embed, mention_author=False)
        return True

    await message.reply(f"Unknown stats subcommand: `{subcommand}`", mention_author=False)
    return True


def register_help() -> None:
    """Backward-compatible alias for older imports."""
    setup_analytics()
