"""
Pass-the-Canvas (PTC) service - automated turn management for collaborative art threads.

Players claim turns by saying "taken" / "i take" in a bound forum thread.
Only the holder can post a submission image; all other messages are silently deleted.
Turns expire after 30 minutes, and simultaneous claims are resolved randomly.

Text commands:
    ptc setforum <id>    - Set the forum channel for PTC
    ptc bind [thread_id]  - Bind PTC to a forum thread (activates enforcement)
    ptc unbind             - Unbind the current thread (deactivates enforcement)
    ptc status             - Show current PTC state (anyone)
    ptc clear              - Clear turn state without unbinding (admin)
    ptc force_clear        - Force clear + unbind (admin)
    ptc debug              - Show debug info (admin)
    ptc help               - Show all PTC commands

Thread enforcement (bound thread only):
    "taken" / "i take" etc. -> claim the canvas
    Image attachment from holder -> submission accepted
    Everything else -> silently deleted
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord

from core.config_migration import get_guild_module_data, update_guild_module_data
from core.help_system import help_system
from core.permissions import can_use_module, is_module_enabled
from core.utils import iso_to_dt, safe_int, utcnow

if TYPE_CHECKING:
    from bot.client import DiscBot

logger = logging.getLogger("discbot.ptc")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODULE_NAME = "ptc"

CLAIM_WINDOW_SECONDS = 1.5  # 1500 ms
TURN_EXPIRY_SECONDS = 30 * 60  # 30 minutes

COMMAND_PATTERN = re.compile(r"^ptc\s+(\w+)(?:\s+(.*))?$", re.IGNORECASE)

SUBCOMMANDS = {
    "setforum", "bind", "unbind", "status", "start",
    "clear", "force_clear", "debug", "help",
}

TAKE_PHRASES = frozenset({
    "taken", "take", "taking", "mine",
    "i take", "i'll take", "ill take",
    "i take it", "i'll take it", "ill take it",
})

# Admin subcommands that require host-level permissions.
_HOST_COMMANDS = {"setforum", "bind", "unbind", "clear", "force_clear", "debug", "start"}


class TurnStatus:
    AVAILABLE = "available"
    CLAIMING = "claiming"
    HELD = "held"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

@dataclass
class PTCState:
    forum_channel_id: Optional[int] = None
    bound_thread_id: Optional[int] = None
    active: bool = False

    status: str = TurnStatus.AVAILABLE
    holder_id: Optional[int] = None
    holder_name: Optional[str] = None
    taken_at: Optional[dt.datetime] = None
    expires_at: Optional[dt.datetime] = None
    last_taker_id: Optional[int] = None
    last_submission_url: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    # asyncio handles (not persisted)
    claim_task: Optional[asyncio.Task] = None
    expiry_task: Optional[asyncio.Task] = None


_guild_states: Dict[int, PTCState] = {}

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _state_to_dict(state: PTCState) -> Dict[str, Any]:
    return {
        "forum_channel_id": state.forum_channel_id,
        "bound_thread_id": state.bound_thread_id,
        "active": state.active,
        "status": state.status,
        "holder_id": state.holder_id,
        "holder_name": state.holder_name,
        "taken_at": state.taken_at.isoformat() if state.taken_at else None,
        "expires_at": state.expires_at.isoformat() if state.expires_at else None,
        "last_taker_id": state.last_taker_id,
        "last_submission_url": state.last_submission_url,
        "candidates": state.candidates,
    }


def _dict_to_state(data: Dict[str, Any]) -> PTCState:
    state = PTCState()
    state.forum_channel_id = data.get("forum_channel_id")
    state.bound_thread_id = data.get("bound_thread_id")
    state.active = data.get("active", False)
    state.status = data.get("status", TurnStatus.AVAILABLE)
    state.holder_id = data.get("holder_id")
    state.holder_name = data.get("holder_name")
    taken_at = data.get("taken_at")
    state.taken_at = iso_to_dt(taken_at) if taken_at else None
    expires_at = data.get("expires_at")
    state.expires_at = iso_to_dt(expires_at) if expires_at else None
    state.last_taker_id = data.get("last_taker_id")
    state.last_submission_url = data.get("last_submission_url")
    state.candidates = data.get("candidates", [])
    return state


def _get_state(guild_id: int) -> Optional[PTCState]:
    return _guild_states.get(guild_id)


def _get_or_create_state(guild_id: int) -> PTCState:
    if guild_id not in _guild_states:
        _guild_states[guild_id] = PTCState()
    return _guild_states[guild_id]


async def _persist_state(guild_id: int) -> None:
    state = _guild_states.get(guild_id)
    if state is None:
        return
    await update_guild_module_data(guild_id, MODULE_NAME, _state_to_dict(state))


async def _load_persisted(guild_id: int) -> Optional[PTCState]:
    data = await get_guild_module_data(guild_id, MODULE_NAME)
    if data is None or not isinstance(data, dict):
        return None
    return _dict_to_state(data)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _has_image(message: discord.Message) -> bool:
    for att in message.attachments:
        ct = (att.content_type or "").lower()
        if ct.startswith("image/"):
            return True
    return False


def _get_image_url(message: discord.Message) -> Optional[str]:
    for att in message.attachments:
        ct = (att.content_type or "").lower()
        if ct.startswith("image/"):
            return att.url
    return None


def _is_take_attempt(content: str) -> bool:
    return content.strip().lower() in TAKE_PHRASES


def _is_host(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return perms.administrator or perms.manage_threads or perms.manage_messages


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


async def _try_delete(message: discord.Message) -> None:
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def _send_and_auto_delete(
    channel: discord.abc.Messageable,
    content: str,
    delay: float = 8.0,
) -> None:
    """Send a message that is automatically deleted after *delay* seconds."""
    try:
        msg = await channel.send(content)
        await asyncio.sleep(delay)
        await _try_delete(msg)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _get_thread(guild_id: int, bot: DiscBot, state: PTCState) -> Optional[discord.Thread]:
    """Resolve the bound thread, deactivating PTC if the thread is gone."""
    if state.bound_thread_id is None:
        return None
    thread = bot.get_channel(state.bound_thread_id)
    if thread is not None:
        return thread  # type: ignore[return-value]
    try:
        thread = await bot.fetch_channel(state.bound_thread_id)
        return thread  # type: ignore[return-value]
    except (discord.NotFound, discord.Forbidden):
        logger.warning("PTC thread %s gone for guild %s, deactivating", state.bound_thread_id, guild_id)
        state.active = False
        state.bound_thread_id = None
        await _cancel_timers(state)
        await _persist_state(guild_id)
        return None


# ---------------------------------------------------------------------------
# Timer management
# ---------------------------------------------------------------------------


async def _cancel_timers(state: PTCState) -> None:
    for attr in ("claim_task", "expiry_task"):
        task: Optional[asyncio.Task] = getattr(state, attr)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        setattr(state, attr, None)


async def _claim_window_coro(guild_id: int, bot: DiscBot) -> None:
    """Runs after CLAIM_WINDOW_SECONDS; resolves the take race."""
    await asyncio.sleep(CLAIM_WINDOW_SECONDS)
    await _resolve_claim(guild_id, bot)


async def _expiry_timer_coro(guild_id: int, bot: DiscBot, seconds: float = TURN_EXPIRY_SECONDS) -> None:
    """Runs after *seconds*; expires the current turn."""
    await asyncio.sleep(seconds)
    await _expire_turn(guild_id, bot)


# ---------------------------------------------------------------------------
# Turn logic
# ---------------------------------------------------------------------------


async def _resolve_claim(guild_id: int, bot: DiscBot) -> None:
    """Pick a random winner from the candidates list."""
    state = _guild_states.get(guild_id)
    if state is None or state.status != TurnStatus.CLAIMING:
        return

    state.claim_task = None

    if not state.candidates:
        # No valid candidates (shouldn't happen normally).
        state.status = TurnStatus.AVAILABLE
        await _persist_state(guild_id)
        return

    winner = random.choice(state.candidates)
    await _start_held(guild_id, winner["user_id"], winner["user_name"], bot,
                      total_candidates=len(state.candidates))


async def _start_held(
    guild_id: int,
    winner_id: int,
    winner_name: str,
    bot: DiscBot,
    *,
    total_candidates: int = 1,
) -> None:
    """Transition to HELD state for the given winner."""
    state = _guild_states.get(guild_id)
    if state is None:
        return

    now = utcnow()
    state.status = TurnStatus.HELD
    state.holder_id = winner_id
    state.holder_name = winner_name
    state.taken_at = now
    state.expires_at = now + dt.timedelta(seconds=TURN_EXPIRY_SECONDS)
    state.candidates = []

    # Schedule expiry.
    state.expiry_task = asyncio.create_task(_expiry_timer_coro(guild_id, bot))

    await _persist_state(guild_id)

    thread = await _get_thread(guild_id, bot, state)
    if thread is None:
        return

    if total_candidates > 1:
        text = (
            f"**{winner_name}** won the draw ({total_candidates} contestants). "
            f"Submit your work within 30 minutes."
        )
    else:
        text = (
            f"**{winner_name}** has claimed the canvas. "
            f"Submit your work within 30 minutes."
        )
    try:
        await thread.send(text)
    except discord.HTTPException:
        pass

    logger.info(
        "PTC guild=%s holder=%s (%s) candidates=%s",
        guild_id, winner_id, winner_name, total_candidates,
    )


async def _expire_turn(guild_id: int, bot: DiscBot) -> None:
    """Called when the 30-minute turn timer fires."""
    state = _guild_states.get(guild_id)
    if state is None or state.status != TurnStatus.HELD:
        return

    expired_name = state.holder_name or str(state.holder_id)
    state.expiry_task = None
    state.status = TurnStatus.EXPIRED
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None

    await _persist_state(guild_id)

    thread = await _get_thread(guild_id, bot, state)
    if thread is None:
        return

    try:
        await thread.send(f"**{expired_name}**'s turn has expired. The canvas is available!")
    except discord.HTTPException:
        pass

    logger.info("PTC guild=%s turn expired for %s", guild_id, expired_name)


async def _accept_submission(guild_id: int, message: discord.Message, bot: DiscBot) -> None:
    """Accept an image submission from the current holder."""
    state = _guild_states.get(guild_id)
    if state is None:
        return

    image_url = _get_image_url(message)

    # Cancel expiry timer.
    if state.expiry_task and not state.expiry_task.done():
        state.expiry_task.cancel()
        try:
            await state.expiry_task
        except asyncio.CancelledError:
            pass
    state.expiry_task = None

    submitter_name = state.holder_name or message.author.display_name
    state.last_taker_id = state.holder_id
    state.last_submission_url = image_url
    state.status = TurnStatus.AVAILABLE
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None
    state.candidates = []

    await _persist_state(guild_id)

    try:
        await message.channel.send(
            f"**{submitter_name}** has submitted! The canvas is now available."
        )
    except discord.HTTPException:
        pass

    logger.info("PTC guild=%s submission from %s", guild_id, message.author.id)


async def _accept_late_submission(guild_id: int, message: discord.Message, bot: DiscBot) -> None:
    """Accept a late submission from the previous holder during a take race."""
    state = _guild_states.get(guild_id)
    if state is None:
        return

    # Cancel the claim window if active.
    if state.claim_task and not state.claim_task.done():
        state.claim_task.cancel()
        try:
            await state.claim_task
        except asyncio.CancelledError:
            pass
    state.claim_task = None

    image_url = _get_image_url(message)
    submitter_name = message.author.display_name

    state.last_taker_id = message.author.id
    state.last_submission_url = image_url
    state.status = TurnStatus.AVAILABLE
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None
    state.candidates = []

    await _persist_state(guild_id)

    try:
        await message.channel.send(
            f"**{submitter_name}** submitted late! Race cancelled. The canvas is available."
        )
    except discord.HTTPException:
        pass

    logger.info("PTC guild=%s late submission from %s", guild_id, message.author.id)


# ---------------------------------------------------------------------------
# Thread enforcement
# ---------------------------------------------------------------------------


async def _enforce_thread_message(message: discord.Message, state: PTCState, bot: DiscBot) -> None:
    """Process a non-command message inside the bound PTC thread.

    Handles take attempts, submissions, and deletes everything else.
    """
    guild_id = message.guild.id  # type: ignore[union-attr]
    content = (message.content or "").strip().lower()
    user_id = message.author.id
    user_name = message.author.display_name

    # --- Late submission (previous holder submits during CLAIMING / EXPIRED) ---
    if state.status in (TurnStatus.CLAIMING, TurnStatus.EXPIRED) and _has_image(message):
        if state.last_taker_id is not None and user_id == state.last_taker_id:
            await _accept_late_submission(guild_id, message, bot)
            return

    # --- Take attempts ---
    if _is_take_attempt(content):
        if state.status in (TurnStatus.AVAILABLE, TurnStatus.EXPIRED):
            # Reject consecutive turns.
            if state.last_taker_id is not None and user_id == state.last_taker_id:
                await _try_delete(message)
                asyncio.create_task(
                    _send_and_auto_delete(
                        message.channel,
                        f"{message.author.mention}, you went last. Someone else needs to go first.",
                    )
                )
                return

            # First candidate: start claim window.
            state.status = TurnStatus.CLAIMING
            state.candidates = [{"user_id": user_id, "user_name": user_name}]
            state.claim_task = asyncio.create_task(_claim_window_coro(guild_id, bot))
            await _try_delete(message)
            await _persist_state(guild_id)
            return

        if state.status == TurnStatus.CLAIMING:
            # Reject consecutive turns.
            if state.last_taker_id is not None and user_id == state.last_taker_id:
                await _try_delete(message)
                return

            # Deduplicate.
            existing_ids = {c["user_id"] for c in state.candidates}
            if user_id not in existing_ids:
                state.candidates.append({"user_id": user_id, "user_name": user_name})
                await _persist_state(guild_id)
            await _try_delete(message)
            return

        # HELD: can't take while someone holds.
        await _try_delete(message)
        return

    # --- Submission (image from current holder) ---
    if state.status == TurnStatus.HELD and user_id == state.holder_id and _has_image(message):
        await _accept_submission(guild_id, message, bot)
        return  # keep the submission message

    # --- Everything else: silently delete ---
    await _try_delete(message)


# ---------------------------------------------------------------------------
# Public message handler (wired into on_message)
# ---------------------------------------------------------------------------


async def handle_ptc_message(message: discord.Message, bot: DiscBot) -> bool:
    """Top-level PTC handler called early in the on_message pipeline.

    Returns True if the message was consumed (command handled or thread-enforced).
    """
    if not message.guild:
        return False

    guild_id = message.guild.id
    content = (message.content or "").strip()
    content_lower = content.lower()

    # 1. PTC commands from any channel.
    if content_lower == "ptc" or content_lower.startswith("ptc "):
        return await _handle_command(message, bot)

    # 2. Thread enforcement for bound threads.
    state = _guild_states.get(guild_id)
    if state and state.active and state.bound_thread_id == message.channel.id:
        if not await is_module_enabled(guild_id, MODULE_NAME):
            return False  # module disabled; let message through
        await _enforce_thread_message(message, state, bot)
        return True

    return False


# ---------------------------------------------------------------------------
# Command routing
# ---------------------------------------------------------------------------


async def _handle_command(message: discord.Message, bot: DiscBot) -> bool:
    """Route ``ptc <subcommand>`` messages."""
    if not message.guild:
        return False

    content = (message.content or "").strip()
    match = COMMAND_PATTERN.match(content)
    if not match:
        # Bare "ptc" with no subcommand.
        await message.reply(
            "Use `ptc help` for a list of commands.",
            mention_author=False,
        )
        return True

    subcommand = match.group(1).lower()
    args = (match.group(2) or "").strip()

    if subcommand not in SUBCOMMANDS:
        await message.reply(
            f"Unknown subcommand `{subcommand}`. Use `ptc help` for a list of commands.",
            mention_author=False,
        )
        return True

    member = message.guild.get_member(message.author.id)
    if not member:
        return False

    # Module enabled check.
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.reply(
            "PTC module is disabled in this server.\n"
            "An administrator can enable it with `modules enable ptc`",
            mention_author=False,
        )
        return True

    # Permission check: status is open to anyone with module access.
    if subcommand not in ("status", "help"):
        if not await can_use_module(member, MODULE_NAME):
            await message.reply(
                "You don't have permission to use PTC commands.\n"
                "An administrator can grant access with `modules allow ptc @YourRole`",
                mention_author=False,
            )
            return True

    # Host-only commands.
    if subcommand in _HOST_COMMANDS and not _is_host(member):
        await message.reply(
            "This command requires **Manage Threads**, **Manage Messages**, or **Administrator** permission.",
            mention_author=False,
        )
        return True

    # Dispatch.
    if subcommand == "setforum":
        await _cmd_setforum(message, args)
    elif subcommand == "bind":
        await _cmd_bind(message, args, bot)
    elif subcommand == "unbind":
        await _cmd_unbind(message, bot)
    elif subcommand == "start":
        await _cmd_start(message, bot)
    elif subcommand == "status":
        await _cmd_status(message)
    elif subcommand == "clear":
        await _cmd_clear(message, bot)
    elif subcommand == "force_clear":
        await _cmd_force_clear(message, bot)
    elif subcommand == "debug":
        await _cmd_debug(message)
    elif subcommand == "help":
        await _cmd_help(message)

    return True


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


async def _cmd_start(message: discord.Message, bot: DiscBot) -> None:
    """Start the PTC event in the current bound thread."""
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_state(guild_id)

    if state is None or not state.active:
        await message.reply(
            "PTC is not bound to a thread yet. Use `ptc bind` first.",
            mention_author=False,
        )
        return

    # Delete the command message itself.
    await _try_delete(message)

    thread = await _get_thread(guild_id, bot, state)
    if thread is None:
        return

    try:
        await thread.send("**Event started!** Say **taken** to claim the canvas.")
    except discord.HTTPException:
        pass

    logger.info("PTC guild=%s started by %s", guild_id, message.author.id)


async def _cmd_setforum(message: discord.Message, args: str) -> None:
    if not args:
        await message.reply(
            "Provide a forum channel ID.\n"
            "Usage: `ptc setforum <channel_id>`",
            mention_author=False,
        )
        return

    # Parse channel mention <#id> or raw id.
    ch_match = re.search(r"<#(\d+)>", args)
    if ch_match:
        channel_id = int(ch_match.group(1))
    else:
        raw = args.strip()
        if raw.isdigit():
            channel_id = int(raw)
        else:
            await message.reply("Invalid channel ID.", mention_author=False)
            return

    channel = message.guild.get_channel(channel_id)  # type: ignore[union-attr]
    if channel is None:
        await message.reply("Channel not found in this server.", mention_author=False)
        return
    if not isinstance(channel, discord.ForumChannel):
        await message.reply("That channel is not a forum channel.", mention_author=False)
        return

    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_or_create_state(guild_id)
    state.forum_channel_id = channel_id
    await _persist_state(guild_id)

    await message.reply(f"PTC forum channel set to <#{channel_id}>.", mention_author=False)
    logger.info("PTC guild=%s forum set to %s by %s", guild_id, channel_id, message.author.id)


async def _cmd_bind(message: discord.Message, args: str, bot: DiscBot) -> None:
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_or_create_state(guild_id)

    thread: Optional[discord.Thread] = None

    if args:
        thread_id = safe_int(args.strip())
        if not thread_id:
            await message.reply("Invalid thread ID.", mention_author=False)
            return
        thread = message.guild.get_thread(thread_id)  # type: ignore[union-attr]
        if thread is None:
            try:
                ch = await bot.fetch_channel(thread_id)
                if isinstance(ch, discord.Thread):
                    thread = ch
            except (discord.NotFound, discord.Forbidden):
                pass
        if thread is None:
            await message.reply("Thread not found.", mention_author=False)
            return
    else:
        if not isinstance(message.channel, discord.Thread):
            await message.reply(
                "Run this command inside a forum thread, or provide a thread ID.\n"
                "Usage: `ptc bind [thread_id]`",
                mention_author=False,
            )
            return
        thread = message.channel

    # Validate forum parent.
    if state.forum_channel_id and thread.parent_id != state.forum_channel_id:
        await message.reply(
            f"Thread must be in the configured forum channel (<#{state.forum_channel_id}>).",
            mention_author=False,
        )
        return

    # Auto-set forum channel from thread parent if not yet configured.
    if not state.forum_channel_id and thread.parent_id:
        state.forum_channel_id = thread.parent_id

    # Cancel any existing timers from a previously bound session.
    await _cancel_timers(state)

    state.bound_thread_id = thread.id
    state.active = True
    state.status = TurnStatus.AVAILABLE
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None
    state.last_taker_id = None
    state.last_submission_url = None
    state.candidates = []

    await _persist_state(guild_id)

    await message.reply(
        f"PTC bound to <#{thread.id}>. Enforcement is now active.\n"
        f"Say **taken** in the thread to claim the canvas.",
        mention_author=False,
    )
    logger.info("PTC guild=%s bound to thread %s by %s", guild_id, thread.id, message.author.id)


async def _cmd_unbind(message: discord.Message, bot: DiscBot) -> None:
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_state(guild_id)
    if state is None or not state.active:
        await message.reply("PTC is not currently active.", mention_author=False)
        return

    await _cancel_timers(state)

    old_thread = state.bound_thread_id
    state.bound_thread_id = None
    state.active = False
    state.status = TurnStatus.AVAILABLE
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None
    state.candidates = []

    await _persist_state(guild_id)

    await message.reply(
        f"PTC unbound from <#{old_thread}>. Enforcement is now inactive.",
        mention_author=False,
    )
    logger.info("PTC guild=%s unbound by %s", guild_id, message.author.id)


async def _cmd_status(message: discord.Message) -> None:
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_state(guild_id)

    if state is None or not state.active:
        await message.reply("**Pass the Canvas** -- No thread is currently bound.", mention_author=False)
        return

    lines: list[str] = ["**Pass the Canvas -- Status**"]
    lines.append(f"Thread: <#{state.bound_thread_id}>")

    if state.status == TurnStatus.AVAILABLE:
        lines.append("Status: **Available** -- say `taken` to claim!")
    elif state.status == TurnStatus.CLAIMING:
        n = len(state.candidates)
        lines.append(f"Status: **Claim race in progress** ({n} contestant{'s' if n != 1 else ''})")
    elif state.status == TurnStatus.HELD:
        lines.append(f"Status: **Held** by **{state.holder_name}**")
        if state.expires_at:
            now = utcnow()
            remaining = state.expires_at - now
            mins = int(remaining.total_seconds()) // 60
            secs = int(remaining.total_seconds()) % 60
            if remaining.total_seconds() > 0:
                lines.append(f"Time remaining: {mins}:{secs:02d}")
            else:
                lines.append("Time remaining: **expired** (timer pending)")
    elif state.status == TurnStatus.EXPIRED:
        lines.append("Status: **Expired** -- say `taken` to claim!")

    if state.last_submission_url:
        lines.append(f"Last submission: {state.last_submission_url}")

    await message.reply("\n".join(lines), mention_author=False)


async def _cmd_clear(message: discord.Message, bot: DiscBot) -> None:
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_state(guild_id)
    if state is None or not state.active:
        await message.reply("PTC is not currently active.", mention_author=False)
        return

    await _cancel_timers(state)

    state.status = TurnStatus.AVAILABLE
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None
    state.candidates = []

    await _persist_state(guild_id)

    await message.reply("PTC turn state cleared. The canvas is available.", mention_author=False)
    logger.info("PTC guild=%s cleared by %s", guild_id, message.author.id)


async def _cmd_force_clear(message: discord.Message, bot: DiscBot) -> None:
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_state(guild_id)
    if state is None:
        await message.reply("PTC has no state for this server.", mention_author=False)
        return

    await _cancel_timers(state)

    state.bound_thread_id = None
    state.active = False
    state.status = TurnStatus.AVAILABLE
    state.holder_id = None
    state.holder_name = None
    state.taken_at = None
    state.expires_at = None
    state.last_taker_id = None
    state.last_submission_url = None
    state.candidates = []

    await _persist_state(guild_id)

    await message.reply("PTC force-cleared and unbound.", mention_author=False)
    logger.info("PTC guild=%s force_clear by %s", guild_id, message.author.id)


async def _cmd_debug(message: discord.Message) -> None:
    guild_id = message.guild.id  # type: ignore[union-attr]
    state = _get_state(guild_id)
    if state is None:
        await message.reply("No PTC state for this guild.", mention_author=False)
        return

    lines = [
        "**PTC Debug Info**",
        f"Forum: {f'<#{state.forum_channel_id}>' if state.forum_channel_id else 'Not set'}",
        f"Thread: {f'<#{state.bound_thread_id}>' if state.bound_thread_id else 'Not set'}",
        f"Active: {state.active}",
        f"Status: {state.status}",
        f"Holder: {state.holder_name} ({state.holder_id})" if state.holder_id else "Holder: None",
        f"Taken at: {state.taken_at.isoformat() if state.taken_at else 'N/A'}",
        f"Expires at: {state.expires_at.isoformat() if state.expires_at else 'N/A'}",
        f"Last taker: {state.last_taker_id}",
        f"Last submission: {state.last_submission_url or 'None'}",
        f"Candidates: {len(state.candidates)}",
        f"Claim task active: {state.claim_task is not None and not state.claim_task.done()}",
        f"Expiry task active: {state.expiry_task is not None and not state.expiry_task.done()}",
    ]
    await message.reply("\n".join(lines), mention_author=False)


async def _cmd_help(message: discord.Message) -> None:
    embed = help_system.get_module_embed("Pass the Canvas")
    if embed is None:
        await message.reply("Help not available.", mention_author=False)
        return
    await message.reply(embed=embed, mention_author=False)


# ---------------------------------------------------------------------------
# Help registration
# ---------------------------------------------------------------------------

_HELP_REGISTERED = False


def register_help() -> None:
    global _HELP_REGISTERED
    if _HELP_REGISTERED:
        return
    help_system.register_module(
        name="Pass the Canvas",
        description="Automated turn management for collaborative art in forum threads.",
        help_command="ptc help",
        commands=[
            ("ptc help", "Show all PTC commands"),
            ("ptc setforum <channel_id>", "Set the forum channel for PTC"),
            ("ptc bind [thread_id]", "Bind PTC to a forum thread"),
            ("ptc unbind", "Unbind and deactivate PTC"),
            ("ptc start", "Announce the event has started (admin)"),
            ("ptc status", "Show current PTC state"),
            ("ptc clear", "Clear turn state (admin)"),
            ("ptc force_clear", "Force clear and unbind (admin)"),
            ("ptc debug", "Show debug info (admin)"),
        ],
    )
    _HELP_REGISTERED = True


# ---------------------------------------------------------------------------
# Setup / restore / cleanup
# ---------------------------------------------------------------------------


def setup_ptc() -> None:
    """Called from setup_hook to register help."""
    register_help()


async def restore_ptc_state(bot: DiscBot) -> None:
    """Restore PTC state for all guilds on bot startup."""
    register_help()
    for guild_id in list(bot.guild_states):
        try:
            state = await _load_persisted(guild_id)
            if state is None or not state.active:
                continue

            _guild_states[guild_id] = state
            now = utcnow()

            if state.status == TurnStatus.HELD and state.expires_at:
                if now >= state.expires_at:
                    # Turn expired while the bot was down.
                    await _expire_turn(guild_id, bot)
                else:
                    remaining = (state.expires_at - now).total_seconds()
                    state.expiry_task = asyncio.create_task(
                        _expiry_timer_coro(guild_id, bot, remaining)
                    )
                    logger.info(
                        "PTC guild=%s restored HELD (%.0fs remaining)",
                        guild_id, remaining,
                    )

            elif state.status == TurnStatus.CLAIMING:
                # Claim window would have elapsed during downtime; resolve now.
                if state.candidates:
                    await _resolve_claim(guild_id, bot)
                else:
                    state.status = TurnStatus.AVAILABLE
                    await _persist_state(guild_id)
                logger.info("PTC guild=%s resolved stale CLAIMING on restore", guild_id)

            else:
                logger.info("PTC guild=%s restored (status=%s)", guild_id, state.status)

        except Exception as e:
            logger.error("Failed to restore PTC state for guild %s: %s", guild_id, e)


async def cleanup_ptc() -> None:
    """Cancel all active PTC timers (called on bot shutdown)."""
    for state in _guild_states.values():
        await _cancel_timers(state)
    _guild_states.clear()
