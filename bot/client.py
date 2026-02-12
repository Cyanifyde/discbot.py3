"""
Discord bot client - lean event handling and command registration.

This is the main Discord client class, refactored to be much leaner.
Business logic is delegated to services and GuildState.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Awaitable, Callable, Optional

import discord
from discord import app_commands

from core.config import (
    ConfigError,
    OWNER_ID,
    ensure_guild_config,
    load_default_template,
    load_guild_config,
)
from core.config_migration import migrate_all_guild_configs
from core.constants import K
from core.error_handler import (
    handle_command_error,
    handle_event_error,
    handle_interaction_error,
    handle_slash_command_error,
)
from core.interactions import handle_interaction
from core.io_utils import read_json, read_text
from core.paths import resolve_repo_path
from core.utils import dt_to_iso, iso_to_dt, safe_int, sanitize_text, utcnow
from core.help_system import help_system
from modules import auto_responder as auto_responder_module
from modules import dm_sender as dm_sender_module
from modules import invite_protection as invite_protection_module
from modules import roles as roles_module
from modules import utility as utility_module
from modules.verification import (
    restore_verification_views,
    setup_verification,
)
from modules.moderation import (
    setup_moderation,
)
from modules.server_stats import (
    setup_server_stats,
)
from modules.server_link import (
    setup_server_link,
)
from modules.reports import (
    setup_reports,
)
from modules.utility import (
    setup_utility,
)
from modules.communication import (
    setup_communication,
)
from modules.art_tools import (
    setup_art_tools,
)
from modules.art_search import (
    setup_art_search,
)
from modules.automation import (
    setup_automation,
)
from modules.roles import (
    restore_reaction_roles,
    setup_roles,
)
from modules.custom_content import (
    setup_custom_content,
)
from modules.invite_protection import (
    setup_invite_protection,
)
from services.inactivity import (
    restore_state as restore_inactivity_state,
    setup_inactivity_service,
)
from services.scanner import (
    get_bootstrap_state as get_scanner_bootstrap_state,
    restore_state as restore_scanner_state,
    setup_scanner_service,
)
from services.ptc_service import (
    handle_ptc_message as ptc_handle_message,
    handle_ptc_reaction as ptc_handle_reaction,
    restore_ptc_state,
    setup_ptc,
    cleanup_ptc,
)
from services.sync_service import setup_sync_interactions
from modules.modules_command import register_help as register_modules_help
from services.hot_reload_service import (
    start as start_hot_reload_service,
    stop as stop_hot_reload_service,
)

from .guild_state import GuildState
from .auto_responder_dispatcher import AutoResponderDispatcher

from services.user_utility_service import UserUtilityService
from services.bookmark_scheduler import BookmarkScheduler

logger = logging.getLogger("discbot")

AutoResponderHandler = Callable[[discord.Message], Awaitable[bool]]

RuntimeHookMap = dict[str, Callable[..., Awaitable[Any]]]


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
        self._questions_cache: Optional[dict[str, str]] = None
        self._questions_allowed_user_ids: Optional[set[int]] = None
        self._questions_cache_mtime: Optional[float] = None
        self.started_at = utcnow()
        self._runtime_hooks: RuntimeHookMap = {}
        self._init_runtime_hooks()

        # AI Detection resource limits
        self._ai_detection_semaphore = asyncio.Semaphore(1)  # Only 1 concurrent detection
        self._ai_detection_timeout = 120  # 2 minutes max per detection

        # Bounded dispatchers / schedulers for high throughput workloads
        self.auto_responder = AutoResponderDispatcher(queue_max=5000, worker_count=4)
        self.user_utility = UserUtilityService(max_stores=5000, afk_cache_ttl_seconds=5.0)
        self.bookmark_scheduler = BookmarkScheduler()

    def _init_runtime_hooks(self) -> None:
        """Set default runtime hook bindings."""
        self.set_runtime_hook("dm_send", dm_sender_module.handle_dm_send)
        self.set_runtime_hook("invite_protection", invite_protection_module.handle_invite_protection)
        self.set_runtime_hook("ptc_message", ptc_handle_message)
        self.set_runtime_hook("ptc_reaction", ptc_handle_reaction)
        self.set_runtime_hook("bookmark_reaction", utility_module.handle_bookmark_reaction)
        self.set_runtime_hook("reaction_role_event", roles_module.handle_reaction_role_event)
        self.set_runtime_hook("auto_responder", auto_responder_module.handle_auto_responder)

    def set_runtime_hook(self, name: str, func: Callable[..., Awaitable[Any]]) -> None:
        self._runtime_hooks[name] = func

    def get_runtime_hook(self, name: str) -> Optional[Callable[..., Awaitable[Any]]]:
        return self._runtime_hooks.get(name)

    def get_runtime_hooks_snapshot(self) -> RuntimeHookMap:
        return dict(self._runtime_hooks)

    def restore_runtime_hooks(self, snapshot: RuntimeHookMap) -> None:
        self._runtime_hooks = dict(snapshot)

    def get_auto_responder_handler(self) -> Optional[AutoResponderHandler]:
        hook = self.get_runtime_hook("auto_responder")
        if hook is None:
            return None
        return hook  # type: ignore[return-value]

    async def _safe_dispatch(self, handler_coro, message: discord.Message) -> bool:
        """Wrap a command handler coroutine with centralised error handling."""
        try:
            return await handler_coro
        except Exception as e:
            await handle_command_error(e, message)
            return True

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        # Register interaction handlers
        setup_verification()
        auto_responder_module.setup_auto_responder()
        setup_moderation()
        setup_server_stats()
        setup_server_link()
        setup_sync_interactions()
        setup_scanner_service()
        setup_inactivity_service()

        # Register additional modules
        setup_reports()
        setup_utility()
        setup_communication()
        setup_art_tools()
        setup_art_search()
        setup_automation()
        setup_roles()
        setup_custom_content()
        setup_invite_protection()
        setup_ptc()

        # Register help for the modules management command early so it appears in @bot help.
        register_modules_help()

        expected_help = {
            "Art Search",
            "Art Tools",
            "Auto-Responder",
            "Automation",
            "Communication",
            "Custom Content",
            "Inactivity Enforcement",
            "Invite Protection",
            "Moderation",
            "Module Management",
            "Reports",
            "Roles",
            "Scanner",
            "Server Link",
            "Server Stats",
            "Utility",
            "Verification",
            "Pass the Canvas",
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

            logger.info("Startup restore pipeline: begin")

            # Initialize guild states first
            logger.info("Startup step 1/6 initialize_guild_states: begin")
            await self._initialize_existing_guilds()
            logger.info("Startup step 1/6 initialize_guild_states: done")

            # Restore verification buttons from saved data
            logger.info("Startup step 2/6 restore_verification_views: begin")
            try:
                await restore_verification_views(self)
            except Exception as e:
                await handle_event_error(e, context="restore_verification_views")
            finally:
                logger.info("Startup step 2/6 restore_verification_views: done")

            # Restore service states (inactivity)
            logger.info("Startup step 3/6 restore_inactivity_state: begin")
            try:
                await restore_inactivity_state(self)
            except Exception as e:
                await handle_event_error(e, context="restore_inactivity_state")
            finally:
                logger.info("Startup step 3/6 restore_inactivity_state: done")

            # Reconcile scanner runtime state after early bootstrap.
            logger.info("Startup step 4/6 restore_scanner_state: begin")
            try:
                await restore_scanner_state(self)
            except Exception as e:
                await handle_event_error(e, context="restore_scanner_state")
            finally:
                logger.info("Startup step 4/6 restore_scanner_state: done")

            # Restore PTC state (timers, etc.)
            logger.info("Startup step 5/6 restore_ptc_state: begin")
            try:
                await restore_ptc_state(self)
            except Exception as e:
                await handle_event_error(e, context="restore_ptc_state")
            finally:
                logger.info("Startup step 5/6 restore_ptc_state: done")

            # Restore reaction role emojis
            logger.info("Startup step 6/6 restore_reaction_roles: begin")
            try:
                await restore_reaction_roles(self)
            except Exception as e:
                await handle_event_error(e, context="restore_reaction_roles")
            finally:
                logger.info("Startup step 6/6 restore_reaction_roles: done")

            logger.info("Startup restore pipeline: done")

            # Start bounded dispatchers/schedulers
            try:
                await self.auto_responder.start(self)
            except Exception as e:
                await handle_event_error(e, context="auto_responder_start")
            try:
                await self.bookmark_scheduler.start(self)
            except Exception as e:
                await handle_event_error(e, context="bookmark_scheduler_start")
            try:
                await self.bookmark_scheduler.restore_from_index(self)
            except Exception as e:
                await handle_event_error(e, context="bookmark_scheduler_restore")

            self._status_task = asyncio.create_task(self._status_loop())

            try:
                await start_hot_reload_service(self)
            except Exception as e:
                await handle_event_error(e, context="hot_reload_start")

    async def close(self) -> None:
        """Cleanup when shutting down."""
        await stop_hot_reload_service()

        for state in list(self.guild_states.values()):
            await state.stop()

        await self.auto_responder.stop()
        await self.bookmark_scheduler.stop()
        await cleanup_ptc()
        
        # Cancel and await background tasks
        tasks_to_cancel = []
        if self._status_task:
            self._status_task.cancel()
            tasks_to_cancel.append(self._status_task)
        
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
        
        start_scanner = False
        startup_guild_hashes: set[str] = set()
        try:
            start_scanner, startup_guild_hashes = await get_scanner_bootstrap_state(guild.id)
        except Exception as e:
            logger.warning(
                "Scanner bootstrap read failed for guild %s; using defaults: %s",
                guild.id,
                e,
            )

        state = GuildState(self, config)
        await state.start(
            start_scanner=start_scanner,
            startup_guild_hashes=startup_guild_hashes,
        )
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
                is_afk, _afk_msg = await self.user_utility.is_afk(message.author.id)
                if is_afk:
                    result = await self.user_utility.clear_afk(message.author.id)
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
            try:
                dm_hook = self.get_runtime_hook("dm_send")
                if dm_hook is not None:
                    await dm_hook(self, message)
            except Exception as e:
                await handle_event_error(e, context="dm_send", channel_id=message.channel.id)
            return

        # Invite protection runs before other handlers (can delete messages)
        try:
            invite_hook = self.get_runtime_hook("invite_protection")
            if invite_hook and await invite_hook(message, self):
                return
        except Exception as e:
            await handle_event_error(e, context="invite_protection", guild_id=message.guild.id, channel_id=message.channel.id)

        # PTC enforcement: intercepts ALL messages in bound threads + ptc commands
        try:
            ptc_hook = self.get_runtime_hook("ptc_message")
            if ptc_hook and await ptc_hook(message, self):
                return
        except Exception as e:
            await handle_event_error(e, context="ptc_message", guild_id=message.guild.id, channel_id=message.channel.id)

        # Check for help command first (before other handlers)
        try:
            if await self._handle_help_command(message):
                return
        except Exception as e:
            await handle_command_error(e, message)
        # Central command dispatch via registry.
        content = (message.content or "").strip()
        if content:
            from core.command_registry import command_registry
            if await command_registry.dispatch(message, self):
                return

        state = self._get_guild_state(message.guild.id)
        if not state:
            return

        if state.is_channel_ignored(message.channel.id):
            return

        # Notify when mentioning AFK users
        if message.mentions:
            afk_lines: list[str] = []
            for user in message.mentions:
                if user.bot:
                    continue
                is_afk, afk_message = await self.user_utility.is_afk(user.id)
                if not is_afk:
                    continue
                await self.user_utility.add_mention(user.id, {
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

        # Bounded auto-responder processing (drops on overload).
        self.auto_responder.submit(message)

        # Record message for activity tracking inline (no per-message task spawning).
        try:
            await state.storage.record_message(message.author.id, utcnow())
        except Exception as e:
            await handle_event_error(e, context="record_message", guild_id=message.guild.id, channel_id=message.channel.id)

        # Build and enqueue scan jobs if applicable (one per attachment)
        try:
            from services.scanner import is_enabled as scanner_is_enabled
            scanner_on = await scanner_is_enabled(message.guild.id)
        except Exception:
            scanner_on = False

        if scanner_on:
            try:
                jobs = state.job_factory.build_jobs_for_message(message)
                for job in jobs:
                    await state.enqueue_job(job.to_dict())
            except Exception as e:
                await handle_event_error(e, context="scanner_enqueue", guild_id=message.guild.id, channel_id=message.channel.id)

    # ─── Interaction Events ───────────────────────────────────────────────────

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle interactions (button clicks, etc.)."""
        try:
            await handle_interaction(interaction)
        except Exception as e:
            await handle_interaction_error(e, interaction)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction events (bookmarks, reaction roles)."""
        try:
            bookmark_hook = self.get_runtime_hook("bookmark_reaction")
            if bookmark_hook:
                await bookmark_hook(payload, self)
        except Exception as e:
            await handle_event_error(e, context="bookmark_reaction", guild_id=payload.guild_id, channel_id=payload.channel_id)
        try:
            reaction_role_hook = self.get_runtime_hook("reaction_role_event")
            if reaction_role_hook:
                await reaction_role_hook(payload, self, added=True)
        except Exception as e:
            await handle_event_error(e, context="reaction_role_add", guild_id=payload.guild_id, channel_id=payload.channel_id)
        try:
            ptc_reaction_hook = self.get_runtime_hook("ptc_reaction")
            if ptc_reaction_hook:
                await ptc_reaction_hook(payload, self)
        except Exception as e:
            await handle_event_error(e, context="ptc_reaction", guild_id=payload.guild_id, channel_id=payload.channel_id)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction remove events (reaction roles)."""
        try:
            reaction_role_hook = self.get_runtime_hook("reaction_role_event")
            if reaction_role_hook:
                await reaction_role_hook(payload, self, added=False)
        except Exception as e:
            await handle_event_error(e, context="reaction_role_remove", guild_id=payload.guild_id, channel_id=payload.channel_id)

    # ─── Enforcement ──────────────────────────────────────────────────────────

    async def enforce_hash_match(
        self,
        guild_id: int,
        message: discord.Message,
        matched_hash: str,
    ) -> None:
        """Handle a hash match - enforce against the user."""
        try:
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
        except Exception as e:
            await handle_event_error(e, context="enforce_hash_match", guild_id=guild_id, channel_id=message.channel.id)

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
        
        if not content.startswith("help"):
            return False

        # @bot help
        if content == "help":
            if help_system.has_modules():
                embed = help_system.get_help_embed()
                await message.reply(embed=embed, mention_author=False)
                return True
            return False

        # @bot help me (permission-filtered)
        if content in {"help me", "help my", "help mine", "help available"}:
            member = message.author if isinstance(message.author, discord.Member) else None
            if member is None:
                return False

            is_owner = member.id == OWNER_ID
            is_admin = bool(member.guild_permissions.administrator)
            is_mod = bool(
                is_admin
                or member.guild_permissions.manage_guild
                or member.guild_permissions.manage_roles
                or member.guild_permissions.manage_messages
                or member.guild_permissions.ban_members
                or member.guild_permissions.kick_members
            )

            embed = help_system.get_available_commands_embed(
                title="Available Commands",
                allow_mod=is_mod,
                allow_admin=is_admin,
                allow_owner=is_owner,
                include_hidden=True,
            )
            await message.reply(embed=embed, mention_author=False)
            return True

        return False
        
    # ─── Commands ─────────────────────────────────────────────────────────────

    async def _run_ai_detection(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        initial_embed: discord.Embed,
        version: int,
    ) -> None:
        """Run AI detection on an image with progress updates."""
        import sys
        import io
        import time
        from pathlib import Path
        from PIL import Image as PILImage
        
        # Feature families in model v1 (12 total)
        FAMILIES = [
            'color', 'frequency', 'spectral_diffusion', 'noise',
            'texture', 'gradient', 'forensic', 'model_specific',
            'nss', 'cfa', 'self_similarity', 'residual'
        ]
        
        # Try to acquire semaphore (only allow 1 concurrent detection)
        if not self._ai_detection_semaphore.locked():
            acquired = True
        else:
            # Already running, notify user
            initial_embed.description = (
                "**Queue Full**\n"
                "Another AI detection is currently running. "
                "Please wait and try again in a moment.\n\n"
                "*Only 1 detection can run at a time to prevent server overload.*"
            )
            initial_embed.color = discord.Color.yellow()
            initial_embed.set_field_at(0, name="Status", value="Queued (waiting for slot)", inline=False)
            await interaction.edit_original_response(embed=initial_embed)
            
            # Wait for semaphore with timeout
            try:
                async with asyncio.timeout(300):  # 5 minute max queue wait
                    await self._ai_detection_semaphore.acquire()
                acquired = True
            except asyncio.TimeoutError:
                error_embed = discord.Embed(
                    title=f"AI Detection Analysis - Queue Timeout (Model v{version})",
                    description="Queue wait time exceeded. Please try again later.",
                    color=discord.Color.red(),
                )
                error_embed.set_footer(text=f"Timeout: {image.filename}")
                await interaction.edit_original_response(embed=error_embed)
                return
        
        if not acquired:
            return
        
        last_update_time = 0
        current_family_idx = 0
        
        async def progress_callback(family_name: str, family_idx: int, total_families: int):
            """Update progress embed - rate limited to avoid Discord API limits."""
            nonlocal last_update_time, current_family_idx
            current_family_idx = family_idx
            
            # Rate limit: Update at most once per 2 seconds (Discord allows ~5 per 5 seconds)
            current_time = time.time()
            if current_time - last_update_time < 2.0:
                return
            
            try:
                last_update_time = current_time
                initial_embed.description = (
                    "**Early Test Model - Use with caution**\\n"
                    "This is an experimental model that may produce incorrect results. "
                    "It analyzes pixel-level features but cannot detect tracing or heavily edited images.\\n\\n"
                    f"Status: Analyzing features... ({family_name})"
                )
                initial_embed.color = discord.Color.orange()
                initial_embed.set_field_at(
                    0, 
                    name="Progress", 
                    value=f"Running features {family_idx + 1}/{total_families}",
                    inline=False
                )
                await interaction.edit_original_response(embed=initial_embed)
            except discord.errors.NotFound:
                # Interaction expired or message deleted
                pass
            except Exception as e:
                logger.debug(f"Failed to update progress: {e}")
        
        try:
            # Set resource limits for the detection process
            self._set_process_limits()
            
            # Download image first
            image_data = await image.read()
            pil_image = PILImage.open(io.BytesIO(image_data))
            
            # Convert to RGB if needed
            if pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
            
            # Import detection module
            sys.path.insert(0, str(Path(__file__).parent.parent / "for_discord"))
            try:
                # Model versioning - load different models based on version
                if version == 1:
                    from detect import predict_image_with_progress
                    
                    # Wrap in thread to run with progress updates and timeout
                    try:
                        loop = asyncio.get_running_loop()
                        ai_probability = await asyncio.wait_for(
                            asyncio.to_thread(
                                self._run_detection_with_progress,
                                pil_image,
                                progress_callback,
                                FAMILIES,
                                loop,
                            ),
                            timeout=self._ai_detection_timeout,
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError(f"Detection exceeded {self._ai_detection_timeout}s timeout")
                else:
                    # Future versions would go here
                    raise ValueError(f"Model version {version} not implemented")
                
            finally:
                sys.path.pop(0)
            
            # Determine verdict
            if ai_probability > 0.9:
                verdict = "AI Generated"
                confidence = "High Confidence"
                color = discord.Color.red()
                interpretation = "This image appears to be AI-generated with high certainty."
            elif ai_probability > 0.7:
                verdict = "Likely AI"
                confidence = "Medium Confidence"
                color = discord.Color.orange()
                interpretation = "This image likely contains AI-generated content."
            elif ai_probability < 0.3:
                verdict = "Real Image"
                confidence = "High Confidence"
                color = discord.Color.green()
                interpretation = "This image appears to be real/human-made with high certainty."
            elif ai_probability < 0.5:
                verdict = "Likely Real"
                confidence = "Medium Confidence"
                color = discord.Color.blue()
                interpretation = "This image likely contains real/human-made content."
            else:
                verdict = "Uncertain"
                confidence = "Low Confidence"
                color = discord.Color.greyple()
                interpretation = "Unable to determine with confidence. Manual review recommended."
            
            # Create result embed with disclaimer
            result_embed = discord.Embed(
                title=f"AI Detection Analysis - Complete (Model v{version})",
                description=interpretation,
                color=color,
            )
            result_embed.add_field(name="Verdict", value=verdict, inline=True)
            result_embed.add_field(name="Confidence", value=confidence, inline=True)
            result_embed.add_field(
                name="AI Probability",
                value=f"{ai_probability:.1%} (Score: {ai_probability:.3f})",
                inline=False
            )
            result_embed.add_field(
                name="Real Probability",
                value=f"{(1-ai_probability):.1%} (Score: {(1-ai_probability):.3f})",
                inline=False
            )
            result_embed.add_field(
                name="Limitations",
                value=(
                    "This model cannot detect:\n"
                    "• Tracing over real images\n"
                    "• Heavy manual edits\n"
                    "• Hybrid AI+human work\n"
                    "• Very new AI models\n\n"
                    "*This is an early test model and may produce false results.*"
                ),
                inline=False
            )
            result_embed.set_footer(text=f"Analyzed: {image.filename} | Model v{version}")
            
            await interaction.edit_original_response(embed=result_embed)
            
        except Exception as e:
            logger.error("AI detection failed: %s", e, exc_info=True)
            error_embed = discord.Embed(
                title=f"AI Detection Analysis - Error (Model v{version})",
                description=f"Failed to analyze image: {str(e)}",
                color=discord.Color.red(),
            )
            error_embed.set_footer(text=f"Error analyzing: {image.filename}")
            try:
                await interaction.edit_original_response(embed=error_embed)
            except Exception:
                # If we can't edit the message, log the error
                logger.error("Failed to send error message for AI detection")
        finally:
            # Always release the semaphore
            if acquired:
                self._ai_detection_semaphore.release()
    
    def _run_detection_with_progress(
        self,
        pil_image,
        progress_callback,
        families,
        loop: asyncio.AbstractEventLoop,
    ):
        """Run detection in a thread with progress updates."""
        import sys
        from pathlib import Path
        
        # Re-import in thread context
        sys.path.insert(0, str(Path(__file__).parent.parent / "for_discord"))
        try:
            from detect import predict_image_with_progress
            
            # Synchronous callback adapter
            def sync_progress(family_name, idx, total):
                # Schedule async callback in event loop
                try:
                    asyncio.run_coroutine_threadsafe(
                        progress_callback(family_name, idx, total),
                        loop,
                    )
                except Exception:
                    pass
            
            return predict_image_with_progress(pil_image, sync_progress)
        finally:
            sys.path.pop(0)
    
    def _set_process_limits(self) -> None:
        """Set resource limits for AI detection process."""
        try:
            import os
            import sys
            
            # Lower process priority (nice value)
            if sys.platform != 'win32':
                try:
                    import os
                    current_nice = os.nice(0)
                    if current_nice < 10:
                        os.nice(10)  # Lower priority
                except (OSError, AttributeError):
                    pass
            else:
                # Windows priority
                try:
                    import psutil
                    p = psutil.Process()
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                except (ImportError, Exception):
                    pass
            
            # Set memory limit (if on Linux/Unix)
            if sys.platform != 'win32':
                try:
                    import resource
                    # Limit virtual memory to 2GB
                    resource.setrlimit(
                        resource.RLIMIT_AS,
                        (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024)
                    )
                except (ImportError, OSError):
                    pass
        except Exception as e:
            logger.debug(f"Failed to set process limits: {e}")
    
    async def _register_commands(self) -> None:
        """Register slash commands."""

        async def _load_questions_data() -> tuple[dict[str, str], Optional[set[int]]]:
            """
            Load questions from a JSON file and normalize into:
            - dict mapping normalized question -> answer
            - optional allowed user id set (None means allow everyone)

            Supported formats:
            - Object: { "question": "answer", ... }
            - Object with wrapper: { "allowed_user_ids": [...], "questions": { "question": "answer" } }
            - List: [ { "question": "...", "answer": "..." }, { "q": "...", "a": "..." } ]
            """
            import os

            path = resolve_repo_path(os.getenv("QUESTIONS_JSON_PATH", "templates/questions.json"))
            try:
                stat = await asyncio.to_thread(path.stat)
                mtime = float(stat.st_mtime)
            except FileNotFoundError:
                self._questions_cache = {}
                self._questions_allowed_user_ids = set()
                self._questions_cache_mtime = None
                return {}, set()
            except Exception:
                # Fall back to uncached load if stat fails for any reason
                mtime = None

            if (
                self._questions_cache is not None
                and self._questions_allowed_user_ids is not None
                and self._questions_cache_mtime is not None
                and mtime is not None
                and self._questions_cache_mtime == mtime
            ):
                return self._questions_cache, self._questions_allowed_user_ids

            raw = await read_json(path, default=None)
            mapping: dict[str, str] = {}
            allowed_ids: Optional[set[int]] = None

            def _norm(text: str) -> str:
                return " ".join(text.strip().lower().split())

            if isinstance(raw, dict):
                # Wrapper format: { allowed_user_ids: [...], questions: {...} }
                raw_questions = raw
                if "questions" in raw and isinstance(raw.get("questions"), dict):
                    raw_questions = raw.get("questions")  # type: ignore[assignment]

                if "allowed_user_ids" in raw and isinstance(raw.get("allowed_user_ids"), list):
                    allowed_ids = set()
                    for v in raw.get("allowed_user_ids") or []:
                        if isinstance(v, int):
                            allowed_ids.add(v)
                        elif isinstance(v, str) and v.strip().isdigit():
                            allowed_ids.add(int(v.strip()))

                for q, a in raw_questions.items():
                    if isinstance(q, str) and isinstance(a, str):
                        nq = _norm(q)
                        if nq:
                            mapping[nq] = a
            elif isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    q = item.get("question") or item.get("q")
                    a = item.get("answer") or item.get("a")
                    if isinstance(q, str) and isinstance(a, str):
                        nq = _norm(q)
                        if nq:
                            mapping[nq] = a

            self._questions_cache = mapping
            self._questions_allowed_user_ids = allowed_ids
            self._questions_cache_mtime = mtime
            return mapping, allowed_ids

        @app_commands.command(name="question", description="Ask a question and get a saved answer")
        @app_commands.describe(question="Your question")
        async def question_cmd(interaction: discord.Interaction, question: str) -> None:
            try:
                def _norm(text: str) -> str:
                    return " ".join(text.strip().lower().split())

                q_norm = _norm(question)
                if not q_norm:
                    await interaction.response.send_message(
                        "Please provide a question.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return

                questions_map, allowed_ids = await _load_questions_data()
                if allowed_ids is not None and interaction.user.id not in allowed_ids:
                    await interaction.response.send_message(
                        "You are not allowed to use this command.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                answer = questions_map.get(q_norm)

                if not answer:
                    await interaction.response.send_message(
                        "No saved answer found for that question.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return

                embed = discord.Embed(title="Question")
                embed.add_field(name="User asked", value=sanitize_text(question), inline=False)
                embed.add_field(name="Answer", value=sanitize_text(answer), inline=False)

                await interaction.response.send_message(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as e:
                await handle_slash_command_error(e, interaction, "question")

        self.tree.add_command(question_cmd)
        @app_commands.command(name="check_ai", description="Check if an image is AI-generated")
        @app_commands.describe(
            image="Image to analyze",
            version="Model version to use (default: 1)"
        )
        async def check_ai_cmd(
            interaction: discord.Interaction,
            image: discord.Attachment,
            version: int = 1,
        ) -> None:
            try:
                # Validate version
                if version not in [1]:
                    await interaction.response.send_message(
                        "Invalid model version. Available versions: 1",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                
                # Validate that it's an image
                if not image.content_type or not image.content_type.startswith("image/"):
                    await interaction.response.send_message(
                        "Please provide a valid image file.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                
                # Check file size (max 10MB)
                if image.size > 10 * 1024 * 1024:
                    await interaction.response.send_message(
                        "Image is too large. Maximum size is 10MB.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                
                # Send initial embed with disclaimer
                embed = discord.Embed(
                    title=f"AI Detection Analysis (Model v{version})",
                    description=(
                        "**Early Test Model - Use with caution**\n"
                        "This is an experimental model that may produce incorrect results. "
                        "It analyzes pixel-level features but cannot detect tracing or heavily edited images.\n\n"
                        "Status: Queued..."
                    ),
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Progress", value="Queued 1/1", inline=False)
                embed.set_footer(text=f"Analyzing: {image.filename} | Model v{version}")
                
                await interaction.response.send_message(embed=embed)
                
                # Run analysis in background
                asyncio.create_task(self._run_ai_detection(interaction, image, embed, version))
            except Exception as e:
                await handle_slash_command_error(e, interaction, "check_ai")
        
        self.tree.add_command(check_ai_cmd)
