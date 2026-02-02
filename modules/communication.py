"""
Communication module - feedback box, announcements, and acknowledgments.

Provides commands for feedback submission, commission announcements, and message acknowledgments.
"""
from __future__ import annotations

import logging
import shlex
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled
from core.utils import extract_first_message_link
from services.communication_service import communication_service

logger = logging.getLogger("discbot.communication")

MODULE_NAME = "communication"


def setup_communication() -> None:
    """Register help information for the communication module."""
    help_system.register_module(
        name="Communication",
        description="Feedback, announcements, and acknowledgment tracking.",
        help_command="communication help",
        commands=[
            ("feedback help", "Feedback box commands"),
            ("notify help", "Announcement subscription commands"),
            ("ack help", "Acknowledgment commands"),
            ("communication help", "Show this help message"),
        ],
    )

    help_system.register_module(
        name="Feedback",
        description="Anonymous feedback submission and moderation workflow.",
        help_command="feedback help",
        commands=[
            ("feedback submit <content>", "Submit anonymous feedback"),
            ("feedback list [status]", "List feedback (mod only)"),
            ("feedback view <id>", "View feedback details (mod only)"),
            ("feedback status <id> <status> [note]", "Update feedback status (mod only)"),
            ("feedback upvote <id>", "Upvote feedback"),
            ("feedback config <channel>", "Configure feedback channel (mod only)"),
            ("feedback help", "Show this help message"),
        ],
        group="Communication",
        hidden=True,
    )

    help_system.register_module(
        name="Notify",
        description="Subscribe to artists and receive announcement notifications.",
        help_command="notify help",
        commands=[
            ("notify subscribe @artist", "Subscribe to an artist's announcements"),
            ("notify unsubscribe @artist", "Unsubscribe from an artist"),
            ("notify list", "List your subscriptions"),
            ("notify channel <channel>", "Set announcement channel (mod only)"),
            ("notify help", "Show this help message"),
        ],
        group="Communication",
        hidden=True,
    )

    help_system.register_module(
        name="Acknowledgments",
        description="Create acknowledgments users must click to confirm they've read something.",
        help_command="ack help",
        commands=[
            ("ack create <message_link> <title> <description>", "Create acknowledgment (mod only)"),
            ("ack check", "Check your pending acknowledgments"),
            ("ack stats <message_link>", "View acknowledgment stats (mod only)"),
            ("ack help", "Show this help message"),
        ],
        group="Communication",
        hidden=True,
    )


async def handle_communication_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle communication-related commands.

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

    # Per-subcommand help
    if command == "communication" and len(parts) >= 2 and parts[1].lower() == "help":
        embed = help_system.get_module_help("Communication")
        if embed:
            await message.reply(embed=embed)
        else:
            await message.reply(" Help information not available.")
        return True

    if len(parts) >= 2 and parts[1].lower() == "help":
        target_map = {"feedback": "Feedback", "notify": "Notify", "ack": "Acknowledgments"}
        if command in target_map:
            embed = help_system.get_module_help(target_map[command])
            if embed:
                await message.reply(embed=embed)
            else:
                await message.reply(" Help information not available.")
            return True

    # Route to handlers
    if command == "feedback":
        await _handle_feedback(message, parts, bot)
        return True
    elif command == "notify":
        await _handle_notify(message, parts, bot)
        return True
    elif command == "ack":
        await _handle_ack(message, parts, bot)
        return True

    return False


# ─── Feedback Handlers ────────────────────────────────────────────────────────


