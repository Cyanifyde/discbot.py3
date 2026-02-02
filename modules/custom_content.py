"""
Custom Content module - custom commands, forms, and enhanced responses.

Provides guild-specific custom commands, form builder, and advanced auto-responses.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import is_module_enabled
from core.custom_content_storage import CustomContentStore

logger = logging.getLogger("discbot.custom_content")

MODULE_NAME = "custom_content"


def setup_custom_content() -> None:
    """Register help information for the custom content module."""
    help_system.register_module(
        name="Custom Content",
        description="Custom commands, forms, and enhanced auto-responses.",
        help_command="custom help",
        commands=[
            ("customcmd add <name> <response>", "Add custom command (mod only)"),
            ("customcmd remove <name>", "Remove custom command (mod only)"),
            ("customcmd list", "List custom commands"),
            ("form create <name> <field1> <field2>...", "Create form (mod only)"),
            ("form list", "List forms"),
            ("form submit <form_name>", "Submit a form"),
            ("form responses <form_name>", "View form responses (mod only)"),
        ],
    )


async def handle_custom_content_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle custom content commands.

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

    # Route to handlers
    if command == "customcmd":
        await _handle_customcmd(message, parts)
        return True
    elif command == "form":
        await _handle_form(message, parts, bot)
        return True

    # Check if it's a custom command
    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    custom_cmd = await store.get_custom_command(command)
    if custom_cmd:
        await _execute_custom_command(message, custom_cmd)
        await store.increment_command_usage(command)
        return True

    return False


# ─── Custom Commands ──────────────────────────────────────────────────────────


async def _handle_customcmd(message: discord.Message, parts: list[str]) -> None:
    """Handle custom command management."""
    if len(parts) < 2:
        await message.reply(" Usage: `customcmd <add|remove|list>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "add":
        await _handle_customcmd_add(message, parts)
    elif subcommand == "remove":
        await _handle_customcmd_remove(message, parts)
    elif subcommand == "list":
        await _handle_customcmd_list(message)
    else:
        await message.reply(" Usage: `customcmd <add|remove|list>`")


async def _handle_customcmd_add(message: discord.Message, parts: list[str]) -> None:
    """Add a custom command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to add custom commands.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `customcmd add <name> <response>`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `customcmd add <name> <response>`")
        return

    cmd_name = args[0].lower()
    response = args[1]

    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    command = await store.add_custom_command(cmd_name, response)

    await message.reply(
        f" Custom command created!\n"
        f"**Name:** `{cmd_name}`\n"
        f"**Usage:** `{cmd_name}`"
    )


async def _handle_customcmd_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove a custom command."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to remove custom commands.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `customcmd remove <name>`")
        return

    cmd_name = parts[2].lower()

    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    success = await store.remove_custom_command(cmd_name)

    if success:
        await message.reply(f" Custom command `{cmd_name}` removed")
    else:
        await message.reply(f" No custom command found with name `{cmd_name}`")


