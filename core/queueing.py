"""
Job queue system for image hash scanning.

Provides persistent queue storage and worker-based processing.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import discord

from .io_utils import (
    append_text,
    read_json,
    read_queue_lines,
    rewrite_queue_file,
    write_json_atomic,
)
from .storage import SuspicionStore
from .utils import build_cdn_regex, hash_bytes, magic_bytes_valid, safe_int

if TYPE_CHECKING:
    from bot.client import DiscBot


class QueueStore:
    def __init__(self, root: Path) -> None:
        self.queue_path = root / "queue.jsonl"
        self.state_path = root / "queue.state.json"
        self.state_lock = asyncio.Lock()
        self.state: Dict[str, Any] = {}

    async def initialize(self) -> None:
        data = await read_json(self.state_path, default=None)
        if data is None:
            data = {"read_offset_bytes": 0, "queued_jobs": 0, "compactions": 0}
            await write_json_atomic(self.state_path, data)
        self.state = data
        if "queued_jobs" not in self.state:
            await self.rebuild_queue_length()

    async def rebuild_queue_length(self) -> None:
        read_offset = int(self.state.get("read_offset_bytes", 0))

        def _count_from_offset() -> int:
            if not self.queue_path.exists():
                return 0
            count = 0
            with self.queue_path.open("rb") as handle:
                handle.seek(read_offset)
                for _ in handle:
                    count += 1
            return count

        remaining = await asyncio.to_thread(_count_from_offset)
        async with self.state_lock:
            self.state["queued_jobs"] = max(0, remaining)
            await write_json_atomic(self.state_path, self.state)

    async def enqueue(self, job: Dict[str, Any], max_jobs: int) -> bool:
        async with self.state_lock:
            queued = int(self.state.get("queued_jobs", 0))
            if queued >= max_jobs:
                return False
            self.state["queued_jobs"] = queued + 1
            await write_json_atomic(self.state_path, self.state)
        await append_text(self.queue_path, json.dumps(job, ensure_ascii=True) + "\n")
        return True

    async def update_state(self, read_offset: int, queued_jobs: int) -> None:
        async with self.state_lock:
            self.state["read_offset_bytes"] = read_offset
            self.state["queued_jobs"] = max(0, queued_jobs)
            await write_json_atomic(self.state_path, self.state)

    async def increment_compactions(self) -> None:
        async with self.state_lock:
            self.state["compactions"] = int(self.state.get("compactions", 0)) + 1
            await write_json_atomic(self.state_path, self.state)


class QueueProcessor:
    def __init__(
        self,
        bot: "DiscBot",
        store: QueueStore,
        storage: SuspicionStore,
        config: Dict[str, Any],
    ) -> None:
        self.bot = bot
        self.store = store
        self.storage = storage
        self.config = config
        self.guild_id = int(config.get("guild_id", 0))
        self.max_image_bytes = int(config.get("max_image_bytes", 0))
        self.worker_timeout = float(config.get("worker_job_timeout_seconds", 15))
        self.worker_count = int(config.get("worker_count", 1))
        self.queue: asyncio.Queue[Tuple[Dict[str, Any], int]] = asyncio.Queue(
            maxsize=max(1, int(config.get("queue_max_jobs", 1000)))
        )
        self.pending_order: Deque[int] = deque()
        self.pending_done: Dict[int, bool] = {}
        self.read_offset_bytes = int(store.state.get("read_offset_bytes", 0))
        self.next_read_offset = self.read_offset_bytes
        self.queued_jobs = int(store.state.get("queued_jobs", 0))
        self.compact_threshold = int(config.get("queue_compact_threshold_bytes", 0))
        self.stop_event = asyncio.Event()
        self.stop_event.set()  # Initially stopped - must be explicitly started
        self.reader_task: Optional[asyncio.Task] = None
        self.worker_tasks: List[asyncio.Task] = []
        self.session: Optional[aiohttp.ClientSession] = None
        self.cdn_regex = build_cdn_regex(config.get("allowed_discord_cdn_domains", []))
        self.allowed_cdn_domains = {d.lower() for d in config.get("allowed_discord_cdn_domains", [])}

    def update_config(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.guild_id = int(config.get("guild_id", 0))
        self.max_image_bytes = int(config.get("max_image_bytes", 0))
        self.worker_timeout = float(config.get("worker_job_timeout_seconds", 15))
        self.worker_count = int(config.get("worker_count", 1))
        self.compact_threshold = int(config.get("queue_compact_threshold_bytes", 0))
        self.cdn_regex = build_cdn_regex(config.get("allowed_discord_cdn_domains", []))
        self.allowed_cdn_domains = {d.lower() for d in config.get("allowed_discord_cdn_domains", [])}

    async def start(self) -> None:
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.worker_timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)
        self.stop_event.clear()
        self.reader_task = asyncio.create_task(self._reader_loop())
        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(self.worker_count)
        ]

    async def stop(self) -> None:
        self.stop_event.set()
        if self.reader_task:
            self.reader_task.cancel()
        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*(t for t in [self.reader_task, *self.worker_tasks] if t), return_exceptions=True)
        if self.session:
            await self.session.close()
            self.session = None

    async def enqueue(self, job: Dict[str, Any]) -> bool:
        ok = await self.store.enqueue(job, int(self.config.get("queue_max_jobs", 1000)))
        if ok:
            self.queued_jobs += 1
        return ok

    async def _reader_loop(self) -> None:
        while not self.stop_event.is_set():
            if self.queue.full():
                await asyncio.sleep(0.2)
                continue
            lines = await read_queue_lines(self.store.queue_path, self.next_read_offset)
            if not lines:
                await asyncio.sleep(0.5)
                continue
            for line, end_offset in lines:
                if not line.strip():
                    self.next_read_offset = end_offset
                    continue
                try:
                    job = json.loads(line)
                    if not isinstance(job, dict):
                        raise ValueError("job not dict")
                except Exception:
                    self.next_read_offset = end_offset
                    self.pending_order.append(end_offset)
                    self.pending_done[end_offset] = True
                    await self._ack_processed(end_offset)
                    continue
                await self.queue.put((job, end_offset))
                self.pending_order.append(end_offset)
                self.pending_done[end_offset] = False
                self.next_read_offset = end_offset

    async def _worker_loop(self, worker_id: int) -> None:
        import logging
        logger = logging.getLogger("discbot.queue")
        while not self.stop_event.is_set():
            try:
                job, end_offset = await self.queue.get()
            except asyncio.CancelledError:
                break
            try:
                await asyncio.wait_for(self._process_job(job), timeout=self.worker_timeout)
            except asyncio.TimeoutError:
                logger.warning("Worker %d: Job timed out after %ds", worker_id, self.worker_timeout)
            except Exception as e:
                logger.error("Worker %d: Job processing failed: %s", worker_id, e)
            await self._ack_processed(end_offset)
            self.queue.task_done()

    async def _ack_processed(self, end_offset: int) -> None:
        if end_offset not in self.pending_done:
            return
        self.pending_done[end_offset] = True
        progressed = False
        while self.pending_order and self.pending_done.get(self.pending_order[0]):
            done_offset = self.pending_order.popleft()
            self.pending_done.pop(done_offset, None)
            self.read_offset_bytes = done_offset
            self.queued_jobs = max(0, self.queued_jobs - 1)
            progressed = True
        if progressed:
            await self.store.update_state(self.read_offset_bytes, self.queued_jobs)
            await self._maybe_compact()

    async def _maybe_compact(self) -> None:
        if self.compact_threshold <= 0:
            return
        if self.read_offset_bytes < self.compact_threshold:
            return
        if self.pending_order:
            return
        await rewrite_queue_file(self.store.queue_path, self.read_offset_bytes)
        self.read_offset_bytes = 0
        self.next_read_offset = 0
        await self.store.increment_compactions()
        await self.store.update_state(self.read_offset_bytes, self.queued_jobs)

    async def _process_job(self, job: Dict[str, Any]) -> None:
        guild_id = safe_int(job.get("guild_id"), default=0)
        if not guild_id or guild_id != self.guild_id:
            return
        channel_id = safe_int(job.get("channel_id"), default=0)
        message_id = safe_int(job.get("message_id"), default=0)
        if not channel_id or not message_id:
            return
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except Exception:
            return
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)):
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if self.bot.is_channel_ignored(self.guild_id, message.channel.id):
            return
        if not isinstance(message.author, discord.Member):
            return
        if self.bot.is_exempt(self.guild_id, message.author):
            return
        source = job.get("source")
        matched_hash: Optional[str] = None
        if source == "attachment":
            matched_hash = await self._hash_from_attachment_job(message, job)
        elif source == "discord_cdn_url":
            matched_hash = await self._hash_from_url_job(job)
        elif source == "discord_message_link":
            matched_hash = await self._hash_from_link_job(job)
        if matched_hash and self.bot.has_hash(self.guild_id, matched_hash):
            await self.bot.enforce_hash_match(self.guild_id, message, matched_hash)

    async def _hash_from_attachment_job(self, message: discord.Message, job: Dict[str, Any]) -> Optional[str]:
        max_bytes = self.max_image_bytes
        attachments = message.attachments
        if not attachments:
            return None
        
        # Try to match the specific attachment from the job
        attachment_info = job.get("attachment")
        target_attachment = None
        
        if isinstance(attachment_info, dict):
            job_url = attachment_info.get("url")
            # Find matching attachment by URL
            for att in attachments:
                if att.url == job_url:
                    target_attachment = att
                    break
        
        # Fallback to first attachment if no match
        if not target_attachment:
            target_attachment = attachments[0]
        
        if target_attachment.size and target_attachment.size > max_bytes:
            return None
        try:
            data = await target_attachment.read()
        except Exception:
            data = b""
        if not data:
            # Fallback to downloading from URL
            if isinstance(attachment_info, dict):
                url = attachment_info.get("url")
                if url:
                    data = await self._download_url(url)
        if not data:
            return None
        if len(data) > max_bytes:
            return None
        if not magic_bytes_valid(data):
            return None
        return hash_bytes(data)

    async def _hash_from_url_job(self, job: Dict[str, Any]) -> Optional[str]:
        url = job.get("url")
        if not isinstance(url, str):
            return None
        data = await self._download_url(url)
        if not data:
            return None
        if not magic_bytes_valid(data):
            return None
        return hash_bytes(data)

    async def _hash_from_link_job(self, job: Dict[str, Any]) -> Optional[str]:
        linked = job.get("linked")
        if not isinstance(linked, dict):
            return None
        guild_id = safe_int(linked.get("guild_id"), default=0)
        channel_id = safe_int(linked.get("channel_id"), default=0)
        message_id = safe_int(linked.get("message_id"), default=0)
        if not guild_id or not channel_id or not message_id:
            return None
        if guild_id != self.guild_id:
            return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except Exception:
            return None
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)):
            return None
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        if not message.attachments:
            return None
        attachment = message.attachments[0]
        max_bytes = self.max_image_bytes
        if attachment.size and attachment.size > max_bytes:
            return None
        try:
            data = await attachment.read()
        except Exception:
            return None
        if len(data) > max_bytes:
            return None
        if not magic_bytes_valid(data):
            return None
        return hash_bytes(data)

    async def _download_url(self, url: str) -> Optional[bytes]:
        if not self.session:
            return None
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            return None
        host = (parsed.hostname or "").lower()
        if host not in self.allowed_cdn_domains:
            return None
        max_bytes = self.max_image_bytes
        current_url = url
        for _ in range(3):
            try:
                async with self.session.get(current_url, allow_redirects=False) as resp:
                    if 300 <= resp.status < 400:
                        location = resp.headers.get("Location")
                        if not location:
                            return None
                        next_url = location
                        parsed_next = urlparse(next_url)
                        if parsed_next.scheme.lower() != "https":
                            return None
                        if (parsed_next.hostname or "").lower() not in self.allowed_cdn_domains:
                            return None
                        current_url = next_url
                        continue
                    if resp.status != 200:
                        return None
                    content_length = resp.headers.get("Content-Length")
                    if content_length and content_length.isdigit():
                        if int(content_length) > max_bytes:
                            return None
                    data = bytearray()
                    async for chunk in resp.content.iter_chunked(8192):
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            return None
                    return bytes(data)
            except Exception:
                return None
        return None
