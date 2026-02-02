"""
Verification module - handles role-based verification via button clicks.

Admins can set up a verification message with a button that users can click
to get verified (receive verified role, lose unverified role).

Text command format:
    addverification #channel "message text" unverified_role_id verified_role_id

Verification data is stored in guild config and survives bot restarts.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import discord
from discord.ui import Button, View

from core.config_migration import get_guild_module_data, update_guild_module_data
from core.interactions import register_component_handler
from core.help_system import help_system
from core.permissions import can_use_command, can_use_module, is_module_enabled

logger = logging.getLogger("discbot.verification")

# Module name for config storage
MODULE_NAME = "verification"

# Custom ID prefix for verification buttons
VERIFY_BUTTON_PREFIX = "verify_btn:"

# Regex to parse the addverification command
# addverification <#channel_id> "text" unverified_role_id verified_role_id
ADD_COMMAND_PATTERN = re.compile(
    r"^addverification\s+"
    r"<#(\d+)>\s+"           # Channel mention
    r'"([^"]+)"\s+'          # Quoted text
    r"(\d+)\s+"              # Unverified role ID
    r"(\d+)\s*$",            # Verified role ID
    re.IGNORECASE
)

# Regex to parse the removeverification command
# removeverification message_id (works from any channel now)
# OR removeverification <#channel_id> message_id (old format still supported)
REMOVE_COMMAND_PATTERN = re.compile(
    r"^removeverification\s+"
    r"(?:<#(\d+)>\s+)?(\d+)\s*$",  # Optional channel mention, then message ID
    re.IGNORECASE
)


class VerifyButton(Button):
    """A button that verifies users when clicked."""
    
    def __init__(
        self,
        unverified_role_id: int,
        verified_role_id: int,
        label: str = "Verify",
        style: discord.ButtonStyle = discord.ButtonStyle.green,
    ):
        # Store role IDs in custom_id for persistence
        custom_id = f"{VERIFY_BUTTON_PREFIX}{unverified_role_id}:{verified_role_id}"
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.unverified_role_id = unverified_role_id
        self.verified_role_id = verified_role_id


class VerifyView(View):
    """A persistent view containing the verify button."""
    
    def __init__(self, unverified_role_id: int, verified_role_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(VerifyButton(unverified_role_id, verified_role_id))


async def handle_verify_button(interaction: discord.Interaction) -> bool:
    """
    Handle a verification button click.
    
    Returns True if the interaction was handled, False otherwise.
    """
    if not interaction.data:
        return False
    
    custom_id = interaction.data.get("custom_id", "")
    if not isinstance(custom_id, str) or not custom_id.startswith(VERIFY_BUTTON_PREFIX):
        return False
    
    # Parse role IDs from custom_id
    try:
        role_data = custom_id[len(VERIFY_BUTTON_PREFIX):]
        unverified_id_str, verified_id_str = role_data.split(":")
        unverified_role_id = int(unverified_id_str)
        verified_role_id = int(verified_id_str)
    except (ValueError, IndexError):
        logger.warning("Invalid verification button custom_id: %s", custom_id)
        return False
    
    if not interaction.guild:
        await interaction.response.send_message(
            "This button only works in a server.",
            ephemeral=True,
        )
        return True
    
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        await interaction.response.send_message(
            "Could not find your member info.",
            ephemeral=True,
        )
        return True
    
    # Get roles
    verified_role = interaction.guild.get_role(verified_role_id)
    unverified_role = interaction.guild.get_role(unverified_role_id)
    
    if not verified_role:
        await interaction.response.send_message(
            "The verified role no longer exists. Please contact an admin.",
            ephemeral=True,
        )
        return True
    
    # Check if already verified
    if verified_role in member.roles:
        await interaction.response.send_message(
            "You are already verified!",
            ephemeral=True,
        )
        return True
    
    # Perform verification
    try:
        # Add verified role
        await member.add_roles(verified_role, reason="User clicked verify button")
        
        # Remove unverified role if it exists and member has it
        if unverified_role and unverified_role in member.roles:
            await member.remove_roles(unverified_role, reason="User verified")
        
        await interaction.response.send_message(
            "You have been verified!",
            ephemeral=True,
        )
        logger.info(
            "User %s (%s) verified in guild %s",
            member.name,
            member.id,
            interaction.guild.id,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to manage your roles. Please contact an admin.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        logger.error("Failed to verify user %s: %s", member.id, e)
        await interaction.response.send_message(
            "An error occurred while verifying. Please try again later.",
            ephemeral=True,
        )
    
    return True


def setup_verification() -> None:
    """Register the verification button handler with the core interaction system."""
    # Register help information
    help_system.register_module(
        name="Verification",
        description="Role-based verification via button clicks.",
        help_command="@bot help",
        commands=[
            ("addverification #channel \"message\" unverified_id verified_id", "Set up verification button"),
            ("removeverification message_id", "Remove verification button (auto-finds channel)"),
        ]
    )
    
    register_component_handler(VERIFY_BUTTON_PREFIX, handle_verify_button)


async def handle_verification_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle the addverification text command.
    
    Format: addverification #channel "text" unverified_role_id verified_role_id
    
    Returns True if the command was handled (even if it failed).
    """
    content = message.content.strip()
    
    # Check if it's the addverification command
    if not content.lower().startswith("addverification"):
        return False
    
    # Must be in a guild
    if not message.guild:
        return False
    
    # Must be an admin
    if not isinstance(message.author, discord.Member):
        return False
    
    # Check module and command permissions (guild-specific)
    if not await is_module_enabled(message.guild.id, "verification"):
        await message.reply(
            "Verification module is disabled in this server.\n"
            "An administrator can enable it with `modules enable verification`",
            mention_author=False,
        )
        return True
    
    if not await can_use_command(message.author, "addverification"):
        await message.reply(
            "You don't have permission to use this command in this server.\n"
            "An administrator can grant access with `modules allow addverification @YourRole`",
            mention_author=False,
        )
        return True
    
    if not message.author.guild_permissions.administrator:
        await message.reply(
            "You need Administrator permission to use this command.",
            mention_author=False,
        )
        return True
    
    # Parse the command
    match = ADD_COMMAND_PATTERN.match(content)
    if not match:
        await message.reply(
            "**Invalid format.**\n"
            "```\n"
            "addverification #channel \"message text\" unverified_role_id verified_role_id\n"
            "```",
            mention_author=False,
        )
        return True
    
    channel_id = int(match.group(1))
    text = match.group(2)
    unverified_role_id = int(match.group(3))
    verified_role_id = int(match.group(4))
    
    # Get channel
    channel = message.guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        await message.reply(
            "Could not find that channel or it's not a text channel.",
            mention_author=False,
        )
        return True
    
    # Get roles (for validation)
    verified_role = message.guild.get_role(verified_role_id)
    unverified_role = message.guild.get_role(unverified_role_id)
    
    if not verified_role:
        await message.reply(
            f"Could not find verified role with ID `{verified_role_id}`.",
            mention_author=False,
        )
        return True
    
    if not unverified_role:
        await message.reply(
            f"Could not find unverified role with ID `{unverified_role_id}`.",
            mention_author=False,
        )
        return True
    
    # Check bot permissions
    bot_member = message.guild.get_member(bot.user.id) if bot.user else None
    if not bot_member:
        await message.reply(
            "Could not find my member info in this server.",
            mention_author=False,
        )
        return True
    
    if not bot_member.guild_permissions.manage_roles:
        await message.reply(
            "I need the **Manage Roles** permission to handle verification.",
            mention_author=False,
        )
        return True
    
    # Check role hierarchy
    if verified_role >= bot_member.top_role:
        await message.reply(
            f"I cannot assign **{verified_role.name}** because it's at or above my highest role.",
            mention_author=False,
        )
        return True
    
    if unverified_role >= bot_member.top_role:
        await message.reply(
            f"I cannot remove **{unverified_role.name}** because it's at or above my highest role.",
            mention_author=False,
        )
        return True
    
    # Check channel permissions
    channel_perms = channel.permissions_for(bot_member)
    if not channel_perms.send_messages:
        await message.reply(
            f"I don't have permission to send messages in #{channel.name}.",
            mention_author=False,
        )
        return True
    
    # Create and send the verification message
    view = VerifyView(unverified_role_id, verified_role_id)
    
    try:
        sent_message = await channel.send(content=text, view=view)
        
        # Store verification data for persistence
        await save_verification_button(
            guild_id=message.guild.id,
            channel_id=channel.id,
            message_id=sent_message.id,
            text=text,
            unverified_role_id=unverified_role_id,
            verified_role_id=verified_role_id,
        )
        
        await message.reply(
            f"Verification button posted in #{channel.name}!",
            mention_author=False,
        )
        logger.info(
            "Verification button added in guild %s, channel %s by %s",
            message.guild.id,
            channel.id,
            message.author.id,
        )
    except discord.HTTPException as e:
        logger.error("Failed to send verification message: %s", e)
        await message.reply(
            "Failed to send the verification message. Please check my permissions.",
            mention_author=False,
        )
    
    return True


