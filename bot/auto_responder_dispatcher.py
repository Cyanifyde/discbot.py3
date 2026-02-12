"""
Bounded dispatcher for auto-responder processing.

Avoids unbounded asyncio.create_task() fan-out under high message throughput by
processing messages using a fixed worker pool + bounded queue.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from core.error_handler import handle_event_error

if TYPE_CHECKING:
    import discord
    from bot.client import DiscBot

logger = logging.getLogger("discbot.auto_responder_dispatcher")

AutoResponderHandler = Callable[["discord.Message"], Awaitable[bool]]


class AutoResponderDispatcher:
    __slots__ = (
        "_queue",
        "_worker_count",
        "_workers",
        "_bot",
        "_dropped",
        "_processed",
        "_stop",
    )

    def __init__(self, *, queue_max: int, worker_count: int) -> None:
        self._queue: asyncio.Queue["discord.Message"] = asyncio.Queue(maxsize=max(1, int(queue_max)))
        self._worker_count = max(1, int(worker_count))
        self._workers: list[asyncio.Task] = []
        self._bot: Optional["DiscBot"] = None
        self._dropped = 0
        self._processed = 0
        self._stop = asyncio.Event()

    async def start(self, bot: "DiscBot") -> None:
        if self._workers:
            return
        self._bot = bot
        self._stop.clear()
        self._workers = [
            asyncio.create_task(self._worker_loop(i), name=f"autoresponder-worker-{i}")
            for i in range(self._worker_count)
        ]

    async def stop(self) -> None:
        self._stop.set()
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        self._bot = None

    def submit(self, message: "discord.Message") -> bool:
        """
        Submit a message to the dispatcher.

        Returns False when the queue is full (message dropped).
        """
        if self._stop.is_set():
            return False
        try:
            self._queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            # Log occasionally to avoid spam under sustained overload.
            if self._dropped in (1, 10, 100) or self._dropped % 1000 == 0:
                logger.warning(
                    "Auto-responder queue full: dropped=%d processed=%d qsize=%d",
                    self._dropped,
                    self._processed,
                    self._queue.qsize(),
                )
            return False

    async def _worker_loop(self, worker_id: int) -> None:
        while not self._stop.is_set():
            try:
                msg = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                handler: Optional[AutoResponderHandler] = None
                if self._bot is not None:
                    try:
                        handler = self._bot.get_auto_responder_handler()
                    except Exception:
                        handler = None
                if handler is None:
                    logger.warning("Auto-responder handler unavailable; dropping message")
                    continue

                await handler(msg)
            except Exception as e:
                try:
                    mid = getattr(msg, "id", None)
                except Exception:
                    mid = None
                logger.error("Auto-responder worker %d failed for message %s: %s", worker_id, mid, e)
                await handle_event_error(
                    e, context="auto_responder_worker",
                    guild_id=getattr(msg, "guild", None) and msg.guild.id,
                    channel_id=getattr(msg, "channel", None) and msg.channel.id,
                )
            finally:
                self._processed += 1
                try:
                    self._queue.task_done()
                except Exception:
                    pass
