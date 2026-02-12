"""
Inactivity service - controls the inactivity enforcement system.

This service allows moderators to enable/disable the inactivity checker
that enforces against users who haven't posted within the threshold period.
The inactivity checker doesn't auto-run by default.

Text commands:
    inactivity enable   - Enable inactivity enforcement
    inactivity disable  - Disable inactivity enforcement
    inactivity status   - Check if enforcement is enabled and show stats
    inactivity step     - Run one enforcement step manually
    inactivity stats    - Show detailed enforcement statistics
    inactivity help     - Show all inactivity commands

The inactivity state is persisted in guild config and survives bot restarts.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord

from core.config_migration import get_guild_module_data, update_guild_module_data
from core.constants import K
from core.utils import iso_to_dt, safe_int, utcnow
from core.help_system import help_system
from core.permissions import can_use_module, is_module_enabled
from core.command_registry import command_registry, CommandRoute

if TYPE_CHECKING:
    from bot.client import DiscBot
    from bot.guild_state import GuildState

logger = logging.getLogger("discbot.inactivity")

MODULE_NAME = "inactivity"

COMMAND_PATTERN = re.compile(r"^inactivity\s+(\w+)(?:\s+(.*))?$", re.IGNORECASE)

SUBCOMMANDS = {
    "enable", "disable", "status", "step", "stats", "help",
    "setup", "removerole", "addrole", "clearroles", "config",
    "setgrace", "setbaseline", "init", "diagnose",
}

# Default state structure
DEFAULT_STATE: Dict[str, Any] = {
    "enabled": False,
    "enabled_at": None,
    "enabled_by": None,
    "disabled_at": None,
    "disabled_by": None,
    "total_enforced": 0,
    "total_scanned": 0,
    "last_step_at": None,
    "roles_to_remove": [],  # Role IDs to remove on enforcement (empty = all roles)
    "roles_to_add": [],     # Role IDs to add on enforcement
    "grace_period_days": 3,  # Days new members have to post before enforcement
    "baseline_date": None,   # First run baseline - users must have posted since this date
}


_HELP_REGISTERED = False


def register_help() -> None:
    """Register help information for the inactivity service."""
    global _HELP_REGISTERED
    if _HELP_REGISTERED:
        return
    help_system.register_module(
        name="Inactivity Enforcement",
        description="Enforce actions against users who haven't posted within the threshold period.",
        help_command="inactivity help",
        commands=[
            ("inactivity help", "Show all inactivity commands"),
            ("inactivity enable", "Enable inactivity checking"),
            ("inactivity disable", "Disable inactivity checking"),
            ("inactivity status", "Check current enforcement status"),
            ("inactivity stats", "View enforcement statistics"),
            ("inactivity step", "Manually run enforcement step"),
            ("inactivity setup", "Quick setup wizard"),
            ("inactivity config", "Show current configuration"),
            ("inactivity init", "Initialize baseline date for enforcement"),
            ("inactivity setgrace <days>", "Set grace period for new members"),
            ("inactivity setbaseline <YYYY-MM-DD>", "Set baseline date manually"),
            ("inactivity addrole <role_id>", "Add role to assign on enforcement"),
            ("inactivity removerole <role_id>", "Remove role from assignment list"),
            ("inactivity clearroles", "Clear all role assignments"),
            ("inactivity diagnose", "Sample records and show why they were skipped"),
        ],
    )
    _HELP_REGISTERED = True

    command_registry.register(CommandRoute(
        name="inactivity",
        roots=["inactivity"],
        handler=handle_command,
        needs_bot=True,
    ))


def setup_inactivity_service() -> None:
    """Idempotent setup entrypoint for inactivity registration."""
    register_help()


def _is_mod(member: discord.Member) -> bool:
    """Check if member has mod permissions."""
    perms = member.guild_permissions
    return (
        perms.administrator
        or perms.manage_guild
        or perms.manage_roles
        or perms.manage_messages
    )


async def get_state(guild_id: int) -> Dict[str, Any]:
    """Get the inactivity state for a guild."""
    data = await get_guild_module_data(guild_id, MODULE_NAME)
    if data is None:
        return dict(DEFAULT_STATE)
    # Ensure all keys exist
    result = dict(DEFAULT_STATE)
    result.update(data)
    return result


async def set_enabled(guild_id: int, enabled: bool, user_id: int) -> Dict[str, Any]:
    """Set the inactivity enabled state."""
    data = await get_state(guild_id)

    if enabled:
        data["enabled"] = True
        data["enabled_at"] = utcnow().isoformat()
        data["enabled_by"] = user_id
    else:
        data["enabled"] = False
        data["disabled_at"] = utcnow().isoformat()
        data["disabled_by"] = user_id

    await update_guild_module_data(guild_id, MODULE_NAME, data)
    return data


async def increment_stats(guild_id: int, enforced: int = 0, scanned: int = 0) -> None:
    """Increment inactivity statistics."""
    data = await get_state(guild_id)
    data["total_enforced"] = data.get("total_enforced", 0) + enforced
    data["total_scanned"] = data.get("total_scanned", 0) + scanned
    data["last_step_at"] = utcnow().isoformat()
    await update_guild_module_data(guild_id, MODULE_NAME, data)


async def update_state(guild_id: int, updates: dict) -> None:
    """Merge arbitrary key-value updates into the guild inactivity state."""
    data = await get_state(guild_id)
    data.update(updates)
    await update_guild_module_data(guild_id, MODULE_NAME, data)


async def is_enabled(guild_id: int) -> bool:
    """Check if inactivity enforcement is enabled for a guild."""
    data = await get_state(guild_id)
    return data.get("enabled", False)


async def handle_command(message: discord.Message, bot: "DiscBot") -> bool:
    """
    Handle inactivity commands.

    Returns True if message was an inactivity command (handled), False otherwise.
    """
    if not message.guild:
        return False

    content = message.content.strip()
    match = COMMAND_PATTERN.match(content)
    if not match:
        return False

    subcommand = match.group(1).lower()
    if subcommand not in SUBCOMMANDS:
        return False

    member = message.guild.get_member(message.author.id)
    if not member:
        return False
    
    # Check module permissions (guild-specific)
    if not await is_module_enabled(message.guild.id, "inactivity"):
        await message.reply(
            "Inactivity module is disabled in this server.\\n"
            "An administrator can enable it with `modules enable inactivity`",
            mention_author=False,
        )
        return True
    
    if not await can_use_module(member, "inactivity"):
        await message.reply(
            "You don't have permission to use inactivity commands in this server.\\n"
            "An administrator can grant access with `modules allow inactivity @YourRole`",
            mention_author=False,
        )
        return True

    guild_id = message.guild.id
    state = bot.guild_states.get(guild_id)

    if subcommand == "help":
        await _cmd_help(message)
    elif subcommand == "enable":
        await _cmd_enable(message, bot, state)
    elif subcommand == "disable":
        await _cmd_disable(message, bot, state)
    elif subcommand == "status":
        await _cmd_status(message, bot, state)
    elif subcommand == "step":
        await _cmd_step(message, bot, state)
    elif subcommand == "stats":
        await _cmd_stats(message, bot, state)
    elif subcommand == "setup":
        await _cmd_setup(message)
    elif subcommand == "removerole":
        await _cmd_removerole(message, match.group(2))
    elif subcommand == "addrole":
        await _cmd_addrole(message, match.group(2))
    elif subcommand == "clearroles":
        await _cmd_clearroles(message)
    elif subcommand == "config":
        await _cmd_config(message)
    elif subcommand == "setgrace":
        await _cmd_setgrace(message, match.group(2))
    elif subcommand == "setbaseline":
        await _cmd_setbaseline(message, match.group(2))
    elif subcommand == "init":
        await _cmd_init(message)
    elif subcommand == "diagnose":
        await _cmd_diagnose(message, bot, state)

    return True


async def _cmd_help(message: discord.Message) -> None:
    """Show help for inactivity commands using the help system."""
    embed = help_system.get_module_embed("Inactivity Enforcement")
    if embed is None:
        await message.reply("Help not available.", mention_author=False)
        return
    await message.reply(embed=embed, mention_author=False)


async def _cmd_enable(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Enable inactivity enforcement."""
    guild_id = message.guild.id

    current = await get_state(guild_id)
    if current.get("enabled"):
        await message.reply(
            "Inactivity enforcement is already enabled!",
            mention_author=False,
        )
        return

    if not state:
        await message.reply(
            "Guild state not initialized. Please try again later.",
            mention_author=False,
        )
        return

    await set_enabled(guild_id, True, message.author.id)
    logger.info(
        "Enabled inactivity enforcement for guild %s by user %s",
        guild_id,
        message.author.id,
    )

    threshold = int(state.config.get(K.INACTIVE_DAYS_THRESHOLD, 0))
    msg_threshold = int(state.config.get(K.INACTIVITY_MESSAGE_THRESHOLD, 3))
    
    data = await get_state(guild_id)
    grace_days = data.get("grace_period_days", 3)

    await message.reply(
        "**Inactivity enforcement enabled!**\n"
        f"**Inactive threshold:** {threshold} days\n"
        f"**Message threshold:** {msg_threshold} messages\n"
        f"**Grace period:** {grace_days} days (for new members)\n"
        "**Note:** Users who post at least once are never checked again.\n\n"
        "Enforcement will run automatically every 6 hours.\n"
        "You can also use `inactivity step` to run enforcement manually at any time.",
        mention_author=False,
    )


