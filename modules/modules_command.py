"""
Modules command - manage module permissions and role-based access control.

Allows admins to enable/disable modules and control which roles can use them.
All data is guild-specific with no cross-guild data leaking.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

import discord

from core.config import OWNER_ID
from core.permissions import (
    AVAILABLE_COMMANDS,
    AVAILABLE_MODULES,
    DEFAULT_MODULE_ENABLED,
    add_role_to_command,
    add_role_to_module,
    can_use_command,
    can_use_module,
    get_command_roles,
    get_guild_permissions,
    get_module_roles,
    is_module_enabled,
    remove_role_from_command,
    remove_role_from_module,
    set_module_enabled,
)
from core.help_system import help_system
from core.command_registry import command_registry, CommandRoute
from services.hot_reload_service import (
    check_and_update_once as check_hot_reload_now,
    pause as pause_hot_reload,
    resume as resume_hot_reload,
    status as hot_reload_status,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("discbot.modules_command")

COMMAND_PATTERN = re.compile(r"^modules\s+(\w+)(?:\s+(.*))?$", re.IGNORECASE)

SUBCOMMANDS = {
    "list", "enable", "disable", "permissions", "allow", "deny", "help", "reload"
}


def register_help() -> None:
    """Register help for modules command."""
    help_system.register_module(
        name="Module Management",
        description="Control which modules are enabled and which roles can use them. All settings are per-guild.",
        help_command="modules help",
        commands=[
            ("modules list", "Show all modules and their status"),
            ("modules enable <module>", "Enable a module for this guild"),
            ("modules disable <module>", "Disable a module for this guild"),
            ("modules permissions <module|command>", "Show which roles can use a module/command"),
            ("modules allow <module|command> <role_id>", "Grant a role access to module/command"),
            ("modules deny <module|command> <role_id>", "Revoke role access to module/command"),
            ("modules help", "Show detailed module management help"),
            ("modules reload status", "Show hot-reload updater status (owner only)"),
            ("modules reload now", "Run one immediate git check + apply cycle (owner only)"),
            ("modules reload pause", "Pause automatic hot-reload polling (owner only)"),
            ("modules reload resume", "Resume automatic hot-reload polling (owner only)"),
        ]
    )

    command_registry.register(CommandRoute(
        name="modules_command",
        roots=["modules"],
        handler=handle_command,
        needs_bot=False,
    ))


async def handle_command(message: discord.Message) -> bool:
    """
    Handle the modules command.
    
    All operations are guild-specific. No data leaks between guilds.
    
    Returns True if the command was handled.
    """
    content = message.content.strip()
    
    # Check if it's the modules command
    if not content.lower().startswith("modules"):
        return False
    
    # Must be in a guild
    if not message.guild:
        return False
    
    # Parse subcommand
    match = COMMAND_PATTERN.match(content)
    if not match:
        await _cmd_help(message)
        return True
    
    subcommand = match.group(1).lower()
    args = match.group(2) or ""
    
    if subcommand not in SUBCOMMANDS:
        await message.reply(
            f"Unknown subcommand: `{subcommand}`\\nUse `modules help` for available commands.",
            mention_author=False,
        )
        return True

    if subcommand == "reload":
        await _cmd_reload(message, args)
        return True

    # Non-reload subcommands remain admin-only.
    if not isinstance(message.author, discord.Member):
        return False
    if not message.author.guild_permissions.administrator:
        await message.reply(
            "You need Administrator permission to use this command.",
            mention_author=False,
        )
        return True
    
    # Route to subcommand handler
    if subcommand == "list":
        await _cmd_list(message)
    elif subcommand == "enable":
        await _cmd_enable(message, args)
    elif subcommand == "disable":
        await _cmd_disable(message, args)
    elif subcommand == "permissions":
        await _cmd_permissions(message, args)
    elif subcommand == "allow":
        await _cmd_allow(message, args)
    elif subcommand == "deny":
        await _cmd_deny(message, args)
    elif subcommand == "help":
        await _cmd_help(message)
    
    return True


async def _cmd_reload(message: discord.Message, args: str) -> None:
    """Owner-only controls for git hot reload service."""
    if message.author.id != OWNER_ID:
        await message.reply(
            "Only the bot owner can use `modules reload` commands.",
            mention_author=False,
        )
        return

    action = (args.strip().split()[0].lower() if args.strip() else "status")

    if action == "status":
        state = hot_reload_status()
        await message.reply(
            "\n".join([
                "**Hot Reload Status**",
                f"Enabled: `{state.get('enabled')}`",
                f"Running: `{state.get('running')}`",
                f"Paused: `{state.get('paused')}`",
                f"Remote: `{state.get('remote')}/{state.get('branch')}`",
                f"Poll Seconds: `{state.get('poll_seconds')}`",
                f"Protected Halt: `{state.get('protected_halt')}`",
                f"Last Check: `{state.get('last_check_at')}`",
                f"Last Local Head: `{state.get('last_local_head')}`",
                f"Last Remote Head: `{state.get('last_remote_head')}`",
                f"Last Result: `{state.get('last_result')}`",
            ]),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if action == "now":
        result = await check_hot_reload_now()
        await message.reply(
            "\n".join([
                "**Hot Reload Manual Check**",
                f"Status: `{result.get('status')}`",
                f"Detail: `{result.get('detail', '')}`",
                f"Local Head: `{result.get('local_head')}`",
                f"Remote Head: `{result.get('remote_head')}`",
                f"Reload Success: `{result.get('reload_success')}`",
            ]),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if action == "pause":
        pause_hot_reload()
        await message.reply("Hot reload polling paused.", mention_author=False)
        return

    if action == "resume":
        resume_hot_reload()
        await message.reply("Hot reload polling resumed.", mention_author=False)
        return

    await message.reply(
        "Usage: `modules reload <status|now|pause|resume>`",
        mention_author=False,
    )


async def _cmd_list(message: discord.Message) -> None:
    """Show all modules and their status for this guild."""
    guild_id = message.guild.id
    perms = await get_guild_permissions(guild_id)
    
    lines = [
        "**Module Status** (Guild-Specific)",
        "",
        "**Available Modules:**"
    ]
    
    # Load config once instead of per-module to avoid N disk reads
    from core.config_migration import get_guild_module_data
    guild_config = await get_guild_module_data(guild_id)
    modules_config = guild_config.get("modules", {})
    
    for module, description in AVAILABLE_MODULES.items():
        module_data = modules_config.get(module, {})
        enabled = module_data.get("enabled", module not in DEFAULT_MODULE_ENABLED or DEFAULT_MODULE_ENABLED[module])
        status = "Enabled" if enabled else "Disabled"
        
        role_ids = module_data.get("allowed_roles", [])
        if role_ids:
            roles_text = f" - Roles: {', '.join(str(rid) for rid in role_ids)}"
        else:
            roles_text = " - Admin only"
        
        lines.append(f"• **{module}** - {status}{roles_text}")
        lines.append(f"  _{description}_")
    
    lines.append("")
    lines.append("Use `modules permissions <module>` to see command permissions.")
    
    await message.reply(
        "\\n".join(lines),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _cmd_enable(message: discord.Message, args: str) -> None:
    """Enable a module for this guild."""
    module = args.strip().lower()
    
    if not module:
        await message.reply(
            "Please specify a module to enable.\\n"
            "Use `modules list` to see available modules.",
            mention_author=False,
        )
        return
    
    if module not in AVAILABLE_MODULES:
        await message.reply(
            f"Unknown module: `{module}`\\n"
            f"Available modules: {', '.join(f'`{m}`' for m in AVAILABLE_MODULES.keys())}",
            mention_author=False,
        )
        return
    
    success = await set_module_enabled(message.guild.id, module, True)
    if success:
        await message.reply(
            f"Module `{module}` has been enabled for this guild.",
            mention_author=False,
        )
        logger.info(
            "Module %s enabled in guild %s by user %s",
            module,
            message.guild.id,
            message.author.id,
        )
    else:
        await message.reply(
            f"Failed to enable module `{module}`.",
            mention_author=False,
        )


async def _cmd_disable(message: discord.Message, args: str) -> None:
    """Disable a module for this guild."""
    module = args.strip().lower()
    
    if not module:
        await message.reply(
            "Please specify a module to disable.\\n"
            "Use `modules list` to see available modules.",
            mention_author=False,
        )
        return
    
    if module not in AVAILABLE_MODULES:
        await message.reply(
            f"Unknown module: `{module}`\\n"
            f"Available modules: {', '.join(f'`{m}`' for m in AVAILABLE_MODULES.keys())}",
            mention_author=False,
        )
        return
    
    success = await set_module_enabled(message.guild.id, module, False)
    if success:
        await message.reply(
            f"Module `{module}` has been disabled for this guild.",
            mention_author=False,
        )
        logger.info(
            "Module %s disabled in guild %s by user %s",
            module,
            message.guild.id,
            message.author.id,
        )
    else:
        await message.reply(
            f"Failed to disable module `{module}`.",
            mention_author=False,
        )


async def _cmd_permissions(message: discord.Message, args: str) -> None:
    """Show role permissions for a module or command."""
    target = args.strip().lower()
    
    if not target:
        await message.reply(
            "Please specify a module or command.\\n"
            "Example: `modules permissions scanner`",
            mention_author=False,
        )
        return
    
    guild_id = message.guild.id
    
    # Check if it's a module
    if target in AVAILABLE_MODULES:
        enabled = await is_module_enabled(guild_id, target)
        role_ids = await get_module_roles(guild_id, target)
        
        lines = [
            f"**Permissions for Module: {target}**",
            f"Status: {'Enabled' if enabled else 'Disabled'}",
            ""
        ]
        
        if role_ids:
            lines.append("**Allowed Roles:**")
            for role_id in role_ids:
                role = message.guild.get_role(role_id)
                if role:
                    lines.append(f"• {role.mention} (`{role_id}`)")
                else:
                    lines.append(f"• Unknown Role (`{role_id}`)")
        else:
            lines.append("**Allowed Roles:** None (Admin only)")
        
        # Show commands in this module
        if target in AVAILABLE_COMMANDS:
            lines.append("")
            lines.append("**Commands in this module:**")
            for cmd in AVAILABLE_COMMANDS[target]:
                lines.append(f"• `{cmd}`")
        
        await message.reply(
            "\\n".join(lines),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    else:
        # Check if it's a command
        role_ids = await get_command_roles(guild_id, target)
        
        lines = [
            f"**Permissions for Command: {target}**",
            ""
        ]
        
        if role_ids:
            lines.append("**Allowed Roles:**")
            for role_id in role_ids:
                role = message.guild.get_role(role_id)
                if role:
                    lines.append(f"• {role.mention} (`{role_id}`)")
                else:
                    lines.append(f"• Unknown Role (`{role_id}`)")
        else:
            lines.append("**Allowed Roles:** None (Admin only)")
        
        await message.reply(
            "\\n".join(lines),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _cmd_allow(message: discord.Message, args: str) -> None:
    """Allow a role to use a module or command."""
    # Parse: <module/command> <@role>
    parts = args.split()
    if len(parts) < 2:
        await message.reply(
            "**Invalid format.**\\n"
            "```\\nmodules allow <module|command> <role_id>\\n```\\n"
            "Example: `modules allow scanner 123456789`",
            mention_author=False,
        )
        return
    
    target = parts[0].lower()
    
    # Extract role from mention or ID
    role: Optional[discord.Role] = None
    if message.role_mentions:
        role = message.role_mentions[0]
    else:
        # Try to parse as ID
        role_str = parts[1].strip("<@&>")
        if role_str.isdigit():
            role = message.guild.get_role(int(role_str))
    
    if not role:
        await message.reply(
            "Could not find that role. Please mention a role or provide a role ID.",
            mention_author=False,
        )
        return
    
    # Check if it's a module
    if target in AVAILABLE_MODULES:
        success = await add_role_to_module(message.guild.id, target, role.id)
        if success:
            await message.reply(
                f"Role `{role.name}` ({role.id}) can now use module `{target}`",
                mention_author=False,
            )
            logger.info(
                "Role %s added to module %s in guild %s by user %s",
                role.id,
                target,
                message.guild.id,
                message.author.id,
            )
        else:
            await message.reply(
                f"Failed to add role to module `{target}`.",
                mention_author=False,
            )
    else:
        # Treat as command
        success = await add_role_to_command(message.guild.id, target, role.id)
        if success:
            await message.reply(
                f"Role `{role.name}` ({role.id}) can now use command `{target}`",
                mention_author=False,
            )
            logger.info(
                "Role %s added to command %s in guild %s by user %s",
                role.id,
                target,
                message.guild.id,
                message.author.id,
            )
        else:
            await message.reply(
                f"Failed to add role to command `{target}`.",
                mention_author=False,
            )


async def _cmd_deny(message: discord.Message, args: str) -> None:
    """Remove a role's permission to use a module or command."""
    # Parse: <module/command> <role_id>
    parts = args.split()
    if len(parts) < 2:
        await message.reply(
            "**Invalid format.**\\n"
            "```\\nmodules deny <module|command> <role_id>\\n```\\n"
            "Example: `modules deny scanner 123456789`",
            mention_author=False,
        )
        return
    
    target = parts[0].lower()
    
    # Extract role from mention or ID
    role: Optional[discord.Role] = None
    if message.role_mentions:
        role = message.role_mentions[0]
    else:
        # Try to parse as ID
        role_str = parts[1].strip("<@&>")
        if role_str.isdigit():
            role = message.guild.get_role(int(role_str))
    
    if not role:
        await message.reply(
            "Could not find that role. Please mention a role or provide a role ID.",
            mention_author=False,
        )
        return
    
    # Check if it's a module
    if target in AVAILABLE_MODULES:
        success = await remove_role_from_module(message.guild.id, target, role.id)
        if success:
            await message.reply(
                f"Role `{role.name}` ({role.id}) can no longer use module `{target}`",
                mention_author=False,
            )
            logger.info(
                "Role %s removed from module %s in guild %s by user %s",
                role.id,
                target,
                message.guild.id,
                message.author.id,
            )
        else:
            await message.reply(
                f"Role was not in the allowed list for module `{target}`.",
                mention_author=False,
            )
    else:
        # Treat as command
        success = await remove_role_from_command(message.guild.id, target, role.id)
        if success:
            await message.reply(
                f"Role `{role.name}` ({role.id}) can no longer use command `{target}`",
                mention_author=False,
            )
            logger.info(
                "Role %s removed from command %s in guild %s by user %s",
                role.id,
                target,
                message.guild.id,
                message.author.id,
            )
        else:
            await message.reply(
                f"Role was not in the allowed list for command `{target}`.",
                mention_author=False,
            )


async def _cmd_help(message: discord.Message) -> None:
    """Show detailed help for modules command using the help system."""
    embed = help_system.get_module_embed("Module Management")
    if embed is None:
        await message.reply("Help not available.", mention_author=False)
        return
    await message.reply(embed=embed, mention_author=False)
