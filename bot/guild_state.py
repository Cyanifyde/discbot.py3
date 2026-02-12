"""
Guild state management.

Extracted from bot.py to separate concerns and improve testability.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

import discord

from core.constants import K
from core.hashes import load_hashes
from core.queueing import QueueProcessor, QueueStore
from core.storage import SuspicionStore
from core.utils import build_cdn_regex, utcnow
from services.enforcement import EnforcementService
from services.job_factory import JobFactory

if TYPE_CHECKING:
    from .client import DiscBot

logger = logging.getLogger("discbot.guild")


class GuildState:
    """
    Manages state for a single Discord guild.
    
    Each guild has its own:
    - Configuration
    - Hash set for image matching
    - Storage for user records
    - Job queue for processing
    - Services for enforcement and job creation
    """

    def __init__(self, bot: "DiscBot", config: dict[str, Any]) -> None:
        self.bot = bot
        self.config = config
        self.guild_id = int(config.get(K.GUILD_ID, 0))
        
        # Hash set for image matching
        self.hashes: set[str] = set()
        
        # Storage and queuing
        self.storage = SuspicionStore(self.guild_id, cache_size=3)
        self.queue_store = QueueStore(self.storage.root)
        self.queue_processor = QueueProcessor(bot, self.queue_store, self.storage, config)
        
        # Services
        self.enforcement = EnforcementService(config)
        self.job_factory = JobFactory(config)
        
        # Cached regex for CDN URL matching
        self.cdn_regex = build_cdn_regex(config.get(K.ALLOWED_DISCORD_CDN_DOMAINS, []))
        
        # Runtime state
        self.start_time = utcnow()
        self.queue_state_task: Optional[asyncio.Task] = None
        self.enforcement_task: Optional[asyncio.Task] = None
        self.action_count = 0
        self.snapshot_lock = asyncio.Lock()
        
        # Cache frequently accessed config values
        self._refresh_config_cache()

    def _refresh_config_cache(self) -> None:
        """Cache frequently accessed config values for performance."""
        self._ignored_channels = set(self.config.get(K.IGNORED_CHANNEL_IDS, []))
        self._excluded_channels = set(self.config.get(K.EXCLUDED_CHANNEL_IDS, []))
        self._exempt_roles = set(self.config.get(K.EXEMPT_ROLE_IDS, []))
        self._exemptions = set(self.config.get(K.EXEMPTIONS, []))

    def update_config(self, config: dict[str, Any]) -> None:
        """Update guild configuration."""
        self.config = config
        self.guild_id = int(config.get(K.GUILD_ID, 0))
        self.cdn_regex = build_cdn_regex(config.get(K.ALLOWED_DISCORD_CDN_DOMAINS, []))
        
        # Update services
        self.enforcement.update_config(config)
        self.job_factory.update_config(config)
        self.queue_processor.update_config(config)
        
        self._refresh_config_cache()

    async def start(
        self,
        start_scanner: bool = False,
        startup_guild_hashes: Optional[set[str]] = None,
    ) -> None:
        """
        Initialize and start background tasks.
        
        Args:
            start_scanner: Whether to start the sus scanner queue processor.
                          If False (default), scanner must be enabled via 'sus enable'.
            startup_guild_hashes: Optional per-guild hashes from persisted scanner
                                 module data loaded during early startup.
        """
        await self.storage.initialize()
        await self.queue_store.initialize()
        self.hashes = await load_hashes(self.config)
        if startup_guild_hashes:
            self.hashes.update(startup_guild_hashes)
        # Configure delayed shard flush without a periodic loop.
        try:
            interval = float(self.config.get(K.QUEUE_FLUSH_INTERVAL_SECONDS, 30))
        except Exception:
            interval = 30.0
        self.storage.flush_delay_seconds = max(5.0, interval)
        
        # Only start queue processor if explicitly requested
        # The sus scanner module handles starting this based on saved state
        if start_scanner:
            await self.queue_processor.start()
            self._ensure_queue_state_task()
        
        # Start periodic tasks (only those that are still needed)
        # queue_state_task only runs while the scanner is running.
        self.enforcement_task = asyncio.create_task(self._periodic_enforcement())
        
        logger.info(
            "Guild %s state started (scanner_running=%s, hashes=%d)",
            self.guild_id,
            self.is_scanner_running(),
            len(self.hashes),
        )

    async def stop(self) -> None:
        """Stop background tasks and flush state."""
        # Cancel background tasks
        if self.queue_state_task:
            self.queue_state_task.cancel()
        if self.enforcement_task:
            self.enforcement_task.cancel()
        
        await asyncio.gather(
            *(t for t in [self.queue_state_task, self.enforcement_task] if t),
            return_exceptions=True,
        )
        
        # Stop processor and flush storage
        await self.queue_processor.stop()
        await self.storage.flush_all()
        await self.queue_store.update_state(
            self.queue_processor.read_offset_bytes,
            self.queue_processor.queued_jobs,
        )
        
        logger.info("Guild %s state stopped", self.guild_id)

    def _ensure_queue_state_task(self) -> None:
        """
        Ensure queue state flushing runs while the scanner is running.

        This avoids an always-on periodic loop when the scanner is disabled.
        """
        if self.queue_state_task and not self.queue_state_task.done():
            return
        self.queue_state_task = asyncio.create_task(self._queue_state_flush_loop())

    async def _queue_state_flush_loop(self) -> None:
        import random
        base_interval = float(self.config.get(K.QUEUE_STATE_FLUSH_INTERVAL_SECONDS, 15))
        jitter = random.uniform(0, base_interval * 0.1)
        await asyncio.sleep(jitter)

        while True:
            try:
                # Exit when scanner stops.
                if self.queue_processor.stop_event.is_set():
                    return
                try:
                    await asyncio.wait_for(self.queue_processor.stop_event.wait(), timeout=base_interval)
                    return
                except asyncio.TimeoutError:
                    pass

                await self.queue_store.update_state(
                    self.queue_processor.read_offset_bytes,
                    self.queue_processor.queued_jobs,
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(
                    "Error in queue state flush for guild %s: %s",
                    self.guild_id,
                    e,
                    exc_info=True,
                )
                await asyncio.sleep(min(base_interval, 60))

    async def _periodic_enforcement(self) -> None:
        """Periodically run inactivity enforcement if enabled."""
        from core.permissions import is_module_enabled
        from services.inactivity import is_enabled, run_enforcement_step, increment_stats
        
        interval = float(self.config.get(K.ENFORCEMENT_INTERVAL_SECONDS, 21600))
        
        while True:
            await asyncio.sleep(interval)
            
            try:
                if not await is_module_enabled(self.guild_id, "inactivity"):
                    continue
                if not await is_enabled(self.guild_id):
                    continue
                
                guild = self.bot.get_guild(self.guild_id)
                if not guild:
                    logger.warning(
                        "Guild %s not found for scheduled enforcement",
                        self.guild_id,
                    )
                    continue
                
                logger.info("Running scheduled enforcement for guild %s", self.guild_id)
                
                enforced, scanned, skip_reasons = await run_enforcement_step(self.bot, self, guild)
                await increment_stats(self.guild_id, enforced=enforced, scanned=scanned)
                
                # Persist skip reasons for later display
                from services.inactivity import update_state
                await update_state(self.guild_id, {"last_skip_reasons": skip_reasons})
                
                logger.info(
                    "Scheduled enforcement complete for guild %s: scanned=%d enforced=%d skips=%s",
                    self.guild_id, scanned, enforced, skip_reasons,
                )
            except Exception as e:
                logger.error(
                    "Error in scheduled enforcement for guild %s: %s",
                    self.guild_id, e,
                )

    def is_channel_ignored(self, channel_id: int) -> bool:
        """Check if a channel should be ignored."""
        return channel_id in self._ignored_channels or channel_id in self._excluded_channels

    def is_exempt(self, member: discord.Member) -> bool:
        """Check if a member is exempt from enforcement."""
        perms = member.guild_permissions
        
        # Staff permissions exempt
        if perms.administrator or perms.manage_guild or perms.manage_roles or perms.manage_messages:
            return True
        
        # Exempt roles
        if any(role.id in self._exempt_roles for role in member.roles):
            return True
        
        # Individual exemptions
        if member.id in self._exemptions:
            return True
        
        return False

    def record_action(self, label: str) -> None:
        """Record an action for stats/logging."""
        self.action_count += 1
        if self.action_count % 100 == 0:
            logger.info(
                "Guild %s actions=%s last_action=%s",
                self.guild_id,
                self.action_count,
                label,
            )

    def has_hash(self, hash_value: str) -> bool:
        """Check if a hash matches known bad hashes."""
        return hash_value in self.hashes

    def is_scanner_running(self) -> bool:
        """Check if the sus scanner queue processor is running."""
        return (
            self.queue_processor.reader_task is not None and
            not self.queue_processor.stop_event.is_set()
        )

    def on_scanner_started(self) -> None:
        """Hook for scanner enable to start queue state persistence."""
        self._ensure_queue_state_task()

    def on_scanner_stopped(self) -> None:
        """Hook for scanner disable to stop queue state persistence."""
        if self.queue_state_task:
            self.queue_state_task.cancel()

    async def enqueue_job(self, job: dict[str, Any]) -> bool:
        """
        Enqueue a scan job.
        
        Jobs are only enqueued if the scanner is running.
        """
        # Don't enqueue if scanner isn't running
        if not self.is_scanner_running():
            return False
        
        enqueued = await self.queue_processor.enqueue(job)
        if not enqueued:
            await self.storage.increment_queue_dropped()
        return enqueued
