"""
Communication storage - persistent storage for feedback, announcements, and acknowledgments.

Provides storage for feedback submissions, commission announcements, and message acknowledgments.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso

# Storage directory
COMMUNICATION_DIR = BASE_DIR / "data" / "communication"


class CommunicationStore:
    """Storage for communication features."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = COMMUNICATION_DIR / str(guild_id)
        self.feedback_path = self.root / "feedback.json"
        self.announcements_path = self.root / "announcements.json"
        self.acknowledgments_path = self.root / "acknowledgments.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Feedback Box ─────────────────────────────────────────────────────────

    async def _read_feedback(self) -> Dict[str, Any]:
        """Read feedback file."""
        default = {"submissions": [], "config": {"enabled": True, "channel_id": None}}
        data = await read_json(self.feedback_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_feedback(self, data: Dict[str, Any]) -> None:
        """Write feedback file."""
        await write_json_atomic(self.feedback_path, data)

    async def add_feedback(
        self,
        feedback_id: str,
        content: str,
        anonymous: bool = True,
        author_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Add a feedback submission."""
        async with self._lock:
            data = await self._read_feedback()

            submission = {
                "id": feedback_id,
                "content": content,
                "anonymous": anonymous,
                "author_id": author_id if not anonymous else None,
                "created_at": dt_to_iso(utcnow()),
                "status": "pending",  # pending, reviewed, implemented, dismissed
                "upvotes": 0,
                "notes": [],
            }

            data["submissions"].append(submission)
            await self._write_feedback(data)
            return submission

    async def get_feedback(self, feedback_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific feedback submission."""
        async with self._lock:
            data = await self._read_feedback()
            for submission in data["submissions"]:
                if submission["id"].startswith(feedback_id):
                    return submission
            return None

    async def get_all_feedback(
        self,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all feedback submissions, optionally filtered by status."""
        async with self._lock:
            data = await self._read_feedback()
            submissions = data["submissions"]

            if status:
                submissions = [s for s in submissions if s["status"] == status]

            return submissions

    async def update_feedback_status(
        self,
        feedback_id: str,
        status: str,
        note: Optional[str] = None,
    ) -> bool:
        """Update feedback status."""
        async with self._lock:
            data = await self._read_feedback()

            for submission in data["submissions"]:
                if submission["id"].startswith(feedback_id):
                    submission["status"] = status
                    if note:
                        submission["notes"].append({
                            "note": note,
                            "added_at": dt_to_iso(utcnow()),
                        })
                    await self._write_feedback(data)
                    return True

            return False

    async def upvote_feedback(self, feedback_id: str) -> bool:
        """Increment upvote count for feedback."""
        async with self._lock:
            data = await self._read_feedback()

            for submission in data["submissions"]:
                if submission["id"].startswith(feedback_id):
                    submission["upvotes"] = submission.get("upvotes", 0) + 1
                    await self._write_feedback(data)
                    return True

            return False

    async def get_feedback_config(self) -> Dict[str, Any]:
        """Get feedback configuration."""
        async with self._lock:
            data = await self._read_feedback()
            return data.get("config", {"enabled": True, "channel_id": None})

    async def update_feedback_config(self, updates: Dict[str, Any]) -> None:
        """Update feedback configuration."""
        async with self._lock:
            data = await self._read_feedback()
            data["config"].update(updates)
            await self._write_feedback(data)

    # ─── Commission Announcements ─────────────────────────────────────────────

    async def _read_announcements(self) -> Dict[str, Any]:
        """Read announcements file."""
        default = {"subscribers": {}, "config": {"channel_id": None}}
        data = await read_json(self.announcements_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_announcements(self, data: Dict[str, Any]) -> None:
        """Write announcements file."""
        await write_json_atomic(self.announcements_path, data)

    async def subscribe_to_artist(
        self,
        user_id: int,
        artist_id: int,
    ) -> bool:
        """Subscribe a user to an artist's commission announcements."""
        async with self._lock:
            data = await self._read_announcements()

            user_key = str(user_id)
            if user_key not in data["subscribers"]:
                data["subscribers"][user_key] = []

            if artist_id not in data["subscribers"][user_key]:
                data["subscribers"][user_key].append(artist_id)
                await self._write_announcements(data)
                return True

            return False

    async def unsubscribe_from_artist(
        self,
        user_id: int,
        artist_id: int,
    ) -> bool:
        """Unsubscribe a user from an artist's announcements."""
        async with self._lock:
            data = await self._read_announcements()

            user_key = str(user_id)
            if user_key not in data["subscribers"]:
                return False

            if artist_id in data["subscribers"][user_key]:
                data["subscribers"][user_key].remove(artist_id)
                await self._write_announcements(data)
                return True

            return False

    async def get_subscribers(self, artist_id: int) -> List[int]:
        """Get all subscribers for an artist."""
        async with self._lock:
            data = await self._read_announcements()

            subscribers = []
            for user_id_str, artist_list in data["subscribers"].items():
                if artist_id in artist_list:
                    subscribers.append(int(user_id_str))

            return subscribers

    async def get_user_subscriptions(self, user_id: int) -> List[int]:
        """Get all artists a user is subscribed to."""
        async with self._lock:
            data = await self._read_announcements()
            user_key = str(user_id)
            return data["subscribers"].get(user_key, [])

    async def set_announcement_channel(self, channel_id: int) -> None:
        """Set the announcement channel."""
        async with self._lock:
            data = await self._read_announcements()
            data["config"]["channel_id"] = channel_id
            await self._write_announcements(data)

    async def get_announcement_channel(self) -> Optional[int]:
        """Get the announcement channel ID."""
        async with self._lock:
            data = await self._read_announcements()
            return data["config"].get("channel_id")

    # ─── Message Acknowledgments ──────────────────────────────────────────────

    async def _read_acknowledgments(self) -> Dict[str, Any]:
        """Read acknowledgments file."""
        default = {"messages": {}}
        data = await read_json(self.acknowledgments_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_acknowledgments(self, data: Dict[str, Any]) -> None:
        """Write acknowledgments file."""
        await write_json_atomic(self.acknowledgments_path, data)

    async def create_acknowledgment(
        self,
        message_id: int,
        title: str,
        content: str,
        required_role_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create an acknowledgment requirement for a message."""
        async with self._lock:
            data = await self._read_acknowledgments()

            ack = {
                "message_id": message_id,
                "title": title,
                "content": content,
                "required_role_id": required_role_id,
                "created_at": dt_to_iso(utcnow()),
                "acknowledged_by": [],
            }

            data["messages"][str(message_id)] = ack
            await self._write_acknowledgments(data)
            return ack

    async def acknowledge_message(
        self,
        message_id: int,
        user_id: int,
    ) -> bool:
        """Record that a user acknowledged a message."""
        async with self._lock:
            data = await self._read_acknowledgments()

            msg_key = str(message_id)
            if msg_key not in data["messages"]:
                return False

            ack_list = data["messages"][msg_key]["acknowledged_by"]
            if user_id not in ack_list:
                ack_list.append(user_id)
                await self._write_acknowledgments(data)

            return True

    async def get_acknowledgment(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get acknowledgment details."""
        async with self._lock:
            data = await self._read_acknowledgments()
            return data["messages"].get(str(message_id))

    async def has_acknowledged(self, message_id: int, user_id: int) -> bool:
        """Check if a user has acknowledged a message."""
        async with self._lock:
            data = await self._read_acknowledgments()

            msg_key = str(message_id)
            if msg_key not in data["messages"]:
                return False

            return user_id in data["messages"][msg_key]["acknowledged_by"]

    async def get_pending_acknowledgments(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all acknowledgments pending for a user."""
        async with self._lock:
            data = await self._read_acknowledgments()

            pending = []
            for message_id_str, ack in data["messages"].items():
                if user_id not in ack["acknowledged_by"]:
                    pending.append(ack)

            return pending