async def _cmd_disable(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Disable inactivity enforcement."""
    guild_id = message.guild.id

    current = await get_state(guild_id)
    if not current.get("enabled"):
        await message.reply(
            "Inactivity enforcement is already disabled.",
            mention_author=False,
        )
        return

    await set_enabled(guild_id, False, message.author.id)
    logger.info(
        "Disabled inactivity enforcement for guild %s by user %s",
        guild_id,
        message.author.id,
    )

    await message.reply(
        "**Inactivity enforcement disabled.**\n"
        "No users will be enforced for inactivity.\n"
        "Use `inactivity enable` to re-enable.",
        mention_author=False,
    )


async def _cmd_status(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Show inactivity enforcement status."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    enabled = data.get("enabled", False)
    status_text = "Enabled" if enabled else "Disabled"

    lines = [
        f"**Inactivity Enforcement Status: {status_text}**",
        "",
    ]

    if state:
        threshold = int(state.config.get(K.INACTIVE_DAYS_THRESHOLD, 0))
        msg_threshold = int(state.config.get(K.INACTIVITY_MESSAGE_THRESHOLD, 3))
        max_scan = int(state.config.get(K.ENFORCEMENT_SCAN_MAX_USERS_PER_RUN, 0))

        lines.append("**Configuration:**")
        lines.append(f"• Inactive threshold: {threshold} days")
        lines.append(f"• Message threshold: {msg_threshold} messages")
        lines.append(f"• Max users per step: {max_scan}")
        
        # Show grace period and baseline
        grace_days = data.get("grace_period_days", 7)
        lines.append(f"• Grace period: {grace_days} days (for new members)")
        
        baseline_str = data.get("baseline_date")
        if baseline_str:
            try:
                baseline_dt = iso_to_dt(baseline_str)
                if baseline_dt:
                    baseline_formatted = baseline_dt.strftime("%Y-%m-%d")
                    lines.append(f"• Baseline date: {baseline_formatted}")
            except Exception:
                pass
        else:
            lines.append("• Baseline date: Not set (use `inactivity init`)")

        cursor = state.storage.state_data.get("enforcement_cursor", {})
        shard = cursor.get("shard", "00")
        lines.append(f"\n**Current cursor:** shard {shard}")
    else:
        lines.append("Guild state not initialized")

    if data.get("enabled_by"):
        lines.append(f"\n**Last enabled by:** User ID {data['enabled_by']}")
        if data.get("enabled_at"):
            lines.append(f"**Enabled at:** {data['enabled_at']}")

    if data.get("disabled_by"):
        lines.append(f"\n**Last disabled by:** User ID {data['disabled_by']}")
        if data.get("disabled_at"):
            lines.append(f"**Disabled at:** {data['disabled_at']}")

    await message.reply(
        "\n".join(lines),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _cmd_stats(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Show detailed inactivity stats."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    lines = [
        "**Inactivity Enforcement Statistics**",
        "",
        f"**Total Enforced:** {data.get('total_enforced', 0):,}",
        f"**Total Scanned:** {data.get('total_scanned', 0):,}",
    ]

    total_scanned = data.get("total_scanned", 0)
    total_enforced = data.get("total_enforced", 0)

    if total_scanned > 0:
        enforce_rate = (total_enforced / total_scanned) * 100
        lines.append(f"**Enforcement Rate:** {enforce_rate:.2f}%")

    if data.get("last_step_at"):
        lines.append(f"\n**Last step at:** {data['last_step_at']}")

    if state:
        lines.append("")
        lines.append("**Current Session:**")
        lines.append(f"• Actions taken: {state.action_count:,}")

        counts = await state.storage.summary_counts()
        lines.append(f"• Total records: {counts.get('total', 0):,}")
        lines.append(f"• Cleared: {counts.get('cleared', 0):,}")
        lines.append(f"• Enforced: {counts.get('enforced', 0):,}")
    # Show last step skip reasons if available
    last_skips = data.get("last_skip_reasons")
    if last_skips and isinstance(last_skips, dict):
        lines.append("")
        lines.append("**Last Step Skip Reasons:**")
        for reason, count in last_skips.items():
            if count > 0:
                lines.append(f"\u2022 {reason}: {count:,}")
        if not any(v > 0 for v in last_skips.values()):
            lines.append("\u2022 (none)")
    await message.reply("\n".join(lines), mention_author=False)


async def _cmd_step(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Run one inactivity enforcement step."""
    guild_id = message.guild.id

    current = await get_state(guild_id)
    if not current.get("enabled"):
        await message.reply(
            "Inactivity enforcement is disabled. Use `inactivity enable` first.",
            mention_author=False,
        )
        return

    if not state:
        await message.reply(
            "Guild state not initialized.",
            mention_author=False,
        )
        return

    guild = message.guild
    await message.reply(" Running enforcement step...", mention_author=False)

    try:
        enforced, scanned, skip_reasons = await run_enforcement_step(bot, state, guild)
        await increment_stats(guild_id, enforced=enforced, scanned=scanned)
        await update_state(guild_id, {"last_skip_reasons": skip_reasons})

        skip_lines = []
        for reason, count in skip_reasons.items():
            if count > 0:
                skip_lines.append(f"  {reason}: {count:,}")

        skip_text = "\n".join(skip_lines) if skip_lines else "  (none)"

        await message.channel.send(
            f"**Enforcement step complete!**\n"
            f"**Scanned:** {scanned:,} users\n"
            f"**Enforced:** {enforced:,} users\n"
            f"**Skip reasons:**\n{skip_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as e:
        logger.error("Failed to run enforcement step: %s", e)
        await message.channel.send(
            f"Enforcement step failed: {e}",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _cmd_setup(message: discord.Message) -> None:
    """Show setup instructions."""
    help_text = """**Inactivity Setup Instructions**

**1. Set up time configuration (recommended first):**
```
inactivity init
```
Gives all current members 30 days to post at least once.

```
inactivity setgrace 3
```
Set grace period for new members (days they have to post before enforcement).
Default is 3 days.

**2. Configure roles to remove on enforcement:**
```
inactivity removerole <role_id>
```
Add a role ID that will be removed when a user is enforced.
Use `inactivity removerole all` to remove ALL roles (except @everyone).

**3. Configure roles to add on enforcement:**
```
inactivity addrole <role_id>
```
Add a role ID that will be given to users when enforced.

**4. View current configuration:**
```
inactivity config
inactivity status
```

**Example Full Setup:**
```
inactivity init
inactivity setgrace 3
inactivity removerole 123456789012345678
inactivity addrole 987654321098765432
inactivity enable
```

**Important Notes:**
- **Users only need to post ONCE** to never be checked again
- **Grace Period**: New members get X days (default 3) to post
- **Baseline (init)**: Gives current members 30 days from now to post once
"""
    await message.reply(help_text, mention_author=False)


async def _cmd_removerole(message: discord.Message, args: Optional[str]) -> None:
    """Add a role to the removal list."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `inactivity removerole <role_id|all>`",
            mention_author=False,
        )
        return

    args = args.strip().lower()

    roles_to_remove = list(data.get("roles_to_remove", []))

    if args == "all":
        if "all" not in roles_to_remove:
            roles_to_remove.append("all")
        data["roles_to_remove"] = roles_to_remove
        await update_guild_module_data(guild_id, MODULE_NAME, data)
        await message.reply(
            "**Configured to remove ALL roles** on enforcement.",
            mention_author=False,
        )
        return

    # Parse role ID
    role_id_str = args.strip("<@&>")
    if not role_id_str.isdigit():
        await message.reply(
            "Invalid role ID. Provide a numeric role ID or 'all'.",
            mention_author=False,
        )
        return

    role_id = int(role_id_str)

    # Verify role exists
    role = message.guild.get_role(role_id)
    if not role:
        await message.reply(
            f"Role with ID `{role_id}` not found in this server.",
            mention_author=False,
        )
        return

    if role_id in roles_to_remove:
        await message.reply(
            f"Role **{role.name}** is already in the removal list.",
            mention_author=False,
        )
        return

    roles_to_remove.append(role_id)
    data["roles_to_remove"] = roles_to_remove
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        f"Role **{role.name}** (`{role_id}`) will be removed on enforcement.",
        mention_author=False,
    )


async def _cmd_addrole(message: discord.Message, args: Optional[str]) -> None:
    """Add a role to the add list."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `inactivity addrole <role_id>`",
            mention_author=False,
        )
        return

    # Parse role ID
    role_id_str = args.strip().strip("<@&>")
    if not role_id_str.isdigit():
        await message.reply(
            "Invalid role ID. Provide a numeric role ID.",
            mention_author=False,
        )
        return

    role_id = int(role_id_str)

    # Verify role exists
    role = message.guild.get_role(role_id)
    if not role:
        await message.reply(
            f"Role with ID `{role_id}` not found in this server.",
            mention_author=False,
        )
        return

    roles_to_add = list(data.get("roles_to_add", []))
    if role_id in roles_to_add:
        await message.reply(
            f"Role **{role.name}** is already in the add list.",
            mention_author=False,
        )
        return

    roles_to_add.append(role_id)
    data["roles_to_add"] = roles_to_add
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        f"Role **{role.name}** (`{role_id}`) will be added on enforcement.",
        mention_author=False,
    )


async def _cmd_clearroles(message: discord.Message) -> None:
    """Clear all configured roles."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    data["roles_to_remove"] = []
    data["roles_to_add"] = []
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        "**All role configurations cleared.**\n"
        "No roles will be removed or added on enforcement.",
        mention_author=False,
    )


async def _cmd_config(message: discord.Message) -> None:
    """Show current role configuration."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    lines = ["**Inactivity Role Configuration**", ""]

    # Roles to remove
    roles_to_remove = data.get("roles_to_remove", [])
    if not roles_to_remove:
        lines.append("**Roles to Remove:** None configured")
    elif "all" in roles_to_remove:
        lines.append("**Roles to Remove:** ALL roles")
    else:
        lines.append("**Roles to Remove:**")
        for role_id in roles_to_remove:
            if isinstance(role_id, int):
                role = message.guild.get_role(role_id)
                name = role.name if role else "Unknown"
                lines.append(f"• {name} (`{role_id}`)")

    # Roles to add
    roles_to_add = data.get("roles_to_add", [])
    if not roles_to_add:
        lines.append("\n**Roles to Add:** None configured")
    else:
        lines.append("\n**Roles to Add:**")
        for role_id in roles_to_add:
            if isinstance(role_id, int):
                role = message.guild.get_role(role_id)
                name = role.name if role else "Unknown"
                lines.append(f"• {name} (`{role_id}`)")

    await message.reply("\n".join(lines), mention_author=False)


async def _cmd_setgrace(message: discord.Message, args: Optional[str]) -> None:
    """Set grace period for new members."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `inactivity setgrace <days>`\n"
            "Example: `inactivity setgrace 7` (new members get 7 days before enforcement)",
            mention_author=False,
        )
        return

    try:
        days = int(args.strip())
        if days < 0:
            raise ValueError("Days must be non-negative")
    except ValueError:
        await message.reply(
            "Invalid number. Provide a positive integer (days).",
            mention_author=False,
        )
        return

    data["grace_period_days"] = days
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        f"**Grace period set to {days} days.**\n"
        f"New members will have {days} days to post before enforcement.",
        mention_author=False,
    )


async def _cmd_setbaseline(message: discord.Message, args: Optional[str]) -> None:
    """Set baseline date for first run."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `inactivity setbaseline <YYYY-MM-DD>`\n"
            "Example: `inactivity setbaseline 2026-01-01`\n"
            "All users must have posted since this date, or use `inactivity init` for current date.",
            mention_author=False,
        )
        return

    try:
        # Parse date
        date_str = args.strip()
        baseline_dt = dt.datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=dt.timezone.utc
        )
    except ValueError:
        await message.reply(
            "Invalid date format. Use YYYY-MM-DD (e.g., 2026-01-15).",
            mention_author=False,
        )
        return

    data["baseline_date"] = baseline_dt.isoformat()
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        f"**Baseline date set to {date_str}.**\n"
        f"Users who haven't posted since this date will be subject to enforcement.",
        mention_author=False,
    )


