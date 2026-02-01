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
from modules.auto_responder import handle_auto_responder
from modules.dm_sender import handle_dm_send
from modules.verification import (
    handle_remove_verification_command,
    handle_verification_command,
    restore_verification_views,
    setup_verification,
)
from services.inactivity import handle_command as handle_inactivity_command
from services.inactivity import restore_state as restore_inactivity_state
from services.scanner import handle_command as handle_scanner_command
from services.scanner import restore_state as restore_scanner_state

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
        self._last_status: Optional[str] = None

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        # Register interaction handlers
        setup_verification()
        
        await self._register_commands()
        await self.tree.sync()

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        if not self.ready_once:
            logger.info("Bot ready as %s", self.user)
            self.ready_once = True

            # Run config migrations (adds new fields without overwriting data)
            await migrate_all_guild_configs()

            # Initialize guild states first
            await self._initialize_existing_guilds()

            # Restore verification buttons from saved data
            await restore_verification_views(self)

            # Restore service states (scanner, inactivity)
            await restore_scanner_state(self)
            await restore_inactivity_state(self)

            self._status_task = asyncio.create_task(self._status_loop())

    async def close(self) -> None:
        """Cleanup when shutting down."""
        for state in list(self.guild_states.values()):
            await state.stop()
        if self._status_task:
            self._status_task.cancel()
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

        # Handle DMs
        if message.guild is None:
            await handle_dm_send(self, message)
            return

        # Handle admin text commands (before guild state check)
        if await handle_verification_command(message, self):
            return
        if await handle_remove_verification_command(message, self):
            return
        if await handle_scanner_command(message, self):
            return
        if await handle_inactivity_command(message, self):
            return

        state = self._get_guild_state(message.guild.id)
        if not state:
            return

        if state.is_channel_ignored(message.channel.id):
            return

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
        jobs = state.job_factory.build_jobs_for_message(message)
        for job in jobs:
            await state.enqueue_job(job.to_dict())

    # ─── Interaction Events ───────────────────────────────────────────────────

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle interactions (button clicks, etc.)."""
        await handle_interaction(interaction)

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
            after = None
            after_int = None
        
        # Update cursor
        if last_scanned_user:
            await state.storage.update_state(
                lambda s: s.update({"enforcement_cursor": {"shard": last_scanned_shard, "after": last_scanned_user}})
            )
        else:
            await state.storage.update_state(
                lambda s: s.update({"enforcement_cursor": {"shard": "00", "after": None}})
            )
        
        return enforced, scanned

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
