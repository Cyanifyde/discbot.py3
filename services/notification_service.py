"""
Notification service - handles DM queueing, quiet hours, and digest mode.

Respects user quiet hours and notification preferences from profile system.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.io_utils import read_json, write_json_atomic
from core.paths import BASE_DIR
from core.utils import utcnow, dt_to_iso, iso_to_dt

logger = logging.getLogger("discbot.notifications")

# Storage directory
NOTIFICATIONS_DIR = BASE_DIR / "data" / "notifications"


class NotificationService:
    """Service for managing user notifications with quiet hours and digest support."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._lock = asyncio.Lock()

    async def queue_dm(
        self,
        user_id: int,
        content: str | discord.Embed,
        priority: str = "normal",  # low/normal/high
        category: str = "general",  # commission_updates/waitlist_notifications/vouch_received/general
    ) -> bool:
        """
        Queue a DM to be sent to a user.

        Returns True if queued/sent, False if user has disabled this category.
        """
        try:
            # Load user profile to check preferences
            from classes.profile import _load_record
            profile = await _load_record(user_id)

            if profile:
                # Check notification preferences
                notif_prefs = profile.get("notification_preferences", {})
                if category in notif_prefs and not notif_prefs.get(category, True):
                    logger.debug(f"User {user_id} has disabled {category} notifications")
                    return False

                # Check digest mode
                if notif_prefs.get("digest_mode", False) and priority != "high":
                    # Add to digest queue instead
                    await self._add_to_digest(user_id, content, category)
                    return True

                # Check quiet hours (unless high priority)
                if priority != "high" and await self.is_quiet_hours(user_id, profile):
                    await self._add_to_queue(user_id, content, priority, category)
                    return True

            # Send immediately
            user = await self.bot.fetch_user(user_id)
            if user:
                if isinstance(content, str):
                    await user.send(content)
                else:
                    await user.send(embed=content)
                return True

        except discord.Forbidden:
            logger.warning(f"Cannot DM user {user_id} - DMs closed")
        except Exception as e:
            logger.error(f"Error queueing DM for user {user_id}: {e}", exc_info=True)

        return False

    async def is_quiet_hours(self, user_id: int, profile: Optional[Dict] = None) -> bool:
        """Check if user is currently in quiet hours."""
        if profile is None:
            from classes.profile import _load_record
            profile = await _load_record(user_id)

        if not profile:
            return False

        quiet_hours = profile.get("quiet_hours", {})
        if not quiet_hours.get("enabled", False):
            return False

        start_str = quiet_hours.get("start", "22:00")
        end_str = quiet_hours.get("end", "08:00")
        tz = quiet_hours.get("timezone") or profile.get("timezone")

        # Parse times
        try:
            start_hour, start_min = map(int, start_str.split(":"))
            end_hour, end_min = map(int, end_str.split(":"))
        except Exception:
            return False

        # Get current time (use user's timezone if available, otherwise UTC)
        now = utcnow()
        if tz:
            try:
                now = now.astimezone(ZoneInfo(str(tz)))
            except ZoneInfoNotFoundError:
                logger.debug("Unknown timezone '%s' for user %s", tz, user_id)
            except Exception as exc:
                logger.debug("Failed timezone conversion for user %s: %s", user_id, exc)
        current_time = now.time()

        start_time = time(start_hour, start_min)
        end_time = time(end_hour, end_min)

        # Handle overnight quiet hours (e.g., 22:00 - 08:00)
        if start_time > end_time:
            return current_time >= start_time or current_time <= end_time
        else:
            return start_time <= current_time <= end_time

    async def _add_to_queue(
        self,
        user_id: int,
        content: str | discord.Embed,
        priority: str,
        category: str,
    ) -> None:
        """Add notification to queue (for quiet hours)."""
        async with self._lock:
            await asyncio.to_thread(NOTIFICATIONS_DIR.mkdir, parents=True, exist_ok=True)

            queue_path = NOTIFICATIONS_DIR / f"{user_id}.json"
            data = await read_json(queue_path, default={"queue": [], "digest": []})

            # Convert embed to dict if needed
            if isinstance(content, discord.Embed):
                content_data = content.to_dict()
                content_type = "embed"
            else:
                content_data = content
                content_type = "text"

            notification = {
                "content": content_data,
                "content_type": content_type,
                "priority": priority,
                "category": category,
                "queued_at": dt_to_iso(utcnow()),
            }

            data["queue"].append(notification)
            await write_json_atomic(queue_path, data)

    async def _add_to_digest(
        self,
        user_id: int,
        content: str | discord.Embed,
        category: str,
    ) -> None:
        """Add notification to daily digest."""
        async with self._lock:
            await asyncio.to_thread(NOTIFICATIONS_DIR.mkdir, parents=True, exist_ok=True)

            queue_path = NOTIFICATIONS_DIR / f"{user_id}.json"
            data = await read_json(queue_path, default={"queue": [], "digest": []})

            # Convert embed to dict if needed
            if isinstance(content, discord.Embed):
                content_data = content.to_dict()
                content_type = "embed"
            else:
                content_data = content
                content_type = "text"

            notification = {
                "content": content_data,
                "content_type": content_type,
                "category": category,
                "added_at": dt_to_iso(utcnow()),
            }

            data["digest"].append(notification)
            await write_json_atomic(queue_path, data)

    async def check_and_send(self) -> int:
        """
        Check all queued notifications and send if user is not in quiet hours.
        Should be called periodically (e.g., every 5-10 minutes).

        Returns number of notifications sent.
        """
        sent_count = 0

        if not NOTIFICATIONS_DIR.exists():
            return 0

        for queue_file in NOTIFICATIONS_DIR.glob("*.json"):
            try:
                user_id = int(queue_file.stem)
            except ValueError:
                continue

            # Check if user is still in quiet hours
            if await self.is_quiet_hours(user_id):
                continue

            # Send queued notifications
            async with self._lock:
                data = await read_json(queue_file, default={"queue": [], "digest": []})
                queue = data.get("queue", [])

                if not queue:
                    continue

                remaining = []
                for notification in queue:
                    try:
                        user = await self.bot.fetch_user(user_id)
                        if user:
                            content = notification["content"]
                            content_type = notification["content_type"]

                            if content_type == "embed":
                                embed = discord.Embed.from_dict(content)
                                await user.send(embed=embed)
                            else:
                                await user.send(content)

                            sent_count += 1
                        else:
                            remaining.append(notification)
                    except discord.Forbidden:
                        logger.warning(f"Cannot DM user {user_id} - DMs closed")
                        # Don't keep trying to send to this user
                    except Exception as e:
                        logger.error(f"Error sending queued notification to {user_id}: {e}")
                        remaining.append(notification)

                # Update queue
                data["queue"] = remaining
                await write_json_atomic(queue_file, data)

        if sent_count > 0:
            logger.info(f"Sent {sent_count} queued notifications")

        return sent_count

    async def build_digest(self, user_id: int) -> Optional[discord.Embed]:
        """Build digest embed for a user."""
        queue_path = NOTIFICATIONS_DIR / f"{user_id}.json"
        if not queue_path.exists():
            return None

        data = await read_json(queue_path, default={"queue": [], "digest": []})
        digest_items = data.get("digest", [])

        if not digest_items:
            return None

        # Group by category
        by_category: Dict[str, List[Any]] = {}
        for item in digest_items:
            category = item.get("category", "general")
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(item)

        # Build embed
        embed = discord.Embed(
            title=" Daily Notification Digest",
            description=f"You have {len(digest_items)} notification(s) from the past 24 hours.",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        for category, items in by_category.items():
            category_name = category.replace("_", " ").title()

            # Summarize items
            summaries = []
            for item in items[:5]:  # Show max 5 per category
                content = item["content"]
                if isinstance(content, dict):
                    # Embed - use title or description
                    summary = content.get("title") or content.get("description", "Notification")[:50]
                else:
                    summary = content[:50]
                summaries.append(f"• {summary}")

            if len(items) > 5:
                summaries.append(f"• ...and {len(items) - 5} more")

            embed.add_field(
                name=f"{category_name} ({len(items)})",
                value="\n".join(summaries) or "_No details_",
                inline=False,
            )

        return embed

    async def send_digest(self, user_id: int) -> bool:
        """Send daily digest to a user and clear digest queue."""
        embed = await self.build_digest(user_id)

        if not embed:
            return False

        try:
            user = await self.bot.fetch_user(user_id)
            if user:
                await user.send(embed=embed)

                # Clear digest
                async with self._lock:
                    queue_path = NOTIFICATIONS_DIR / f"{user_id}.json"
                    data = await read_json(queue_path, default={"queue": [], "digest": []})
                    data["digest"] = []
                    await write_json_atomic(queue_path, data)

                return True
        except discord.Forbidden:
            logger.warning(f"Cannot send digest to user {user_id} - DMs closed")
        except Exception as e:
            logger.error(f"Error sending digest to {user_id}: {e}", exc_info=True)

        return False

    async def send_all_digests(self) -> int:
        """
        Send digests to all users with digest mode enabled.
        Should be called once per day.

        Returns number of digests sent.
        """
        sent_count = 0

        if not NOTIFICATIONS_DIR.exists():
            return 0

        for queue_file in NOTIFICATIONS_DIR.glob("*.json"):
            try:
                user_id = int(queue_file.stem)
            except ValueError:
                continue

            # Check if user has digest mode enabled
            from classes.profile import _load_record
            profile = await _load_record(user_id)

            if profile:
                notif_prefs = profile.get("notification_preferences", {})
                if notif_prefs.get("digest_mode", False):
                    if await self.send_digest(user_id):
                        sent_count += 1

        if sent_count > 0:
            logger.info(f"Sent {sent_count} daily digests")

        return sent_count


# Global service instance
_notification_service: Optional[NotificationService] = None


def get_notification_service(bot: discord.Client) -> NotificationService:
    """Get or create the global notification service instance."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService(bot)
    return _notification_service