async def _cmd_init(message: discord.Message) -> None:
    """Initialize baseline - gives current members 30 days to post."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    now = utcnow()
    # Set baseline to 30 days ago so current members have 30 days from now to post
    baseline_dt = now - dt.timedelta(days=30)
    data["baseline_date"] = baseline_dt.isoformat()
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    baseline_str = baseline_dt.strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")
    await message.reply(
        f"**Baseline initialized to {baseline_str}.**\n"
        f"Current members now have 30 days (until ~{today_str}) to post at least once.\n"
        f"Users who have posted even once will never be checked again.",
        mention_author=False,
    )


async def _cmd_diagnose(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Sample uncleared/unenforced records and show why each is skipped."""
    if not state:
        await message.reply("Guild state not initialized.", mention_author=False)
        return

    guild = message.guild
    now = utcnow()
    threshold_days = int(state.config.get(K.INACTIVE_DAYS_THRESHOLD, 0))
    max_messages = int(state.config.get(K.INACTIVITY_MESSAGE_THRESHOLD, 3))

    inactivity_data = await get_state(guild.id)
    baseline_date_str = inactivity_data.get("baseline_date")
    baseline_date = iso_to_dt(baseline_date_str) if baseline_date_str else None

    samples: list[str] = []
    sample_limit = 20

    shards = [f"{i:02d}" for i in range(100)]
    for shard in shards:
        if len(samples) >= sample_limit:
            break
        data = await state.storage._read_shard_file(state.storage.shard_path(shard))
        for uid, record in data.items():
            if len(samples) >= sample_limit:
                break
            if not isinstance(record, dict):
                continue
            if record.get("enforced") or record.get("cleared"):
                continue

            user_id_int = safe_int(uid)
            if user_id_int is None:
                continue

            # Determine why this record would be skipped
            reason = "WOULD BE ENFORCED"

            msg_count = int(record.get("nonexcluded_messages", 0))
            if msg_count > max_messages:
                reason = f"above_threshold ({msg_count} msgs > {max_messages})"
                samples.append(f"`{uid}`: {reason}")
                continue

            grace_until = iso_to_dt(record.get("grace_until"))
            if grace_until and now < grace_until:
                reason = f"grace_period (until {grace_until.strftime('%Y-%m-%d')})"
                samples.append(f"`{uid}`: {reason}")
                continue

            joined_at = iso_to_dt(record.get("joined_at"))
            if baseline_date:
                baseline = baseline_date
                if joined_at and joined_at > baseline_date:
                    baseline = joined_at
            else:
                baseline = joined_at or iso_to_dt(
                    state.storage.lock_data.get("initialized_at")
                )

            if baseline is None:
                reason = "no_baseline (no joined_at or baseline_date)"
                samples.append(f"`{uid}`: {reason}")
                continue

            last_message = iso_to_dt(record.get("last_message_at"))
            delta = now - (last_message or baseline)
            if delta < dt.timedelta(days=threshold_days):
                days_inactive = delta.days
                reason = f"below_inactive_threshold ({days_inactive}d < {threshold_days}d)"
                samples.append(f"`{uid}`: {reason}")
                continue

            # Check member
            member = guild.get_member(user_id_int)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id_int)
                    await asyncio.sleep(0.05)
                except discord.NotFound:
                    reason = "member_left (not in server)"
                    samples.append(f"`{uid}`: {reason}")
                    continue
                except discord.HTTPException:
                    reason = "member_not_found (API error)"
                    samples.append(f"`{uid}`: {reason}")
                    continue

            if state.is_exempt(member):
                reason = f"exempt ({member.display_name})"
                samples.append(f"`{uid}`: {reason}")
                continue

            reason = f"WOULD BE ENFORCED ({member.display_name}, {delta.days}d inactive)"
            samples.append(f"`{uid}`: {reason}")

    if not samples:
        await message.reply(
            "No uncleared/unenforced records found to diagnose.",
            mention_author=False,
        )
        return

    header = (
        f"**Inactivity Diagnosis** (sampled {len(samples)} uncleared records)\n"
        f"Threshold: {threshold_days}d | Msg threshold: {max_messages} | "
        f"Baseline: {baseline_date_str or 'not set'}\n\n"
    )
    body = "\n".join(samples)

    # Truncate if too long for Discord
    full = header + body
    if len(full) > 1900:
        full = full[:1900] + "\n... (truncated)"

    await message.reply(full, mention_author=False)