async def _handle_customcmd_list(message: discord.Message) -> None:
    """List custom commands."""
    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    commands = await store.get_all_custom_commands()

    if not commands:
        await message.reply(" No custom commands configured")
        return

    embed = discord.Embed(
        title="Custom Commands",
        description=f"Total: {len(commands)}",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )

    for name, cmd in list(commands.items())[:15]:
        response_preview = cmd["response"][:100]
        if len(cmd["response"]) > 100:
            response_preview += "..."

        value = (
            f"**Response:** {response_preview}\n"
            f"**Uses:** {cmd.get('use_count', 0)}"
        )

        embed.add_field(
            name=f"`{name}`",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _execute_custom_command(
    message: discord.Message,
    command: dict,
) -> None:
    """Execute a custom command."""
    response = command["response"]

    # Simple variable substitution
    response = response.replace("{user}", message.author.mention)
    response = response.replace("{server}", message.guild.name)
    response = response.replace("{channel}", message.channel.mention)

    if command.get("embed_data"):
        # Send as embed
        embed_data = command["embed_data"]
        embed = discord.Embed(
            title=embed_data.get("title", ""),
            description=embed_data.get("description", response),
            color=discord.Color.from_str(embed_data.get("color", "#5865F2")),
        )
        await message.reply(embed=embed)
    else:
        # Send as plain text
        await message.reply(response)


# ─── Forms ────────────────────────────────────────────────────────────────────


async def _handle_form(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle form commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `form <create|list|submit|responses>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "create":
        await _handle_form_create(message, parts)
    elif subcommand == "list":
        await _handle_form_list(message)
    elif subcommand == "submit":
        await _handle_form_submit(message, parts, bot)
    elif subcommand == "responses":
        await _handle_form_responses(message, parts)
    else:
        await message.reply(" Usage: `form <create|list|submit|responses>`")


async def _handle_form_create(message: discord.Message, parts: list[str]) -> None:
    """Create a form."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to create forms.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `form create <name> <field1> <field2>...`")
        return

    args = parts[2].split()
    if len(args) < 2:
        await message.reply(" Usage: `form create <name> <field1> <field2>...`")
        return

    form_name = args[0]
    field_names = args[1:]

    # Create fields
    fields = []
    for field_name in field_names:
        fields.append({
            "name": field_name,
            "type": "text",
            "required": True,
        })

    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    form_id = str(uuid.uuid4())
    submit_channel_id = message.channel.id if message.channel_mentions else None

    form = await store.add_form(form_id, form_name, fields, submit_channel_id)

    fields_str = ", ".join(f"`{f['name']}`" for f in fields)
    await message.reply(
        f" Form created! ID: `{form_id[:8]}`\n"
        f"**Name:** {form_name}\n"
        f"**Fields:** {fields_str}\n"
        f"**Usage:** `form submit {form_name}`"
    )


async def _handle_form_list(message: discord.Message) -> None:
    """List forms."""
    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    forms = await store.get_all_forms()

    if not forms:
        await message.reply(" No forms configured")
        return

    embed = discord.Embed(
        title="Forms",
        description=f"Total: {len(forms)}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for form_id, form in list(forms.items())[:10]:
        fields_str = ", ".join(f"`{f['name']}`" for f in form["fields"])

        embed.add_field(
            name=form["name"],
            value=f"**Fields:** {fields_str}\n**ID:** `{form_id[:8]}`",
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_form_submit(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle form submission."""
    if len(parts) < 3:
        await message.reply(" Usage: `form submit <form_name>`")
        return

    form_name = parts[2]

    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    form = await store.get_form(form_name)
    if not form:
        await message.reply(f" No form found with name `{form_name}`")
        return

    # Create modal for form submission
    class FormModal(discord.ui.Modal, title=form["name"]):
        def __init__(self, form_data):
            super().__init__()
            self.form_data = form_data

            # Add fields to modal (max 5)
            for field in form_data["fields"][:5]:
                text_input = discord.ui.TextInput(
                    label=field["name"],
                    style=discord.TextStyle.short,
                    required=field.get("required", True),
                )
                self.add_item(text_input)

        async def on_submit(self, interaction: discord.Interaction):
            # Collect responses
            responses = {}
            for item in self.children:
                responses[item.label] = item.value

            # Store submission
            submission_id = str(uuid.uuid4())
            await store.add_form_submission(
                submission_id,
                self.form_data["id"],
                interaction.user.id,
                responses,
            )

            await interaction.response.send_message(
                f" Form submitted! ID: `{submission_id[:8]}`",
                ephemeral=True,
            )

    await message.reply(
        f" Opening form: **{form['name']}**\n"
        "Note: Full modal implementation requires interaction context."
    )


async def _handle_form_responses(message: discord.Message, parts: list[str]) -> None:
    """View form responses."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view form responses.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `form responses <form_name>`")
        return

    form_name = parts[2]

    guild_id = message.guild.id
    store = CustomContentStore(guild_id)
    await store.initialize()

    form = await store.get_form(form_name)
    if not form:
        await message.reply(f" No form found with name `{form_name}`")
        return

    submissions = await store.get_form_submissions(form["id"])

    if not submissions:
        await message.reply(f" No submissions for form **{form['name']}**")
        return

    embed = discord.Embed(
        title=f"Form Responses: {form['name']}",
        description=f"Total submissions: {len(submissions)}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for submission in submissions[:5]:
        responses_str = "\n".join(
            f"**{k}:** {v[:50]}"
            for k, v in submission["responses"].items()
        )

        embed.add_field(
            name=f"Submission `{submission['id'][:8]}`",
            value=f"**From:** <@{submission['user_id']}>\n{responses_str}",
            inline=False,
        )

    await message.reply(embed=embed)
