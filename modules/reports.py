"""
Reports module - user report system for moderation.

Provides commands for submitting, managing, and resolving user reports.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from services.report_service import report_service

logger = logging.getLogger("discbot.reports")

MODULE_NAME = "reports"


def setup_reports() -> None:
    """Register help information for the reports module."""
    help_system.register_module(
        name="Reports",
        description="User report system for reporting rule violations to moderators.",
        help_command="report help",
        commands=[
            ("report @user <category> <reason>", "Submit a report about a user"),
            ("report list [status]", "List reports (mod only)"),
            ("report view <id>", "View report details (mod only)"),
            ("report assign <id> @mod", "Assign report to moderator (mod only)"),
            ("report resolve <id> [notes]", "Resolve a report (mod only)"),
            ("report dismiss <id> [reason]", "Dismiss a report (mod only)"),
            ("report stats", "View report statistics (mod only)"),
            ("report categories list", "List report categories (mod only)"),
            ("report categories add <name>", "Add report category (mod only)"),
            ("report categories remove <name>", "Remove report category (mod only)"),
            ("report help", "Show this help message"),
        ],
    )


async def handle_report_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle report-related commands.

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

    if command != "report":
        return False

    if subcommand == "categories":
        await _handle_categories(message, parts)
        return True

    # Handle user report submission (doesn't require mod permissions)
    if subcommand in ("submit", "@") or (message.mentions and len(parts) >= 3):
        await _handle_submit(message, parts)
        return True

    # Route to handlers (all require mod permissions)
    if subcommand == "list":
        await _handle_list(message, parts)
        return True
    elif subcommand == "view":
        await _handle_view(message, parts)
        return True
    elif subcommand == "assign":
        await _handle_assign(message, parts)
        return True
    elif subcommand == "resolve":
        await _handle_resolve(message, parts)
        return True
    elif subcommand == "dismiss":
        await _handle_dismiss(message, parts)
        return True
    elif subcommand == "stats":
        await _handle_stats(message)
        return True
    elif subcommand == "help":
        await _handle_help(message)
        return True

    return False


async def _handle_categories(message: discord.Message, parts: list[str]) -> None:
    """Manage report categories (mod only)."""
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to manage report categories.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `report categories <list|add|remove> ...`")
        return

    args = parts[2].split(maxsplit=1)
    action = args[0].lower() if args else ""
    guild_id = message.guild.id

    if action == "list":
        categories = await report_service.get_categories(guild_id)
        if not categories:
            await message.reply(" No categories configured.")
            return
        await message.reply(
            "**Report Categories:**\n" + "\n".join(f"- `{c}`" for c in categories),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if action in {"add", "remove"}:
        if len(args) < 2:
            await message.reply(f" Usage: `report categories {action} <name>`")
            return
        name = args[1].strip().lower()
        if not name:
            await message.reply(f" Usage: `report categories {action} <name>`")
            return

        if action == "add":
            ok = await report_service.add_category(guild_id, name)
            if ok:
                await message.reply(f" Category added: `{name}`")
            else:
                await message.reply(" Category already exists (or invalid).")
            return

        ok = await report_service.remove_category(guild_id, name)
        if ok:
            await message.reply(f" Category removed: `{name}`")
        else:
            await message.reply(" Category not found.")
        return

    await message.reply(" Usage: `report categories <list|add|remove> ...`")


# ─── Command Handlers ─────────────────────────────────────────────────────────


async def _handle_submit(message: discord.Message, parts: list[str]) -> None:
    """Handle 'report @user <category> <reason>' command for users to submit reports."""
    if not message.mentions:
        await message.reply(" Please mention the user you want to report: `report @user <category> <reason>`")
        return

    target_user = message.mentions[0]
    
    if target_user.id == message.author.id:
        await message.reply(" You cannot report yourself.")
        return
    
    if target_user.bot:
        await message.reply(" You cannot report bots.")
        return

    # Parse category and reason from remaining text
    content_parts = message.content.split(maxsplit=2)
    if len(content_parts) < 3:
        categories = await report_service.get_categories(message.guild.id)
        await message.reply(
            f" Please provide a category and reason.\n"
            f"Usage: `report @user <category> <reason>`\n"
            f"Categories: {', '.join(categories)}"
        )
        return

    # Extract text after the mention
    remaining = content_parts[2]
    reason_parts = remaining.split(maxsplit=1)
    
    if len(reason_parts) < 2:
        await message.reply(" Please provide both a category and a reason.")
        return
    
    category = reason_parts[0].lower()
    reason = reason_parts[1] if len(reason_parts) > 1 else "No reason provided"
    
    # Validate category
    categories = await report_service.get_categories(message.guild.id)
    if category not in categories:
        await message.reply(
            f" Invalid category. Valid options: {', '.join(categories)}"
        )
        return

    # Create the report
    report = await report_service.create_report(
        reporter_id=message.author.id,
        target_id=target_user.id,
        message_id=message.id,
        guild_id=message.guild.id,
        category=category,
        details=reason,
    )

    await message.reply(
        f" Report submitted! ID: `{report.id[:8]}`\n"
        f"Category: {category}\n"
        f"Moderators will review your report."
    )


async def _handle_list(message: discord.Message, parts: list[str]) -> None:
    """Handle 'report list [status]' command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view reports.")
        return

    status_filter = None
    if len(parts) >= 3:
        status_filter = parts[2].lower()

    guild_id = message.guild.id

    reports = await report_service.get_reports(guild_id, status=status_filter)

    if not reports:
        msg = " No reports found"
        if status_filter:
            msg += f" with status '{status_filter}'"
        await message.reply(msg)
        return

    # Build embed
    title = "Reports"
    if status_filter:
        title += f" - {status_filter.title()}"

    embed = discord.Embed(
        title=title,
        description=f"Total: {len(reports)}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )

    # Show first 10 reports
    for report in reports[:10]:
        status_emoji = {
            "open": "",
            "assigned": "",
            "resolved": "",
            "dismissed": "",
        }
        emoji = status_emoji.get(report.status, "")

        priority_emoji = {
            "urgent": "",
            "normal": "",
            "low": "",
        }
        p_emoji = priority_emoji.get(report.priority, "")

        value = (
            f"**Reporter:** <@{report.reporter_id}>\n"
            f"**Target:** <@{report.target_id}>\n"
            f"**Category:** {report.category}\n"
            f"**Priority:** {report.priority} {p_emoji}\n"
            f"**Status:** {report.status} {emoji}"
        )

        if report.assigned_mod_id:
            value += f"\n**Assigned:** <@{report.assigned_mod_id}>"

        embed.add_field(
            name=f"Report `{report.id[:8]}`",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_view(message: discord.Message, parts: list[str]) -> None:
    """Handle 'report view <id>' command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view reports.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `report view <id>`")
        return

    report_id = parts[2]
    guild_id = message.guild.id

    # Find report by partial ID
    reports = await report_service.get_reports(guild_id)
    matching = [r for r in reports if r.id.startswith(report_id)]

    if not matching:
        await message.reply(f" No report found with ID starting with `{report_id}`")
        return

    report = matching[0]

    # Build detailed embed
    embed = discord.Embed(
        title=f"Report Details - {report.id[:8]}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Reporter",
        value=f"<@{report.reporter_id}>",
        inline=True,
    )

    embed.add_field(
        name="Target",
        value=f"<@{report.target_id}>",
        inline=True,
    )

    embed.add_field(
        name="Category",
        value=report.category,
        inline=True,
    )

    embed.add_field(
        name="Priority",
        value=report.priority,
        inline=True,
    )

    embed.add_field(
        name="Status",
        value=report.status,
        inline=True,
    )

    embed.add_field(
        name="Created",
        value=report.created_at[:10],
        inline=True,
    )

    if report.assigned_mod_id:
        embed.add_field(
            name="Assigned To",
            value=f"<@{report.assigned_mod_id}>",
            inline=True,
        )

    if report.resolved_at:
        embed.add_field(
            name="Resolved",
            value=report.resolved_at[:10],
            inline=True,
        )

    if report.outcome:
        embed.add_field(
            name="Outcome",
            value=report.outcome,
            inline=False,
        )

    if report.notes:
        notes_str = "\n".join(f"• {note}" for note in report.notes[:5])
        embed.add_field(
            name="Notes",
            value=notes_str,
            inline=False,
        )

    # Add link to reported message
    if report.target_message_id:
        msg_link = f"https://discord.com/channels/{guild_id}/{message.channel.id}/{report.target_message_id}"
        embed.add_field(
            name="Reported Message",
            value=f"[Jump to message]({msg_link})",
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_assign(message: discord.Message, parts: list[str]) -> None:
    """Handle 'report assign <id> @mod' command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to assign reports.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `report assign <id> @mod`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2 or not message.mentions:
        await message.reply(" Usage: `report assign <id> @mod`")
        return

    report_id = args[0]
    mod = message.mentions[0]

    guild_id = message.guild.id

    # Find report
    reports = await report_service.get_reports(guild_id)
    matching = [r for r in reports if r.id.startswith(report_id)]

    if not matching:
        await message.reply(f" No report found with ID starting with `{report_id}`")
        return

    report = matching[0]

    success = await report_service.assign_report(guild_id, report.id, mod.id)

    if success:
        await message.reply(f" Assigned report to {mod.mention}")
    else:
        await message.reply(" Failed to assign report")


async def _handle_resolve(message: discord.Message, parts: list[str]) -> None:
    """Handle 'report resolve <id> [notes]' command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to resolve reports.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `report resolve <id> [notes]`")
        return

    args = parts[2].split(maxsplit=1)
    report_id = args[0]
    notes = args[1] if len(args) > 1 else None

    guild_id = message.guild.id

    # Find report
    reports = await report_service.get_reports(guild_id)
    matching = [r for r in reports if r.id.startswith(report_id)]

    if not matching:
        await message.reply(f" No report found with ID starting with `{report_id}`")
        return

    report = matching[0]

    success = await report_service.resolve_report(
        guild_id,
        report.id,
        outcome="Action taken",
        notes=notes,
    )

    if success:
        await message.reply(" Report resolved")
    else:
        await message.reply(" Failed to resolve report")


async def _handle_dismiss(message: discord.Message, parts: list[str]) -> None:
    """Handle 'report dismiss <id> [reason]' command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to dismiss reports.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `report dismiss <id> [reason]`")
        return

    args = parts[2].split(maxsplit=1)
    report_id = args[0]
    reason = args[1] if len(args) > 1 else "No reason provided"

    guild_id = message.guild.id

    # Find report
    reports = await report_service.get_reports(guild_id)
    matching = [r for r in reports if r.id.startswith(report_id)]

    if not matching:
        await message.reply(f" No report found with ID starting with `{report_id}`")
        return

    report = matching[0]

    success = await report_service.dismiss_report(guild_id, report.id, reason)

    if success:
        await message.reply(" Report dismissed")
    else:
        await message.reply(" Failed to dismiss report")


async def _handle_stats(message: discord.Message) -> None:
    """Handle 'report stats' command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view report stats.")
        return

    guild_id = message.guild.id

    stats = await report_service.get_report_stats(guild_id)

    embed = discord.Embed(
        title="Report Statistics",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Total Reports",
        value=str(stats["total"]),
        inline=True,
    )

    # Status breakdown
    by_status = stats.get("by_status", {})
    if by_status:
        status_str = "\n".join(
            f"• **{status.title()}:** {count}"
            for status, count in by_status.items()
        )
        embed.add_field(
            name="By Status",
            value=status_str or "None",
            inline=False,
        )

    # Category breakdown
    by_category = stats.get("by_category", {})
    if by_category:
        cat_str = "\n".join(
            f"• **{cat}:** {count}"
            for cat, count in list(by_category.items())[:5]
        )
        embed.add_field(
            name="By Category",
            value=cat_str or "None",
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_help(message: discord.Message) -> None:
    """Handle 'report help' command."""
    help_text = help_system.get_module_help("Reports")
    if help_text:
        await message.reply(embed=help_text)
    else:
        await message.reply(" Help information not available.")


# ─── Context Menu Integration ─────────────────────────────────────────────────

async def handle_report_message_context(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    """
    Handle "Report Message" context menu action.

    This should be registered as a message context menu command.
    """
    if not interaction.guild:
        await interaction.response.send_message(
            " Reports can only be filed in servers.",
            ephemeral=True
        )
        return

    # Check if module is enabled
    if not await is_module_enabled(interaction.guild.id, MODULE_NAME):
        await interaction.response.send_message(
            " The report system is not enabled in this server.",
            ephemeral=True
        )
        return

    # Get categories
    categories = await report_service.get_categories(interaction.guild.id)

    # Create modal for report details
    class ReportModal(discord.ui.Modal, title="Report Message"):
        category_select = discord.ui.TextInput(
            label="Category",
            placeholder=f"Choose: {', '.join(categories[:3])}...",
            max_length=50,
            required=True,
        )

        details = discord.ui.TextInput(
            label="Additional Details (Optional)",
            style=discord.TextStyle.paragraph,
            placeholder="Provide any additional context...",
            max_length=500,
            required=False,
        )

        async def on_submit(self, submit_interaction: discord.Interaction):
            category = self.category_select.value.lower()

            # Validate category
            if category not in categories:
                await submit_interaction.response.send_message(
                    f" Invalid category. Valid options: {', '.join(categories)}",
                    ephemeral=True
                )
                return

            # Create report
            report = await report_service.create_report(
                reporter_id=submit_interaction.user.id,
                target_id=message.author.id,
                message_id=message.id,
                guild_id=submit_interaction.guild.id,
                category=category,
            )

            await submit_interaction.response.send_message(
                f" Report submitted! ID: `{report.id[:8]}`\n"
                f"Moderators will review your report.",
                ephemeral=True
            )

    await interaction.response.send_modal(ReportModal())
