"""
Communication service - business logic for feedback, announcements, and acknowledgments.

Handles feedback management, commission announcements, and message acknowledgment tracking.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.communication_storage import CommunicationStore

if TYPE_CHECKING:
    import discord


class CommunicationService:
    """Business logic for communication features."""

    def __init__(self) -> None:
        self._stores: Dict[int, CommunicationStore] = {}

    def _get_store(self, guild_id: int) -> CommunicationStore:
        """Get or create a communication store for a guild."""
        if guild_id not in self._stores:
            self._stores[guild_id] = CommunicationStore(guild_id)
        return self._stores[guild_id]

    async def initialize_store(self, guild_id: int) -> None:
        """Initialize storage for a guild."""
        store = self._get_store(guild_id)
        await store.initialize()

    # ─── Feedback Box ─────────────────────────────────────────────────────────

    async def submit_feedback(
        self,
        guild_id: int,
        content: str,
        anonymous: bool = True,
        author_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Submit feedback.

        Args:
            guild_id: Guild ID
            content: Feedback content
            anonymous: Whether submission is anonymous
            author_id: Author ID (if not anonymous)

        Returns:
            Created feedback submission
        """
        store = self._get_store(guild_id)
        await store.initialize()

        feedback_id = str(uuid.uuid4())
        return await store.add_feedback(feedback_id, content, anonymous, author_id)

    async def get_feedback(
        self,
        guild_id: int,
        feedback_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific feedback submission."""
        store = self._get_store(guild_id)
        return await store.get_feedback(feedback_id)

    async def get_all_feedback(
        self,
        guild_id: int,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all feedback submissions."""
        store = self._get_store(guild_id)
        return await store.get_all_feedback(status)

    async def update_feedback_status(
        self,
        guild_id: int,
        feedback_id: str,
        status: str,
        note: Optional[str] = None,
    ) -> bool:
        """Update feedback status."""
        store = self._get_store(guild_id)
        return await store.update_feedback_status(feedback_id, status, note)

    async def upvote_feedback(
        self,
        guild_id: int,
        feedback_id: str,
    ) -> bool:
        """Upvote a feedback submission."""
        store = self._get_store(guild_id)
        return await store.upvote_feedback(feedback_id)

    async def configure_feedback(
        self,
        guild_id: int,
        enabled: Optional[bool] = None,
        channel_id: Optional[int] = None,
    ) -> None:
        """Configure feedback settings."""
        store = self._get_store(guild_id)
        await store.initialize()

        updates = {}
        if enabled is not None:
            updates["enabled"] = enabled
        if channel_id is not None:
            updates["channel_id"] = channel_id

        await store.update_feedback_config(updates)

    async def get_feedback_config(self, guild_id: int) -> Dict[str, Any]:
        """Get feedback configuration."""
        store = self._get_store(guild_id)
        return await store.get_feedback_config()

    # ─── Commission Announcements ─────────────────────────────────────────────

    async def subscribe_to_artist(
        self,
        guild_id: int,
        user_id: int,
        artist_id: int,
    ) -> bool:
        """Subscribe a user to an artist's announcements."""
        store = self._get_store(guild_id)
        await store.initialize()
        return await store.subscribe_to_artist(user_id, artist_id)

    async def unsubscribe_from_artist(
        self,
        guild_id: int,
        user_id: int,
        artist_id: int,
    ) -> bool:
        """Unsubscribe a user from an artist's announcements."""
        store = self._get_store(guild_id)
        return await store.unsubscribe_from_artist(user_id, artist_id)

    async def get_subscribers(
        self,
        guild_id: int,
        artist_id: int,
    ) -> List[int]:
        """Get all subscribers for an artist."""
        store = self._get_store(guild_id)
        return await store.get_subscribers(artist_id)

    async def get_user_subscriptions(
        self,
        guild_id: int,
        user_id: int,
    ) -> List[int]:
        """Get all artists a user is subscribed to."""
        store = self._get_store(guild_id)
        return await store.get_user_subscriptions(user_id)

    async def announce_slots_open(
        self,
        guild_id: int,
        artist_id: int,
        bot: discord.Client,
    ) -> int:
        """
        Announce that an artist's commission slots are open.

        Returns:
            Number of subscribers notified
        """
        store = self._get_store(guild_id)
        subscribers = await store.get_subscribers(artist_id)

        if not subscribers:
            return 0

        # Get announcement channel
        channel_id = await store.get_announcement_channel()
        if not channel_id:
            return 0

        try:
            channel = bot.get_channel(channel_id)
            if not channel:
                return 0

            # Send announcement
            mentions = " ".join(f"<@{uid}>" for uid in subscribers)
            await channel.send(
                f"{mentions}\n"
                f"<@{artist_id}>'s commission slots are now open!"
            )

            return len(subscribers)
        except Exception:
            return 0

    async def set_announcement_channel(
        self,
        guild_id: int,
        channel_id: int,
    ) -> None:
        """Set the announcement channel."""
        store = self._get_store(guild_id)
        await store.initialize()
        await store.set_announcement_channel(channel_id)

    # ─── Message Acknowledgments ──────────────────────────────────────────────

    async def create_acknowledgment(
        self,
        guild_id: int,
        message_id: int,
        channel_id: Optional[int],
        title: str,
        content: str,
        required_role_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create an acknowledgment requirement."""
        store = self._get_store(guild_id)
        await store.initialize()
        return await store.create_acknowledgment(
            message_id,
            title,
            content,
            channel_id,
            required_role_id,
        )

    async def acknowledge_message(
        self,
        guild_id: int,
        message_id: int,
        user_id: int,
    ) -> bool:
        """Record message acknowledgment."""
        store = self._get_store(guild_id)
        return await store.acknowledge_message(message_id, user_id)

    async def get_acknowledgment(
        self,
        guild_id: int,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Get acknowledgment details."""
        store = self._get_store(guild_id)
        return await store.get_acknowledgment(message_id)

    async def has_acknowledged(
        self,
        guild_id: int,
        message_id: int,
        user_id: int,
    ) -> bool:
        """Check if user has acknowledged a message."""
        store = self._get_store(guild_id)
        return await store.has_acknowledged(message_id, user_id)

    async def get_pending_acknowledgments(
        self,
        guild_id: int,
        user_id: int,
    ) -> List[Dict[str, Any]]:
        """Get pending acknowledgments for a user."""
        store = self._get_store(guild_id)
        return await store.get_pending_acknowledgments(user_id)

    async def get_acknowledgment_stats(
        self,
        guild_id: int,
        message_id: int,
    ) -> Dict[str, Any]:
        """Get acknowledgment statistics."""
        ack = await self.get_acknowledgment(guild_id, message_id)
        if not ack:
            return {}

        total = len(ack["acknowledged_by"])
        return {
            "total_acknowledged": total,
            "acknowledged_by": ack["acknowledged_by"],
            "created_at": ack["created_at"],
        }


# Global service instance
communication_service = CommunicationService()
