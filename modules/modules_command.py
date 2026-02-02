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

from core.permissions import (
    AVAILABLE_COMMANDS,
    AVAILABLE_MODULES,
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

if TYPE_CHECKING:
    pass

logger = logging.getLogger("discbot.modules_command")

COMMAND_PATTERN = re.compile(r"^modules\s+(\w+)(?:\s+(.*))?$", re.IGNORECASE)

SUBCOMMANDS = {
    "list", "enable", "disable", "permissions", "allow", "deny", "help"
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
        ]
    )


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
    
    # Must be an admin
    if not isinstance(message.author, discord.Member):
        return False
    
    if not message.author.guild_permissions.administrator:
        await message.reply(
            "You need Administrator permission to use this command.",
            mention_author=False,
        )
        return True
    
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


async def _cmd_list(message: discord.Message) -> None:
    """Show all modules and their status for this guild."""
    guild_id = message.guild.id
    perms = await get_guild_permissions(guild_id)
    
    lines = [
        "**Module Status** (Guild-Specific)",
        "",
        "**Available Modules:**"
    ]
    
    for module, description in AVAILABLE_MODULES.items():
        enabled = await is_module_enabled(guild_id, module)
        status = "Enabled" if enabled else "Disabled"
        
        role_ids = await get_module_roles(guild_id, module)
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
