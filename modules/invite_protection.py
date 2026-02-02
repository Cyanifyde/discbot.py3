"""
Invite protection module - detect Discord invite links and enforce an allowlist with approval workflow.

Commands (admin/mod only by default, configurable via `modules`):
- invite status
- invite allowlist <list|add|remove> ...
- invite pending
- invite approve <pending_id|code>
- invite deny <pending_id>
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

import discord

from core.help_system import help_system
from core.invite_protection_storage import InviteProtectionStore
from core.permissions import can_use_command, is_module_enabled

logger = logging.getLogger("discbot.invite_protection")

MODULE_NAME = "inviteprotection"

_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def setup_invite_protection() -> None:
    help_system.register_module(
        name="Invite Protection",
        description="Detect and gate Discord invite links with an allowlist + approval workflow.",
        help_command="invite status",
        commands=[
            ("invite status", "Show invite protection status + counts"),
            ("invite allowlist list", "List allowlisted invite codes"),
            ("invite allowlist add <code|url>", "Allowlist an invite code (admin/mod)"),
            ("invite allowlist remove <code>", "Remove an allowlisted invite code (admin/mod)"),
            ("invite pending", "List pending invites awaiting approval"),
            ("invite approve <pending_id|code>", "Approve a pending invite (or directly allowlist a code)"),
            ("invite deny <pending_id>", "Deny/remove a pending invite"),
        ],
    )


def _extract_invite(message_content: str) -> Optional[Tuple[str, str]]:
    """
    Returns (code, matched_url_fragment) if an invite link is present.
    matched_url_fragment is what matched the regex, not necessarily a full URL.
    """
    if not message_content:
        return None
    m = _INVITE_RE.search(message_content)
    if not m:
        return None
    code = m.group(1)
    frag = m.group(0)
    return code, frag


def _normalize_code(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    m = _INVITE_RE.search(token)
    if m:
        return m.group(1)
    # remove accidental punctuation
    return token.strip("<>\"'`.,;:()[]{}")


async def handle_invite_protection(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle invite protection commands and message scanning.

    Returns True if the message was handled (command processed or invite blocked).
    """
    if not message.guild:
        return False

    content = (message.content or "").strip()
    if not content:
        return False

    # Commands first (so we don't accidentally block admin commands containing invites).
    if content.lower().startswith("invite"):
        return await _handle_invite_command(message)

    # Message scanning (only when module enabled)
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False

    invite = _extract_invite(content)
    if not invite:
        return False

    code, frag = invite
    store = InviteProtectionStore(message.guild.id)
    await store.initialize()

    if await store.is_allowlisted(code):
        return False

    # If the author can approve invites, allow them to post without blocking.
    if isinstance(message.author, discord.Member) and await can_use_command(message.author, "invite approve"):
        return False

    try:
        await message.delete()
    except discord.Forbidden:
        logger.warning("Missing permissions to delete message in guild %s", message.guild.id)
        return False
    except Exception as e:
        logger.error("Failed to delete invite message %s: %s", message.id, e)
        return False

    pending_id = await store.add_pending(
        code=code,
        invite_url=frag,
        posted_by=message.author.id,
        channel_id=message.channel.id,
        message_id=message.id,
    )

    short_id = pending_id.split("-")[0]
    try:
        await message.channel.send(
            f"Invite link removed (not allowlisted).\n"
            f"Mods: `invite approve {short_id}` to allowlist, or `invite deny {short_id}` to dismiss.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass

    return True


async def _handle_invite_command(message: discord.Message) -> bool:
    content = (message.content or "").strip()
    parts = content.split()
    if len(parts) < 2:
        await _cmd_help(message)
        return True

    sub = parts[1].lower()
    if sub == "help":
        await _cmd_help(message)
        return True

    if sub == "status":
        await _cmd_status(message)
        return True

    if sub == "allowlist":
        await _cmd_allowlist(message, parts[2:])
        return True

    if sub == "pending":
        await _cmd_pending(message)
        return True

    if sub == "approve":
        await _cmd_approve(message, parts[2:])
        return True

    if sub == "deny":
        await _cmd_deny(message, parts[2:])
        return True

    await _cmd_help(message)
    return True


async def _cmd_help(message: discord.Message) -> None:
    embed = help_system.get_module_help("Invite Protection")
    if embed:
        await message.channel.send(embed=embed)
    else:
        await message.channel.send(" Usage: `invite status`")


async def _cmd_status(message: discord.Message) -> None:
    if not message.guild:
        return

    enabled = await is_module_enabled(message.guild.id, MODULE_NAME)
    store = InviteProtectionStore(message.guild.id)
    await store.initialize()
    allow = await store.list_allowlist()
    pending = await store.list_pending()

    await message.channel.send(
        "\n".join(
            [
                f"**Invite Protection:** {'Enabled' if enabled else 'Disabled'}",
                f"**Allowlisted codes:** {len(allow)}",
                f"**Pending approvals:** {len(pending)}",
                "",
                "Enable/disable via: `modules enable inviteprotection` / `modules disable inviteprotection`",
            ]
        ),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _cmd_allowlist(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "invite allowlist"):
        await message.channel.send(" You don't have permission to manage the invite allowlist.")
        return

    if not args:
        await message.channel.send(" Usage: `invite allowlist <list|add|remove> ...`")
        return

    action = args[0].lower()
    store = InviteProtectionStore(message.guild.id)
    await store.initialize()

    if action == "list":
        items = await store.list_allowlist()
        if not items:
            await message.channel.send(" Allowlist is empty.")
            return
        lines = [f"- `{code}`" for code, _meta in items[:50]]
        extra = "" if len(items) <= 50 else f"\n…and {len(items) - 50} more."
        await message.channel.send("**Allowlisted invite codes:**\n" + "\n".join(lines) + extra)
        return

    if action == "add":
        if len(args) < 2:
            await message.channel.send(" Usage: `invite allowlist add <code|url>`")
            return
        code = _normalize_code(args[1])
        if not code:
            await message.channel.send(" Invalid invite code.")
            return
        await store.add_allowlist(code, actor_id=message.author.id)
        await message.channel.send(f" Allowlisted invite code `{code}`.")
        return

    if action == "remove":
        if len(args) < 2:
            await message.channel.send(" Usage: `invite allowlist remove <code>`")
            return
        code = _normalize_code(args[1])
        if not code:
            await message.channel.send(" Invalid invite code.")
            return
        ok = await store.remove_allowlist(code)
        if ok:
            await message.channel.send(f" Removed `{code}` from allowlist.")
        else:
            await message.channel.send(f" `{code}` was not allowlisted.")
        return

    await message.channel.send(" Usage: `invite allowlist <list|add|remove> ...`")


async def _cmd_pending(message: discord.Message) -> None:
    if not message.guild:
        return
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "invite pending"):
        await message.channel.send(" You don't have permission to view pending invites.")
        return

    store = InviteProtectionStore(message.guild.id)
    await store.initialize()
    pending = await store.list_pending()

    if not pending:
        await message.channel.send(" No pending invites.")
        return

    lines: list[str] = ["**Pending invites:**"]
    for pid, entry in pending[:15]:
        short_id = pid.split("-")[0]
        code = entry.get("code", "?")
        posted_by = entry.get("posted_by")
        lines.append(f"- `{short_id}` code=`{code}` posted_by=`{posted_by}`")
    if len(pending) > 15:
        lines.append(f"…and {len(pending) - 15} more.")
    await message.channel.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())


async def _cmd_approve(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "invite approve"):
        await message.channel.send(" You don't have permission to approve invites.")
        return
    if not args:
        await message.channel.send(" Usage: `invite approve <pending_id|code>`")
        return

    token = _normalize_code(args[0]) or args[0].strip()
    store = InviteProtectionStore(message.guild.id)
    await store.initialize()

    code = await store.approve(token, actor_id=message.author.id)
    if not code:
        await message.channel.send(" Nothing approved.")
        return
    await message.channel.send(f" Approved and allowlisted `{code}`.")


async def _cmd_deny(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "invite deny"):
        await message.channel.send(" You don't have permission to deny invites.")
        return
    if not args:
        await message.channel.send(" Usage: `invite deny <pending_id>`")
        return

    token = args[0].strip()
    store = InviteProtectionStore(message.guild.id)
    await store.initialize()

    removed = await store.deny(token)
    if not removed:
        await message.channel.send(" Pending invite not found (or prefix not unique).")
        return
    await message.channel.send(f" Denied pending invite `{removed.split('-')[0]}`.")
