"""
Discord bot client - lean event handling and command registration.

This is the main Discord client class, refactored to be much leaner.
Business logic is delegated to services and GuildState.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Optional

import discord
from discord import app_commands

from classes import profile as profile_module
from core.config import (
    ConfigError,
    ensure_guild_config,
    load_default_template,
    load_guild_config,
)
from core.config_migration import migrate_all_guild_configs
from core.constants import K
from core.interactions import handle_interaction
from core.io_utils import read_text
from core.paths import resolve_repo_path
from core.utils import dt_to_iso, iso_to_dt, safe_int, sanitize_text, utcnow
from core.help_system import help_system
from modules.auto_responder import (
    handle_auto_responder,
    handle_add_response_command,
    handle_list_responses_command,
    handle_remove_response_command,
)
from modules.dm_sender import handle_dm_send
from modules.verification import (
    handle_remove_verification_command,
    handle_verification_command,
    restore_verification_views,
    setup_verification,
)
from modules.moderation import (
    handle_moderation_command,
    setup_moderation,
)
from modules.server_stats import (
    handle_serverstats_command,
    setup_server_stats,
)
from modules.server_link import (
    handle_server_link_command,
    setup_server_link,
)
from modules.commissions import (
    handle_commission_command,
    setup_commissions,
)
from modules.commission_reviews import (
    handle_commission_reviews_command,
    setup_commission_reviews,
)
from modules.portfolio import (
    handle_portfolio_command,
    setup_portfolio,
)
from modules.reports import (
    handle_report_command,
    setup_reports,
)
from modules.utility import (
    handle_utility_command,
    setup_utility,
    handle_bookmark_reaction,
    bookmark_delivery_loop,
)
from modules.communication import (
    handle_communication_command,
    setup_communication,
)
from modules.art_tools import (
    handle_art_tools_command,
    setup_art_tools,
)
from modules.art_search import (
    handle_art_search_command,
    setup_art_search,
)
from modules.automation import (
    handle_automation_command,
    setup_automation,
)
from modules.roles import (
    handle_roles_command,
    setup_roles,
)
from modules.custom_content import (
    handle_custom_content_command,
    setup_custom_content,
)
from modules.analytics import (
    handle_analytics_command,
    setup_analytics,
)
from modules.trust import (
    handle_trust_command,
    setup_trust,
)
from modules.invite_protection import (
    handle_invite_protection,
    setup_invite_protection,
)
from services.inactivity import handle_command as handle_inactivity_command
from services.inactivity import restore_state as restore_inactivity_state
from services.scanner import handle_command as handle_scanner_command
from services.scanner import restore_state as restore_scanner_state
from services.sync_service import setup_sync_interactions
from modules.modules_command import handle_command as handle_modules_command
from modules.modules_command import register_help as register_modules_help

from .guild_state import GuildState

logger = logging.getLogger("discbot")


class DiscBot(discord.Client):
    """
    Main Discord bot client.
    
    Handles:
    - Discord events (on_ready, on_message, etc.)
    - Command registration
    - Guild state management
    
    Business logic is delegated to GuildState and services.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(intents=intents)
        
        self.tree = app_commands.CommandTree(self)
        self.guild_states: dict[int, GuildState] = {}
        self.default_template: Optional[dict[str, Any]] = None
        self.ready_once = False
        self._status_task: Optional[asyncio.Task] = None
        self._bookmark_task: Optional[asyncio.Task] = None
        self._last_status: Optional[str] = None

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        # Register interaction handlers
        setup_verification()
        setup_moderation()
        setup_server_stats()
        setup_server_link()
        setup_sync_interactions()

        # Register Phase 2-4 modules
        setup_commissions()
        setup_commission_reviews()
        setup_portfolio()
        setup_reports()
        setup_utility()
        setup_communication()
        setup_art_tools()
        setup_art_search()
        setup_automation()
        setup_roles()
        setup_custom_content()
        setup_analytics()
        setup_trust()
        setup_invite_protection()

        # Register help for the modules management command early so it appears in @bot help.
        register_modules_help()

        expected_help = {
            "Analytics",
            "Art Search",
            "Art Tools",
            "Auto-Responder",
            "Automation",
            "Commission Reviews",
            "Commissions",
            "Communication",
            "Custom Content",
            "Inactivity Enforcement",
            "Invite Protection",
            "Moderation",
            "Module Management",
            "Portfolio",
            "Reports",
            "Roles",
            "Scanner",
            "Server Link",
            "Server Stats",
            "Trust",
            "Utility",
            "Verification",
        }
        missing = sorted(expected_help - set(help_system.get_module_names()))
        if missing:
            logger.warning("Missing help registrations for: %s", ", ".join(missing))

        await self._register_commands()
        await self.tree.sync()

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        if not self.ready_once:
            logger.info("Bot ready as %s", self.user)
            self.ready_once = True

            # Run config migrations (adds new fields without overwriting data)
            await migrate_all_guild_configs()

            # Register help for modules command
            register_modules_help()

            # Initialize guild states first
            await self._initialize_existing_guilds()

            # Restore verification buttons from saved data
            await restore_verification_views(self)

            # Restore service states (inactivity)
            await restore_inactivity_state(self)

            # Restore scanner state (also registers scanner help)
            await restore_scanner_state(self)

            self._status_task = asyncio.create_task(self._status_loop())
            self._bookmark_task = asyncio.create_task(bookmark_delivery_loop(self))

    async def close(self) -> None:
        """Cleanup when shutting down."""
        for state in list(self.guild_states.values()):
            await state.stop()
        
        # Cancel and await background tasks
        tasks_to_cancel = []
        if self._status_task:
            self._status_task.cancel()
            tasks_to_cancel.append(self._status_task)
        if self._bookmark_task:
            self._bookmark_task.cancel()
            tasks_to_cancel.append(self._bookmark_task)
        
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        
        await super().close()

    # ─── Guild State Management ───────────────────────────────────────────────

    def _get_guild_state(self, guild_id: int) -> Optional[GuildState]:
        """Get state for a guild if it exists."""
        return self.guild_states.get(guild_id)

    async def _initialize_existing_guilds(self) -> None:
        """Initialize state for guilds the bot is already in."""
        for guild in self.guilds:
            await self._ensure_guild_state(guild, create_if_missing=False)

    async def _ensure_guild_state(
        self,
        guild: discord.Guild,
        create_if_missing: bool,
    ) -> Optional[GuildState]:
        """Ensure guild state exists, optionally creating config if missing."""
        if guild.id in self.guild_states:
            return self.guild_states[guild.id]
        
        try:
            config = await load_guild_config(guild.id)
        except ConfigError as exc:
            if not create_if_missing:
                logger.warning("Guild %s not configured: %s", guild.id, exc)
                return None
            
            if self.default_template is None:
                self.default_template = await load_default_template()
            
            try:
                config = await ensure_guild_config(guild.id, self.default_template)
            except ConfigError as exc2:
                logger.error("Failed to seed config for guild %s: %s", guild.id, exc2)
                return None
        
        state = GuildState(self, config)
        await state.start()
        self.guild_states[guild.id] = state
        return state

    async def _remove_guild_state(self, guild_id: int) -> None:
        """Remove and cleanup guild state."""
        state = self.guild_states.pop(guild_id, None)
        if state:
            await state.stop()

    # ─── Guild Events ─────────────────────────────────────────────────────────

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Called when the bot joins a guild."""
        state = await self._ensure_guild_state(guild, create_if_missing=True)
        if state:
            asyncio.create_task(self._auto_snapshot_on_join(state, guild))

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Called when the bot is removed from a guild."""
        await self._remove_guild_state(guild.id)

    # ─── Message Events ───────────────────────────────────────────────────────

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        if message.author.bot:
            return

        # Auto-clear AFK when the user speaks (except when setting AFK).
        try:
            content_l = (message.content or "").strip().lower()
            if not content_l.startswith("afk"):
                from core.utility_storage import UtilityStore

                store = UtilityStore(message.author.id)
                await store.initialize()
                is_afk, _afk_msg = await store.is_afk()
                if is_afk:
                    result = await store.clear_afk()
                    mentions = result.get("mentions") or []
                    if mentions:
                        lines: list[str] = []
                        for m in mentions[:10]:
                            author = m.get("author", "Someone")
                            msg_content = m.get("content", "")
                            guild_id = m.get("guild_id")
                            channel_id = m.get("channel_id")
                            message_id = m.get("message_id")
                            link = ""
                            if guild_id and channel_id and message_id:
                                link = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
                            snippet = msg_content if len(msg_content) <= 200 else f"{msg_content[:197]}..."
                            if link:
                                lines.append(f"- {author}: {snippet}\n  {link}")
                            else:
                                lines.append(f"- {author}: {snippet}")

                        dm_text = (
                            f"AFK cleared automatically. You were mentioned {len(mentions)} times:\n"
                            + "\n".join(lines)
                        )
                        try:
                            await message.author.send(dm_text)
                        except discord.Forbidden:
                            logger.debug("Cannot DM user %s for AFK recap", message.author.id)
        except Exception as e:
            # AFK is a convenience feature; never block message handling.
            logger.debug("AFK auto-clear error: %s", e)
            pass

        # Handle DMs
        if message.guild is None:
            await handle_dm_send(self, message)
            return

        # Invite protection runs before other handlers (can delete messages)
        if await handle_invite_protection(message, self):
            return

        # Check for help command first (before other handlers)
        if await self._handle_help_command(message):
            return

        # Handle auto-responder commands
        if await handle_list_responses_command(message):
            return
        if await handle_add_response_command(message):
            return
        if await handle_remove_response_command(message):
            return

        # Handle admin text commands (before guild state check)
        if await handle_modules_command(message):
            return
        if await handle_verification_command(message, self):
            return
        if await handle_remove_verification_command(message, self):
            return
        if await handle_scanner_command(message, self):
            return
        if await handle_inactivity_command(message, self):
            return
        if await handle_moderation_command(message, self):
            return
        if await handle_serverstats_command(message, self):
            return
        if await handle_server_link_command(message, self):
            return

        # Handle Phase 2-4 module commands
        if await handle_commission_command(message, self):
            return
        if await handle_commission_reviews_command(message, self):
            return
        if await handle_portfolio_command(message, self):
            return
        if await handle_report_command(message, self):
            return
        if await handle_utility_command(message, self):
            return
        if await handle_communication_command(message, self):
            return
        if await handle_art_tools_command(message, self):
            return
        if await handle_art_search_command(message, self):
            return
        if await handle_automation_command(message, self):
            return
        if await handle_roles_command(message, self):
            return
        if await handle_custom_content_command(message, self):
            return
        if await handle_analytics_command(message, self):
            return
        if await handle_trust_command(message, self):
            return

        state = self._get_guild_state(message.guild.id)
        if not state:
            return

        if state.is_channel_ignored(message.channel.id):
            return

        # Notify when mentioning AFK users
        if message.mentions:
            from core.utility_storage import UtilityStore
            afk_lines: list[str] = []
            for user in message.mentions:
                if user.bot:
                    continue
                store = UtilityStore(user.id)
                await store.initialize()
                is_afk, afk_message = await store.is_afk()
                if not is_afk:
                    continue
                await store.add_mention({
                    "author": message.author.display_name,
                    "author_id": message.author.id,
                    "channel_id": message.channel.id,
                    "guild_id": message.guild.id,
                    "message_id": message.id,
                    "content": message.content,
                    "timestamp": utcnow().isoformat(),
                })
                if afk_message:
                    afk_lines.append(f"{user.mention} is AFK: {afk_message}")
                else:
                    afk_lines.append(f"{user.mention} is AFK.")
            if afk_lines:
                await message.reply("\n".join(afk_lines))

        # Run auto-responder with error handling
        async def _safe_auto_responder():
            try:
                await handle_auto_responder(message)
            except Exception as e:
                logger.error("Auto-responder error for message %s: %s", message.id, e)
        
        asyncio.create_task(_safe_auto_responder())

        # Record message for activity tracking with error handling
        async def _safe_record_message():
            try:
                await state.storage.record_message(message.author.id, utcnow())
            except Exception as e:
                logger.error("Failed to record message for user %s: %s", message.author.id, e)
        
        asyncio.create_task(_safe_record_message())

        # Build and enqueue scan jobs if applicable (one per attachment)
        try:
            from services.scanner import is_enabled as scanner_is_enabled
            scanner_on = await scanner_is_enabled(message.guild.id)
        except Exception:
            scanner_on = False

        if scanner_on:
            jobs = state.job_factory.build_jobs_for_message(message)
            for job in jobs:
                await state.enqueue_job(job.to_dict())

    # ─── Interaction Events ───────────────────────────────────────────────────

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle interactions (button clicks, etc.)."""
        await handle_interaction(interaction)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction events used for bookmarks."""
        await handle_bookmark_reaction(payload, self)

    # ─── Enforcement ──────────────────────────────────────────────────────────

    async def enforce_hash_match(
        self,
        guild_id: int,
        message: discord.Message,
        matched_hash: str,
    ) -> None:
        """Handle a hash match - enforce against the user."""
        state = self._get_guild_state(guild_id)
        if not state:
            return
        
        guild = message.guild
        if guild is None or guild.id != guild_id:
            return
        
        member = message.author if isinstance(message.author, discord.Member) else None
        if member is None:
            return
        
        if state.is_exempt(member):
            return
        
        # Get bot's top role for permission checks
        bot_member = guild.get_member(self.user.id) if self.user else None
        bot_top_role = bot_member.top_role if bot_member else None
        
        # Use enforcement service
        result = await state.enforcement.enforce_member(
            member,
            bot_top_role,
            reason="hash match",
            delete_message=message,
        )
        
        state.record_action("hash_match")
        
        # Log the action
        log_text = state.enforcement.format_action_log(
            member,
            result,
            action="image uploaded",
            extra={
                "channel_id": message.channel.id,
                "message_id": message.id,
                "matched_hash": matched_hash,
            },
        )
        await self._post_action_log(state, log_text)

    # ─── Helper Methods ───────────────────────────────────────────────────────

    def is_channel_ignored(self, guild_id: int, channel_id: int) -> bool:
        """Check if a channel is ignored for a guild."""
        state = self._get_guild_state(guild_id)
        return state.is_channel_ignored(channel_id) if state else True

    def is_exempt(self, guild_id: int, member: discord.Member) -> bool:
        """Check if a member is exempt in a guild."""
        state = self._get_guild_state(guild_id)
        return state.is_exempt(member) if state else True

    def has_hash(self, guild_id: int, matched_hash: str) -> bool:
        """Check if a hash matches in a guild."""
        state = self._get_guild_state(guild_id)
        return state.has_hash(matched_hash) if state else False

    async def _post_action_log(self, state: GuildState, text: str) -> None:
        """Post to the action log channel."""
        channel_id = state.config.get(K.ACTION_LOG_CHANNEL_ID)
        if not channel_id:
            return
        
        channel = self.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.fetch_channel(int(channel_id))
            except Exception:
                return
        
        try:
            await channel.send(
                sanitize_text(text),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    # ─── Status ───────────────────────────────────────────────────────────────

    async def _status_loop(self) -> None:
        """Periodically update bot status."""
        while True:
            await self._refresh_status()
            await asyncio.sleep(300)

    async def _refresh_status(self) -> None:
        """Refresh bot status from status.txt file."""
        text = await read_text(resolve_repo_path("status.txt"))
        status = (text or "").strip()
        
        if status == self._last_status:
            return
        
        self._last_status = status
        
        if not status:
            try:
                await self.change_presence(activity=None)
            except Exception:
                pass
            return
        
        status = sanitize_text(status, max_len=128)
        try:
            await self.change_presence(activity=discord.Game(name=status))
        except Exception:
            pass

    # ─── Snapshot ─────────────────────────────────────────────────────────────

    async def _auto_snapshot_on_join(self, state: GuildState, guild: discord.Guild) -> None:
        """Automatically snapshot members when joining a guild."""
        if state.storage.lock_data.get("snapshot_complete"):
            return
        
        async with state.snapshot_lock:
            total_processed = 0
            while True:
                processed, complete = await self._snapshot_step(state, guild)
                if complete:
                    if total_processed > 0:
                        logger.info(
                            "Guild %s snapshot_complete total_processed=%s",
                            state.guild_id,
                            total_processed,
                        )
                    break
                if processed <= 0:
                    break
                total_processed += processed
                if total_processed % 100 == 0:
                    logger.info(
                        "Guild %s snapshot_processed=%s",
                        state.guild_id,
                        total_processed,
                    )
                await asyncio.sleep(1.0)

    async def _snapshot_step(
        self,
        state: GuildState,
        guild: discord.Guild,
    ) -> tuple[int, bool]:
        """Process one batch of member snapshots."""
        if state.storage.lock_data.get("snapshot_complete"):
            return 0, True
        
        state_data = state.storage.state_data
        after = state_data.get("snapshot_after")
        previous_after = safe_int(after) if after else None
        after_obj = discord.Object(id=int(after)) if after else None
        limit = int(state.config.get(K.SNAPSHOT_MEMBERS_PER_RUN, 200))
        
        processed = 0
        last_id: Optional[int] = None
        max_id: Optional[int] = None
        
        async for member in guild.fetch_members(limit=limit, after=after_obj):
            processed += 1
            last_id = member.id
            if max_id is None or member.id > max_id:
                max_id = member.id
            
            joined_at = member.joined_at
            await state.storage.ensure_joined_at(member.id, joined_at)
            
            lock_time = iso_to_dt(state.storage.lock_data.get("initialized_at"))
            grace_until = None
            if lock_time:
                if joined_at is None or joined_at <= lock_time:
                    grace_days = int(state.config.get(K.FIRST_RUN_GRACE_DAYS, 0))
                    grace_until = lock_time + dt.timedelta(days=grace_days)
            await state.storage.set_grace_until(member.id, grace_until)
        
        # Handle completion states
        if processed == 0:
            await state.storage.update_state(lambda s: s.update({"snapshot_complete": True}))
            await state.storage.update_lock(lambda l: l.update({"snapshot_complete": True}))
            return 0, True
        
        next_after = max_id or last_id
        
        if next_after is not None and previous_after is not None and next_after <= previous_after:
            logger.warning("Guild %s snapshot cursor did not advance; marking complete", state.guild_id)
            await state.storage.update_state(lambda s: s.update({"snapshot_complete": True}))
            await state.storage.update_lock(lambda l: l.update({"snapshot_complete": True}))
            return processed, True
        
        if processed < limit:
            await state.storage.update_state(
                lambda s: s.update({"snapshot_after": str(next_after), "snapshot_complete": True})
            )
            await state.storage.update_lock(lambda l: l.update({"snapshot_complete": True}))
            return processed, True
        
        await state.storage.update_state(
            lambda s: s.update({"snapshot_after": str(next_after) if next_after else s.get("snapshot_after")})
        )
        return processed, False

    # ─── Enforcement Loop ─────────────────────────────────────────────────────

    async def _enforce_inactivity_step(
        self,
        state: GuildState,
        guild: discord.Guild,
    ) -> tuple[int, int]:
        """Process one batch of inactivity enforcement."""
        now = utcnow()
        threshold_days = int(state.config.get(K.INACTIVE_DAYS_THRESHOLD, 0))
        max_scan = int(state.config.get(K.ENFORCEMENT_SCAN_MAX_USERS_PER_RUN, 0))
        max_messages = int(state.config.get(K.INACTIVITY_MESSAGE_THRESHOLD, 3))
        
        cursor = state.storage.state_data.get("enforcement_cursor", {"shard": "00", "after": None})
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
        
        bot_member = guild.get_member(self.user.id) if self.user else None
        bot_top_role = bot_member.top_role if bot_member else None
        
        for shard in shards:
            data = await state.storage._read_shard_file(state.storage.shard_path(shard))
            parsed_ids: list[tuple[int, str]] = [
                (safe_int(uid), uid) for uid in data.keys() if safe_int(uid) is not None  # type: ignore
            ]
            parsed_ids.sort(key=lambda item: item[0])
            
            for user_id_int, user_id in parsed_ids:
                if shard == start_shard and after_int is not None and user_id_int is not None and user_id_int <= after_int:
                    continue
                if scanned >= max_scan:
                    break
                
                record = data.get(user_id)
                if not isinstance(record, dict):
                    # Still track position even for invalid records
                    last_scanned_user = user_id
                    last_scanned_shard = shard
                    continue
                
                scanned += 1
                last_scanned_user = user_id
                last_scanned_shard = shard
                
                # Skip already processed
                if record.get("enforced") or record.get("cleared"):
                    continue
                if int(record.get("nonexcluded_messages", 0)) > max_messages:
                    continue
                
                # Check grace period
                grace_until = iso_to_dt(record.get("grace_until"))
                if grace_until and now < grace_until:
                    continue
                
                # Check inactivity threshold
                baseline = iso_to_dt(record.get("joined_at")) or iso_to_dt(
                    state.storage.lock_data.get("initialized_at")
                )
                if baseline is None:
                    continue
                
                last_message = iso_to_dt(record.get("last_message_at"))
                delta = now - (last_message or baseline)
                if delta < dt.timedelta(days=threshold_days):
                    continue
                
                # Get member and check exemption
                member = guild.get_member(user_id_int)
                if member is None or state.is_exempt(member):
                    continue
                
                # Enforce
                result = await state.enforcement.enforce_member(
                    member,
                    bot_top_role,
                    reason="inactivity",
                )
                
                await state.storage.mark_enforced(member.id)
                state.record_action("inactivity")
                
                log_text = state.enforcement.format_action_log(member, result, action="inactivity")
                await self._post_action_log(state, log_text)
                enforced += 1
            
            if scanned >= max_scan:
                break
            # Reset after filter when moving to next shard
            after = None
            after_int = None
        
        # Update cursor
        if last_scanned_user:
            await state.storage.update_state(
                lambda s: s.update({"enforcement_cursor": {"shard": last_scanned_shard, "after": last_scanned_user}})
            )
        elif scanned == 0:
            # Completed all shards with no users scanned - reset to beginning
            await state.storage.update_state(
                lambda s: s.update({"enforcement_cursor": {"shard": "00", "after": None}})
            )
        else:
            await state.storage.update_state(
                lambda s: s.update({"enforcement_cursor": {"shard": "00", "after": None}})
            )
        
        return enforced, scanned

    # ─── Helper Methods ───────────────────────────────────────────────────────

    async def _handle_help_command(self, message: discord.Message) -> bool:
        """
        Handle @bot help command to show all registered module help.
        
        Returns True if the command was handled.
        """
        if not message.guild:
            return False
        
        # Check if bot is mentioned and message contains "help"
        bot_mentioned = False
        if self.user and message.guild.me:
            bot_id = message.guild.me.id
            bot_mentioned = any(mention.id == bot_id for mention in message.mentions)
        
        if not bot_mentioned:
            return False
        
        content = message.content.strip().lower()
        # Remove bot mention from content
        for mention in message.mentions:
            if self.user and mention.id == self.user.id:
                content = content.replace(f"<@{mention.id}>", "")
                content = content.replace(f"<@!{mention.id}>", "")
        
        content = content.strip()
        
        if content != "help":
            return False
        
        # Generate and send help embed
        if help_system.has_modules():
            embed = help_system.get_help_embed()
            await message.reply(embed=embed, mention_author=False)
            return True
        
        return False

    # ─── Commands ─────────────────────────────────────────────────────────────

    async def _register_commands(self) -> None:
        """Register slash commands."""
        
        @app_commands.command(name="commission", description="Show commission info")
        @app_commands.describe(user="User to view commission info for (optional)")
        async def commission_cmd(
            interaction: discord.Interaction,
            user: Optional[discord.User] = None,
        ) -> None:
            target = user or interaction.user
            embed, error = await profile_module.get_commission_embed_for(interaction.user, target)
            
            if error:
                await interaction.response.send_message(
                    sanitize_text(error),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            
            if not isinstance(embed, dict):
                await interaction.response.send_message(
                    "Invalid commission embed.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            
            try:
                embed_obj = discord.Embed.from_dict(embed)
            except Exception:
                await interaction.response.send_message(
                    "Invalid commission embed.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            
            await interaction.response.send_message(
                embed=embed_obj,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        
        self.tree.add_command(commission_cmd)