async def run_enforcement_step(
    bot: "DiscBot",
    state: "GuildState",
    guild: discord.Guild,
) -> tuple[int, int, dict[str, int]]:
    """
    Run one batch of inactivity enforcement.

    Returns (enforced_count, scanned_count, skip_reasons).
    """
    now = utcnow()
    threshold_days = int(state.config.get(K.INACTIVE_DAYS_THRESHOLD, 0))
    max_scan = int(state.config.get(K.ENFORCEMENT_SCAN_MAX_USERS_PER_RUN, 0))
    max_messages = int(state.config.get(K.INACTIVITY_MESSAGE_THRESHOLD, 3))

    # Get baseline from module data
    inactivity_data = await get_state(guild.id)
    baseline_date_str = inactivity_data.get("baseline_date")
    baseline_date = iso_to_dt(baseline_date_str) if baseline_date_str else None

    skip_reasons: dict[str, int] = {
        "cleared": 0,
        "above_threshold": 0,
        "grace_period": 0,
        "no_baseline": 0,
        "below_inactive_threshold": 0,
        "member_not_found": 0,
        "member_left": 0,
        "exempt": 0,
    }

    cursor = state.storage.state_data.get(
        "enforcement_cursor", {"shard": "00", "after": None}
    )
    start_shard = cursor.get("shard", "00")
    after = cursor.get("after")
    after_int = safe_int(after) if after else None

    shards = [f"{i:02d}" for i in range(100)]
    if start_shard in shards:
        idx = shards.index(start_shard)
        shards = shards[idx:] + shards[:idx]

    scanned = 0
    enforced = 0
    last_scanned_user: Optional[str] = None
    last_scanned_shard: str = start_shard

    bot_member = guild.get_member(bot.user.id) if bot.user else None
    bot_top_role = bot_member.top_role if bot_member else None

    for shard in shards:
        data = await state.storage._read_shard_file(state.storage.shard_path(shard))
        parsed_ids: list[tuple[int, str]] = []
        for uid in data.keys():
            uid_int = safe_int(uid)
            if uid_int is not None:
                parsed_ids.append((uid_int, uid))
        parsed_ids.sort(key=lambda item: item[0])

        for user_id_int, user_id in parsed_ids:
            # Skip users we've already processed in this shard
            if (
                shard == start_shard
                and after_int is not None
                and user_id_int is not None
                and user_id_int <= after_int
            ):
                continue
            if scanned >= max_scan:
                break

            record = data.get(user_id)
            if not isinstance(record, dict):
                # Still update cursor even for invalid records
                last_scanned_user = user_id
                last_scanned_shard = shard
                continue

            scanned += 1
            last_scanned_user = user_id
            last_scanned_shard = shard

            if record.get("enforced") or record.get("cleared"):
                skip_reasons["cleared"] += 1
                continue
            if int(record.get("nonexcluded_messages", 0)) > max_messages:
                skip_reasons["above_threshold"] += 1
                continue

            # Check grace period (per-user grace_until in record)
            grace_until = iso_to_dt(record.get("grace_until"))
            if grace_until and now < grace_until:
                skip_reasons["grace_period"] += 1
                continue

            # Determine baseline: use baseline_date if set, else joined_at, else initialized_at
            joined_at = iso_to_dt(record.get("joined_at"))
            if baseline_date:
                # Use baseline date as the "join date" for enforcement calculation
                baseline = baseline_date
                # If user joined after baseline, use their actual join date
                if joined_at and joined_at > baseline_date:
                    baseline = joined_at
            else:
                # No baseline set, use joined_at or system initialized_at
                baseline = joined_at or iso_to_dt(
                    state.storage.lock_data.get("initialized_at")
                )
            
            if baseline is None:
                skip_reasons["no_baseline"] += 1
                continue

            last_message = iso_to_dt(record.get("last_message_at"))
            delta = now - (last_message or baseline)
            if delta < dt.timedelta(days=threshold_days):
                skip_reasons["below_inactive_threshold"] += 1
                continue

            # Try cache first, then API fetch for cache miss
            member = guild.get_member(user_id_int)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id_int)
                    await asyncio.sleep(0.05)  # rate limit courtesy
                except discord.NotFound:
                    skip_reasons["member_left"] += 1
                    continue
                except discord.HTTPException:
                    skip_reasons["member_not_found"] += 1
                    continue
            if state.is_exempt(member):
                skip_reasons["exempt"] += 1
                continue

            result = await state.enforcement.enforce_member(
                member,
                bot_top_role,
                reason="inactivity",
            )

            await state.storage.mark_enforced(member.id)
            state.record_action("inactivity")

            log_text = state.enforcement.format_action_log(
                member, result, action="inactivity"
            )
            await bot._post_action_log(state, log_text)
            enforced += 1

        if scanned >= max_scan:
            break
        # Move to next shard: reset the after filter
        after = None
        after_int = None

    # Update cursor with the last position we examined
    if last_scanned_user:
        await state.storage.update_state(
            lambda s: s.update(
                {"enforcement_cursor": {"shard": last_scanned_shard, "after": last_scanned_user}}
            )
        )
    elif scanned == 0:
        # No users scanned at all - we've completed all shards, reset to beginning
        await state.storage.update_state(
            lambda s: s.update({"enforcement_cursor": {"shard": "00", "after": None}})
        )

    logger.info(
        "Enforcement step for guild %s: scanned=%d enforced=%d skips=%s",
        guild.id, scanned, enforced, skip_reasons,
    )

    return enforced, scanned, skip_reasons


async def restore_state(bot: "DiscBot") -> None:
    """
    Restore inactivity state for all guilds on bot startup.

    This just logs which guilds have inactivity enabled.
    The actual enforcement loop is controlled separately.
    """
    register_help()
     
    for guild_id, state in bot.guild_states.items():
        try:
            data = await get_state(guild_id)
            if data.get("enabled"):
                logger.info(
                    "Inactivity enforcement enabled for guild %s",
                    guild_id,
                )
        except Exception as e:
            logger.error(
                "Failed to restore inactivity state for guild %s: %s",
                guild_id,
                e,
            )