async def _handle_feedback(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle feedback commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `feedback <submit|list|view|status|upvote|config>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "submit":
        await _handle_feedback_submit(message, parts)
    elif subcommand == "list":
        await _handle_feedback_list(message, parts)
    elif subcommand == "view":
        await _handle_feedback_view(message, parts)
    elif subcommand == "status":
        await _handle_feedback_status(message, parts)
    elif subcommand == "upvote":
        await _handle_feedback_upvote(message, parts)
    elif subcommand == "config":
        await _handle_feedback_config(message, parts)
    else:
        await message.reply(" Usage: `feedback <submit|list|view|status|upvote|config>`")


async def _handle_feedback_submit(message: discord.Message, parts: list[str]) -> None:
    """Handle feedback submission."""
    if len(parts) < 3:
        await message.reply(" Usage: `feedback submit <content>`")
        return

    content = parts[2]
    guild_id = message.guild.id

    # Submit feedback
    feedback = await communication_service.submit_feedback(
        guild_id,
        content,
        anonymous=True,
    )

    await message.reply(f" Feedback submitted! ID: `{feedback['id'][:8]}`")

    # Notify feedback channel if configured
    config = await communication_service.get_feedback_config(guild_id)
    if config.get("channel_id"):
        try:
            channel = message.guild.get_channel(config["channel_id"])
            if channel:
                embed = discord.Embed(
                    title="New Feedback Submission",
                    description=content[:1000],
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(
                    name="ID",
                    value=f"`{feedback['id'][:8]}`",
                    inline=True,
                )
                embed.add_field(
                    name="Status",
                    value="Pending",
                    inline=True,
                )
                await channel.send(embed=embed)
        except Exception:
            pass


async def _handle_feedback_list(message: discord.Message, parts: list[str]) -> None:
    """Handle feedback listing."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view feedback.")
        return

    status_filter = None
    if len(parts) >= 3:
        status_filter = parts[2].lower()

    guild_id = message.guild.id

    feedback_list = await communication_service.get_all_feedback(guild_id, status_filter)

    if not feedback_list:
        msg = " No feedback found"
        if status_filter:
            msg += f" with status '{status_filter}'"
        await message.reply(msg)
        return

    # Build embed
    title = "Feedback Submissions"
    if status_filter:
        title += f" - {status_filter.title()}"

    embed = discord.Embed(
        title=title,
        description=f"Total: {len(feedback_list)}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    # Show first 10
    for feedback in feedback_list[:10]:
        status_emoji = {
            "pending": "",
            "reviewed": "",
            "implemented": "",
            "dismissed": "",
        }
        emoji = status_emoji.get(feedback["status"], "")

        value = (
            f"**Content:** {feedback['content'][:100]}...\n"
            f"**Status:** {feedback['status']} {emoji}\n"
            f"**Upvotes:** {feedback.get('upvotes', 0)}"
        )

        embed.add_field(
            name=f"Feedback `{feedback['id'][:8]}`",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_feedback_view(message: discord.Message, parts: list[str]) -> None:
    """Handle feedback viewing."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view feedback details.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `feedback view <id>`")
        return

    feedback_id = parts[2]
    guild_id = message.guild.id

    feedback = await communication_service.get_feedback(guild_id, feedback_id)

    if not feedback:
        await message.reply(f" No feedback found with ID starting with `{feedback_id}`")
        return

    # Build detailed embed
    embed = discord.Embed(
        title=f"Feedback Details - {feedback['id'][:8]}",
        description=feedback["content"],
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Status",
        value=feedback["status"],
        inline=True,
    )

    embed.add_field(
        name="Upvotes",
        value=str(feedback.get("upvotes", 0)),
        inline=True,
    )

    embed.add_field(
        name="Created",
        value=feedback["created_at"][:10],
        inline=True,
    )

    if feedback.get("notes"):
        notes_str = "\n".join(f"• {n['note']}" for n in feedback["notes"][:5])
        embed.add_field(
            name="Notes",
            value=notes_str,
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_feedback_status(message: discord.Message, parts: list[str]) -> None:
    """Handle feedback status update."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to update feedback status.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `feedback status <id> <status> [note]`")
        return

    args = parts[2].split(maxsplit=2)
    if len(args) < 2:
        await message.reply(" Usage: `feedback status <id> <status> [note]`")
        return

    feedback_id = args[0]
    status = args[1].lower()
    note = args[2] if len(args) > 2 else None

    if status not in ["pending", "reviewed", "implemented", "dismissed"]:
        await message.reply(" Invalid status. Use: pending, reviewed, implemented, dismissed")
        return

    guild_id = message.guild.id

    success = await communication_service.update_feedback_status(
        guild_id,
        feedback_id,
        status,
        note,
    )

    if success:
        await message.reply(f" Feedback status updated to: {status}")
    else:
        await message.reply(" Failed to update feedback status")


async def _handle_feedback_upvote(message: discord.Message, parts: list[str]) -> None:
    """Handle feedback upvote."""
    if len(parts) < 3:
        await message.reply(" Usage: `feedback upvote <id>`")
        return

    feedback_id = parts[2]
    guild_id = message.guild.id

    success = await communication_service.upvote_feedback(guild_id, feedback_id)

    if success:
        await message.reply(" Feedback upvoted!")
    else:
        await message.reply(" Feedback not found")


async def _handle_feedback_config(message: discord.Message, parts: list[str]) -> None:
    """Handle feedback configuration."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to configure feedback.")
        return

    if len(parts) < 3 or not message.channel_mentions:
        await message.reply(" Usage: `feedback config <#channel>`")
        return

    channel = message.channel_mentions[0]
    guild_id = message.guild.id

    await communication_service.configure_feedback(
        guild_id,
        enabled=True,
        channel_id=channel.id,
    )

    await message.reply(f" Feedback notifications will be sent to {channel.mention}")


# ─── Notification Handlers ────────────────────────────────────────────────────


async def _handle_notify(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle notification commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `notify <subscribe|unsubscribe|list|channel>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "subscribe":
        await _handle_notify_subscribe(message, parts)
    elif subcommand == "unsubscribe":
        await _handle_notify_unsubscribe(message, parts)
    elif subcommand == "list":
        await _handle_notify_list(message)
    elif subcommand == "channel":
        await _handle_notify_channel(message, parts)
    else:
        await message.reply(" Usage: `notify <subscribe|unsubscribe|list|channel>`")


async def _handle_notify_subscribe(message: discord.Message, parts: list[str]) -> None:
    """Handle notification subscription."""
    if not message.mentions:
        await message.reply(" Usage: `notify subscribe @artist`")
        return

    artist = message.mentions[0]
    guild_id = message.guild.id
    user_id = message.author.id

    success = await communication_service.subscribe_to_artist(
        guild_id,
        user_id,
        artist.id,
    )

    if success:
        await message.reply(f" Subscribed to {artist.mention}'s commission announcements!")
    else:
        await message.reply(f" You're already subscribed to {artist.mention}")


async def _handle_notify_unsubscribe(message: discord.Message, parts: list[str]) -> None:
    """Handle notification unsubscription."""
    if not message.mentions:
        await message.reply(" Usage: `notify unsubscribe @artist`")
        return

    artist = message.mentions[0]
    guild_id = message.guild.id
    user_id = message.author.id

    success = await communication_service.unsubscribe_from_artist(
        guild_id,
        user_id,
        artist.id,
    )

    if success:
        await message.reply(f" Unsubscribed from {artist.mention}")
    else:
        await message.reply(f" You're not subscribed to {artist.mention}")


async def _handle_notify_list(message: discord.Message) -> None:
    """Handle notification list."""
    guild_id = message.guild.id
    user_id = message.author.id

    subscriptions = await communication_service.get_user_subscriptions(guild_id, user_id)

    if not subscriptions:
        await message.reply(" You have no subscriptions")
        return

    embed = discord.Embed(
        title="Your Subscriptions",
        description=f"You're subscribed to {len(subscriptions)} artist(s)",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for artist_id in subscriptions[:25]:
        embed.add_field(
            name="Artist",
            value=f"<@{artist_id}>",
            inline=True,
        )

    await message.reply(embed=embed)


async def _handle_notify_channel(message: discord.Message, parts: list[str]) -> None:
    """Handle announcement channel configuration."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to configure announcements.")
        return

    if not message.channel_mentions:
        await message.reply(" Usage: `notify channel <#channel>`")
        return

    channel = message.channel_mentions[0]
    guild_id = message.guild.id

    await communication_service.set_announcement_channel(guild_id, channel.id)

    await message.reply(f" Commission announcements will be sent to {channel.mention}")


# ─── Acknowledgment Handlers ──────────────────────────────────────────────────


async def _handle_ack(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle acknowledgment commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `ack create <message_link> <title> <description>` | `ack check` | `ack stats <message_link>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "check":
        await _handle_ack_check(message, bot)
    elif subcommand == "stats":
        await _handle_ack_stats(message, parts)
    elif subcommand == "create":
        await _handle_ack_create(message, parts, bot)
    else:
        await message.reply(" Usage: `ack create <message_link> <title> <description>` | `ack check` | `ack stats <message_link>`")


async def _handle_ack_create(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle acknowledgment creation."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to create acknowledgments.")
        return

    try:
        tokens = shlex.split(message.content)
    except Exception:
        tokens = message.content.split()

    # ack create <message_link> <title> <description...>
    if len(tokens) < 5:
        await message.reply(
            " Usage: `ack create <message_link> <title> <description>`\n"
            "Tip: Wrap multi-word title/description in quotes."
        )
        return

    message_link = tokens[2]
    title = tokens[3]
    description = " ".join(tokens[4:]).strip()

    trip = extract_first_message_link(message_link, message.guild.id)
    if not trip:
        await message.reply(" Invalid message link")
        return
    _gid, channel_id_str, message_id_str = trip
    try:
        channel_id = int(channel_id_str)
        message_id = int(message_id_str)
    except Exception:
        await message.reply(" Invalid message link")
        return

    guild_id = message.guild.id

    ack = await communication_service.create_acknowledgment(
        guild_id,
        message_id,
        channel_id,
        title,
        description,
    )

    # Create button view
    view = AcknowledgeButton(guild_id, message_id)

    jump = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
    embed = discord.Embed(
        title=title,
        description=description[:3500],
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Target Message", value=jump, inline=False)

    await message.reply(" Acknowledgment created.", embed=embed, view=view)


async def _handle_ack_check(message: discord.Message, bot: discord.Client) -> None:
    """Handle acknowledgment check."""
    guild_id = message.guild.id
    user_id = message.author.id

    pending = await communication_service.get_pending_acknowledgments(guild_id, user_id)

    if not pending:
        await message.reply(" No pending acknowledgments")
        return

    embed = discord.Embed(
        title="Pending Acknowledgments",
        description=f"You have {len(pending)} pending acknowledgment(s)",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )

    for ack in pending[:5]:
        channel_id = ack.get("channel_id") or message.channel.id
        msg_link = f"https://discord.com/channels/{guild_id}/{channel_id}/{ack['message_id']}"
        embed.add_field(
            name=ack["title"],
            value=f"{ack['content'][:100]}...\n[Jump to message]({msg_link})",
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_ack_stats(message: discord.Message, parts: list[str]) -> None:
    """Handle acknowledgment stats."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_messages:
        await message.reply(" You need Manage Messages permission to view acknowledgment stats.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `ack stats <message_link>`")
        return

    message_link = parts[2]

    # Parse message link
    try:
        parts_link = message_link.split("/")
        message_id = int(parts_link[-1])
    except (ValueError, IndexError):
        await message.reply(" Invalid message link")
        return

    guild_id = message.guild.id

    stats = await communication_service.get_acknowledgment_stats(guild_id, message_id)

    if not stats:
        await message.reply(" No acknowledgment found for this message")
        return

    embed = discord.Embed(
        title="Acknowledgment Statistics",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Total Acknowledged",
        value=str(stats["total_acknowledged"]),
        inline=True,
    )

    embed.add_field(
        name="Created",
        value=stats["created_at"][:10],
        inline=True,
    )

    await message.reply(embed=embed)


# ─── Button Handler for Acknowledgments ───────────────────────────────────────


class AcknowledgeButton(discord.ui.View):
    """Button view for acknowledging messages."""

    def __init__(self, guild_id: int, message_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.message_id = message_id

    @discord.ui.button(label="I Acknowledge", style=discord.ButtonStyle.green)
    async def acknowledge(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        """Handle acknowledgment button click."""
        success = await communication_service.acknowledge_message(
            self.guild_id,
            self.message_id,
            interaction.user.id,
        )

        if success:
            await interaction.response.send_message(
                " Acknowledged!",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                " Failed to record acknowledgment",
                ephemeral=True,
            )
