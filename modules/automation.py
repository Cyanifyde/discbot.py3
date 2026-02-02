"""
Automation module - triggers, schedules, and vacation mode.

Provides commands for automated actions, trigger management, and vacation mode.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import is_module_enabled
from services.automation_service import automation_service
from core.utils import parse_deadline

logger = logging.getLogger("discbot.automation")

MODULE_NAME = "automation"


def setup_automation() -> None:
    """Register help information for the automation module."""
    help_system.register_module(
        name="Automation",
        description="Automated actions, triggers, schedules, and vacation mode.",
        help_command="automation help",
        commands=[
            ("trigger help", "Automation trigger commands"),
            ("schedule help", "Scheduled action commands"),
            ("vacation help", "Vacation mode commands"),
            ("automation help", "Show this help message"),
        ],
    )

    help_system.register_module(
        name="Triggers",
        description="Automation triggers (mod only).",
        help_command="trigger help",
        commands=[
            ("trigger create <event> <action>", "Create automation trigger (mod only)"),
            ("trigger list", "List triggers (mod only)"),
            ("trigger toggle <id>", "Enable/disable trigger (mod only)"),
            ("trigger remove <id>", "Remove trigger (mod only)"),
            ("trigger help", "Show this help message"),
        ],
        group="Automation",
        hidden=True,
    )

    help_system.register_module(
        name="Schedules",
        description="Scheduled actions (mod only).",
        help_command="schedule help",
        commands=[
            ("schedule <action> <time>", "Schedule an action (mod only)"),
            ("schedule list", "List scheduled actions (mod only)"),
            ("schedule cancel <id>", "Cancel scheduled action (mod only)"),
            ("schedule help", "Show this help message"),
        ],
        group="Automation",
        hidden=True,
    )

    help_system.register_module(
        name="Vacation Mode",
        description="Vacation mode auto-responses and status.",
        help_command="vacation help",
        commands=[
            ("vacation on [return_date] [message]", "Enable vacation mode"),
            ("vacation off", "Disable vacation mode"),
            ("vacation status [@user]", "Check vacation status"),
            ("vacation help", "Show this help message"),
        ],
        group="Automation",
        hidden=True,
    )


async def handle_automation_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle automation-related commands.

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

    # Umbrella + per-subcommand help
    if command == "automation" and len(parts) >= 2 and parts[1].lower() == "help":
        embed = help_system.get_module_help("Automation")
        if embed:
            await message.reply(embed=embed)
        else:
            await message.reply(" Help information not available.")
        return True

    if len(parts) >= 2 and parts[1].lower() == "help":
        target_map = {"trigger": "Triggers", "schedule": "Schedules", "vacation": "Vacation Mode"}
        if command in target_map:
            embed = help_system.get_module_help(target_map[command])
            if embed:
                await message.reply(embed=embed)
            else:
                await message.reply(" Help information not available.")
            return True

    # Route to handlers
    if command == "trigger":
        await _handle_trigger(message, parts, bot)
        return True
    elif command == "schedule":
        await _handle_schedule(message, parts, bot)
        return True
    elif command == "vacation":
        await _handle_vacation(message, parts, bot)
        return True

    return False


# ─── Trigger Handlers ─────────────────────────────────────────────────────────


async def _handle_trigger(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle trigger commands."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to manage triggers.")
        return

    if len(parts) < 2:
        await message.reply(" Usage: `trigger <create|list|toggle|remove>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "create":
        await _handle_trigger_create(message, parts)
    elif subcommand == "list":
        await _handle_trigger_list(message)
    elif subcommand == "toggle":
        await _handle_trigger_toggle(message, parts)
    elif subcommand == "remove":
        await _handle_trigger_remove(message, parts)
    else:
        await message.reply(" Usage: `trigger <create|list|toggle|remove>`")


async def _handle_trigger_create(message: discord.Message, parts: list[str]) -> None:
    """Handle trigger creation."""
    if len(parts) < 3:
        await message.reply(
            " Usage: `trigger create <event> <action>`\n"
            "Events: commission_filled, slots_available\n"
            "Actions: notify, auto_close, auto_open"
        )
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `trigger create <event> <action>`")
        return

    event = args[0]
    action_str = args[1]

    guild_id = message.guild.id

    # Parse action
    action = {"type": action_str}
    if action_str == "notify" and message.channel_mentions:
        action["channel_id"] = message.channel_mentions[0].id
        action["message"] = "Automated notification"

    # Simple condition (always trigger)
    condition = {"type": "always"}

    trigger = await automation_service.create_trigger(
        guild_id,
        event,
        condition,
        action,
    )

    await message.reply(
        f" Trigger created! ID: `{trigger['id'][:8]}`\n"
        f"**Event:** {event}\n"
        f"**Action:** {action_str}"
    )


async def _handle_trigger_list(message: discord.Message) -> None:
    """Handle trigger listing."""
    guild_id = message.guild.id

    store = automation_service._get_store(guild_id)
    triggers = await store.get_all_triggers()

    if not triggers:
        await message.reply(" No triggers configured")
        return

    embed = discord.Embed(
        title="Automation Triggers",
        description=f"Total: {len(triggers)}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for trigger in triggers[:10]:
        status = " Enabled" if trigger.get("enabled") else " Disabled"
        count = trigger.get("trigger_count", 0)

        value = (
            f"**Event:** {trigger['event']}\n"
            f"**Action:** {trigger['action'].get('type', 'unknown')}\n"
            f"**Status:** {status}\n"
            f"**Triggered:** {count} times"
        )

        embed.add_field(
            name=f"Trigger `{trigger['id'][:8]}`",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_trigger_toggle(message: discord.Message, parts: list[str]) -> None:
    """Handle trigger toggle."""
    if len(parts) < 3:
        await message.reply(" Usage: `trigger toggle <id>`")
        return

    trigger_id = parts[2]
    guild_id = message.guild.id

    trigger = await automation_service.get_trigger(guild_id, trigger_id)
    if not trigger:
        await message.reply(f" No trigger found with ID starting with `{trigger_id}`")
        return

    new_enabled = not trigger.get("enabled", True)
    success = await automation_service.update_trigger(
        guild_id,
        trigger_id,
        enabled=new_enabled,
    )

    if success:
        status = "enabled" if new_enabled else "disabled"
        await message.reply(f" Trigger {status}")
    else:
        await message.reply(" Failed to update trigger")


async def _handle_trigger_remove(message: discord.Message, parts: list[str]) -> None:
    """Handle trigger removal."""
    if len(parts) < 3:
        await message.reply(" Usage: `trigger remove <id>`")
        return

    trigger_id = parts[2]
    guild_id = message.guild.id

    success = await automation_service.delete_trigger(guild_id, trigger_id)

    if success:
        await message.reply(" Trigger removed")
    else:
        await message.reply(f" No trigger found with ID starting with `{trigger_id}`")


# ─── Schedule Handlers ────────────────────────────────────────────────────────


async def _handle_schedule(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle schedule commands."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to manage schedules.")
        return

    if len(parts) < 2:
        await message.reply(" Usage: `schedule <action> <time>` or `schedule <list|cancel>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "list":
        await _handle_schedule_list(message)
    elif subcommand == "cancel":
        await _handle_schedule_cancel(message, parts)
    else:
        # Create schedule
        await _handle_schedule_create(message, parts)


async def _handle_schedule_create(message: discord.Message, parts: list[str]) -> None:
    """Handle schedule creation."""
    if len(parts) < 3:
        await message.reply(" Usage: `schedule <action> <time>`")
        return

    args = parts[1].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `schedule <action> <time>`")
        return

    action_str = args[0]
    time_str = args[1]

    guild_id = message.guild.id

    # Parse time
    execute_at = parse_deadline(time_str)
    if not execute_at:
        await message.reply(" Invalid time format. Try: `3d`, `2w`, `2024-12-31`")
        return

    # Create action
    action = {"type": action_str}
    if message.channel_mentions:
        action["channel_id"] = message.channel_mentions[0].id

    from core.utils import dt_to_iso
    schedule = await automation_service.schedule_action(
        guild_id,
        action,
        execute_at=dt_to_iso(execute_at),
    )

    await message.reply(
        f" Action scheduled! ID: `{schedule['id'][:8]}`\n"
        f"**Action:** {action_str}\n"
        f"**Execute at:** {execute_at.strftime('%Y-%m-%d %H:%M')}"
    )


async def _handle_schedule_list(message: discord.Message) -> None:
    """Handle schedule listing."""
    guild_id = message.guild.id

    store = automation_service._get_store(guild_id)
    schedules = await store.get_all_schedules()

    if not schedules:
        await message.reply(" No scheduled actions")
        return

    embed = discord.Embed(
        title="Scheduled Actions",
        description=f"Total: {len(schedules)}",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )

    for schedule in schedules[:10]:
        status = " Enabled" if schedule.get("enabled") else " Disabled"
        repeat = schedule.get("repeat", "one-time")

        value = (
            f"**Action:** {schedule['action'].get('type', 'unknown')}\n"
            f"**Execute at:** {schedule['execute_at'][:16]}\n"
            f"**Repeat:** {repeat}\n"
            f"**Status:** {status}"
        )

        embed.add_field(
            name=f"Schedule `{schedule['id'][:8]}`",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_schedule_cancel(message: discord.Message, parts: list[str]) -> None:
    """Handle schedule cancellation."""
    if len(parts) < 3:
        await message.reply(" Usage: `schedule cancel <id>`")
        return

    schedule_id = parts[2]
    guild_id = message.guild.id

    success = await automation_service.cancel_schedule(guild_id, schedule_id)

    if success:
        await message.reply(" Schedule cancelled")
    else:
        await message.reply(f" No schedule found with ID starting with `{schedule_id}`")


# ─── Vacation Mode Handlers ───────────────────────────────────────────────────


async def _handle_vacation(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle vacation mode commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `vacation <on|off|status>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "on":
        await _handle_vacation_on(message, parts)
    elif subcommand == "off":
        await _handle_vacation_off(message)
    elif subcommand == "status":
        await _handle_vacation_status(message, parts)
    else:
        await message.reply(" Usage: `vacation <on|off|status>`")


async def _handle_vacation_on(message: discord.Message, parts: list[str]) -> None:
    """Handle vacation mode enable."""
    guild_id = message.guild.id
    user_id = message.author.id

    return_date = None
    auto_response = "I'm currently on vacation and will respond when I return."

    if len(parts) >= 3:
        args = parts[2].split(maxsplit=1)
        return_date_str = args[0]

        # Parse return date
        return_dt = parse_deadline(return_date_str)
        if return_dt:
            from core.utils import dt_to_iso
            return_date = dt_to_iso(return_dt)

        if len(args) > 1:
            auto_response = args[1]

    vacation = await automation_service.set_vacation(
        guild_id,
        user_id,
        enabled=True,
        return_date=return_date,
        auto_response=auto_response,
    )

    response = " Vacation mode enabled!"
    if return_date:
        response += f"\n**Return date:** {return_date[:10]}"
    response += f"\n**Auto-response:** {auto_response}"

    await message.reply(response)


async def _handle_vacation_off(message: discord.Message) -> None:
    """Handle vacation mode disable."""
    guild_id = message.guild.id
    user_id = message.author.id

    await automation_service.set_vacation(
        guild_id,
        user_id,
        enabled=False,
    )

    await message.reply(" Welcome back! Vacation mode disabled.")


async def _handle_vacation_status(message: discord.Message, parts: list[str]) -> None:
    """Handle vacation status check."""
    guild_id = message.guild.id

    # Check for mentioned user or self
    if message.mentions:
        user_id = message.mentions[0].id
        user = message.mentions[0]
    else:
        user_id = message.author.id
        user = message.author

    vacation = await automation_service.get_vacation(guild_id, user_id)

    if not vacation or not vacation.get("enabled"):
        await message.reply(f" {user.display_name} is not on vacation")
        return

    embed = discord.Embed(
        title=f" {user.display_name}'s Vacation Status",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Status",
        value="On Vacation",
        inline=True,
    )

    if vacation.get("started_at"):
        embed.add_field(
            name="Since",
            value=vacation["started_at"][:10],
            inline=True,
        )

    if vacation.get("return_date"):
        embed.add_field(
            name="Returns",
            value=vacation["return_date"][:10],
            inline=True,
        )

    if vacation.get("auto_response"):
        embed.add_field(
            name="Auto-Response",
            value=vacation["auto_response"],
            inline=False,
        )

    await message.reply(embed=embed)