async def handle_remove_verification_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle the removeverification text command.
    
    Format: removeverification #channel message_id
    
    Returns True if the command was handled (even if it failed).
    """
    content = message.content.strip()
    
    # Check if it's the removeverification command
    if not content.lower().startswith("removeverification"):
        return False
    
    # Must be in a guild
    if not message.guild:
        return False
    
    # Must be an admin
    if not isinstance(message.author, discord.Member):
        return False
    
    # Check module and command permissions (guild-specific)
    if not await is_module_enabled(message.guild.id, "verification"):
        await message.reply(
            "Verification module is disabled in this server.\n"
            "An administrator can enable it with `modules enable verification`",
            mention_author=False,
        )
        return True
    
    if not await can_use_command(message.author, "removeverification"):
        await message.reply(
            "You don't have permission to use this command in this server.\n"
            "An administrator can grant access with `modules allow removeverification @YourRole`",
            mention_author=False,
        )
        return True
    
    if not message.author.guild_permissions.administrator:
        await message.reply(
            "You need Administrator permission to use this command.",
            mention_author=False,
        )
        return True
    
    # Parse the command
    match = REMOVE_COMMAND_PATTERN.match(content)
    if not match:
        await message.reply(
            "**Invalid format.**\\n"
            "```\\n"
            "removeverification message_id\\n"
            "```\\n"
            "The bot will automatically find which channel the message is in.",
            mention_author=False,
        )
        return True
    
    channel_id_str = match.group(1)  # Optional channel mention
    message_id = int(match.group(2))
    
    # Try to find the verification button in module memory first
    data = await get_guild_module_data(message.guild.id, MODULE_NAME)
    if not isinstance(data, dict):
        data = {}
    
    buttons = data.get("buttons", {})
    if not isinstance(buttons, dict):
        buttons = {}
    
    # Get channel from memory or command parameter
    target_channel = None
    if str(message_id) in buttons:
        # Found in memory
        button_info = buttons[str(message_id)]
        stored_channel_id = button_info.get("channel_id")
        if stored_channel_id:
            target_channel = message.guild.get_channel(stored_channel_id)
    
    # Fallback to channel parameter if provided
    if not target_channel and channel_id_str:
        target_channel = message.guild.get_channel(int(channel_id_str))
    
    # If still not found, search all channels (last resort)
    if not target_channel:
        await message.reply(
            "Searching for the verification message...",
            mention_author=False,
        )
        for channel in message.guild.text_channels:
            try:
                test_message = await channel.fetch_message(message_id)
                target_channel = channel
                break
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
    
    if not target_channel:
        await message.reply(
            f"Could not find message `{message_id}` in any channel.\\n"
            f"It may have already been deleted.",
            mention_author=False,
        )
        # Clean up from memory anyway
        await remove_verification_button(message.guild.id, message_id)
        return True
    
    if not isinstance(target_channel, discord.TextChannel):
        await message.reply(
            "That channel is not a text channel.",
            mention_author=False,
        )
        return True
    
    # Try to delete the message
    try:
        target_message = await target_channel.fetch_message(message_id)
        await target_message.delete()
    except discord.NotFound:
        # Message already deleted, that's fine
        pass
    except discord.Forbidden:
        await message.reply(
            "I don't have permission to delete that message.",
            mention_author=False,
        )
        return True
    except discord.HTTPException as e:
        logger.error("Failed to delete verification message: %s", e)
        await message.reply(
            "Failed to delete the message. Please try again.",
            mention_author=False,
        )
        return True
    
    # Remove from config
    removed = await remove_verification_button(message.guild.id, message_id)
    
    if removed:
        await message.reply(
            f"Verification button removed from {target_channel.mention}!",
            mention_author=False,
        )
        logger.info(
            "Verification button removed in guild %s, channel %s, message %s by %s",
            message.guild.id,
            target_channel.id,
            message_id,
            message.author.id,
        )
    else:
        await message.reply(
            f"Message deleted. (Note: It wasn't in my saved verification list.)",
            mention_author=False,
        )
    
    return True


# ─── Persistence Functions ────────────────────────────────────────────────────


async def get_verification_data(guild_id: int) -> Dict[str, Any]:
    """Get all verification data for a guild."""
    data = await get_guild_module_data(guild_id, MODULE_NAME)
    if data is None:
        return {"buttons": []}
    return data


async def save_verification_button(
    guild_id: int,
    channel_id: int,
    message_id: int,
    text: str,
    unverified_role_id: int,
    verified_role_id: int,
) -> None:
    """Save a verification button to the guild config."""
    data = await get_verification_data(guild_id)
    
    buttons = data.get("buttons", [])
    if not isinstance(buttons, list):
        buttons = []
    
    # Add new button entry
    buttons.append({
        "channel_id": channel_id,
        "message_id": message_id,
        "text": text,
        "unverified_role_id": unverified_role_id,
        "verified_role_id": verified_role_id,
    })
    
    data["buttons"] = buttons
    await update_guild_module_data(guild_id, MODULE_NAME, data)


async def remove_verification_button(guild_id: int, message_id: int) -> bool:
    """Remove a verification button from the guild config."""
    data = await get_verification_data(guild_id)
    
    buttons = data.get("buttons", [])
    if not isinstance(buttons, list):
        return False
    
    original_len = len(buttons)
    buttons = [b for b in buttons if b.get("message_id") != message_id]
    
    if len(buttons) == original_len:
        return False
    
    data["buttons"] = buttons
    await update_guild_module_data(guild_id, MODULE_NAME, data)
    return True


async def restore_verification_views(bot: discord.Client) -> int:
    """
    Restore all verification button views on bot startup.
    
    This re-adds the View to messages so buttons work after restart.
    Returns the number of views restored.
    """
    from core.config_migration import GUILD_CONFIG_DIR
    
    restored = 0
    
    # Find all guild configs
    if not GUILD_CONFIG_DIR.exists():
        return restored
    
    for path in GUILD_CONFIG_DIR.iterdir():
        if path.suffix != ".json" or not path.stem.isdigit():
            continue
        
        guild_id = int(path.stem)
        data = await get_verification_data(guild_id)
        buttons = data.get("buttons", [])
        
        if not buttons:
            continue
        
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        
        # Track buttons to remove (deleted messages/channels)
        to_remove: List[int] = []
        
        for button_data in buttons:
            channel_id = button_data.get("channel_id")
            message_id = button_data.get("message_id")
            unverified_role_id = button_data.get("unverified_role_id")
            verified_role_id = button_data.get("verified_role_id")
            
            if not all([channel_id, message_id, unverified_role_id, verified_role_id]):
                continue
            
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                to_remove.append(message_id)
                continue
            
            try:
                message = await channel.fetch_message(message_id)
                # Re-add the view to make buttons work
                view = VerifyView(unverified_role_id, verified_role_id)
                await message.edit(view=view)
                restored += 1
                logger.debug(
                    "Restored verification button in guild %s, channel %s",
                    guild_id,
                    channel_id,
                )
            except discord.NotFound:
                # Message was deleted
                to_remove.append(message_id)
            except discord.HTTPException as e:
                logger.warning(
                    "Failed to restore verification button in guild %s: %s",
                    guild_id,
                    e,
                )
        
        # Clean up deleted buttons
        for msg_id in to_remove:
            await remove_verification_button(guild_id, msg_id)
    
    if restored:
        logger.info("Restored %d verification button(s)", restored)
    
    return restored
