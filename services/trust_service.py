"""
Trust service - handles trust score calculation and management.

Trust scoring is based on:
- children_count (15%): Number of servers where user is trusted
- upflow_status (20%): Upstream approval success rate
- vouches (25%): Verified vouches from other users
- link_age (15%): How long user has been in the server
- approval_rate (25%): Mod action approval rate
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import discord

from core.trust_storage import TrustStore
from core.types import TrustScore, Vouch
from core.utils import utcnow, dt_to_iso, iso_to_dt
from core.link_storage import get_link_storage

logger = logging.getLogger("discbot.trust")

# Trust tier thresholds
TIER_THRESHOLDS = {
    "untrusted": (0, 20),
    "neutral": (21, 50),
    "trusted": (51, 80),
    "highly_trusted": (81, 100),
}

# Action permission requirements (minimum trust score)
ACTION_PERMISSIONS = {
    "cross_server_sync": 50,
    "vouch_others": 60,
    "mediate_disputes": 80,
}

# Decay multipliers
POSITIVE_EVENT_DECAY_RATE = 1.0  # 1 point per day
NEGATIVE_EVENT_DECAY_RATE = 2.0  # 2 points per day (decays faster)


class TrustService:
    """Service for calculating and managing trust scores."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._calculation_lock = asyncio.Lock()

    def get_tier(self, score: float) -> str:
        """Get trust tier from score."""
        for tier, (min_score, max_score) in TIER_THRESHOLDS.items():
            if min_score <= score <= max_score:
                return tier
        return "untrusted"

    def check_action_permission(self, score: float, action: str) -> bool:
        """Check if a score meets the minimum requirement for an action."""
        required_score = ACTION_PERMISSIONS.get(action, 0)
        return score >= required_score

    async def calculate_score(
        self,
        user_id: int,
        guild_id: int,
        *,
        store: Optional[TrustStore] = None,
    ) -> TrustScore:
        """
        Calculate trust score for a user in a guild.

        Weight distribution:
        - children_count: 15%
        - upflow_status: 20%
        - vouches: 25%
        - link_age: 15%
        - approval_rate: 25%
        """
        if store is None:
            store = TrustStore(guild_id)
            await store.initialize()

        async with self._calculation_lock:
            # Get existing score or create new
            existing_score = await store.get_score(user_id)

            # Calculate component scores
            children_count_score = await self._calculate_children_count(user_id, guild_id)
            upflow_status_score = await self._calculate_upflow_status(user_id, store)
            vouches_score = await self._calculate_vouches_score(user_id, store)
            link_age_score = await self._calculate_link_age(user_id, guild_id)
            approval_rate_score = await self._calculate_approval_rate(user_id, store)

            # Apply weights and calculate total
            total_score = (
                children_count_score * 0.15
                + upflow_status_score * 0.20
                + vouches_score * 0.25
                + link_age_score * 0.15
                + approval_rate_score * 0.25
            )

            # Clamp to 0-100
            total_score = max(0.0, min(100.0, total_score))

            # Determine tier
            tier = self.get_tier(total_score)

            # Create score object
            score = TrustScore(
                user_id=user_id,
                guild_id=guild_id,
                children_count_score=children_count_score,
                upflow_status_score=upflow_status_score,
                vouches_score=vouches_score,
                link_age_score=link_age_score,
                approval_rate_score=approval_rate_score,
                total_score=total_score,
                tier=tier,
                last_updated=dt_to_iso(utcnow()),
            )

            # Save to storage
            await store.save_score(score)

            return score

    async def _calculate_children_count(self, user_id: int, guild_id: int) -> float:
        """
        Calculate score based on number of servers where user is trusted.
        Returns 0-100.
        """
        storage = await get_link_storage()
        children = await storage.get_children(guild_id)

        total_children = len(children)
        trusted_children = sum(1 for c in children if c.get("trust_level") == "trusted")

        if total_children == 0:
            return 25.0

        score = 25.0
        score += trusted_children * 15.0
        score += (total_children - trusted_children) * 5.0

        return max(0.0, min(100.0, score))

    async def _calculate_upflow_status(self, user_id: int, store: TrustStore) -> float:
        """
        Calculate score based on upstream approval success rate.
        Returns 0-100.
        """
        events = await store.get_events(user_id)

        # Count upstream approvals vs rejections
        approvals = sum(1 for e in events if e.get("event_type") == "upstream_approved")
        rejections = sum(1 for e in events if e.get("event_type") == "upstream_rejected")

        total = approvals + rejections
        if total == 0:
            return 50.0  # Neutral if no history

        rate = approvals / total
        return rate * 100.0

    async def _calculate_vouches_score(self, user_id: int, store: TrustStore) -> float:
        """
        Calculate score based on verified vouches.
        Returns 0-100.
        """
        vouches = await store.get_vouches_for(user_id)

        if not vouches:
            return 25.0  # Low score if no vouches

        # Count verified vouches
        verified_count = sum(1 for v in vouches if v.verified_by_mod is not None)
        mutual_count = sum(1 for v in vouches if v.mutual)

        # Score calculation
        # Base: 25, +5 per verified vouch (max 50), +10 per mutual (max 50)
        score = 25.0
        score += min(verified_count * 5, 50)
        score += min(mutual_count * 10, 25)

        return min(score, 100.0)

    async def _calculate_link_age(self, user_id: int, guild_id: int) -> float:
        """
        Calculate score based on how long user has been in the server.
        Returns 0-100.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return 50.0

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                return 50.0

        joined_at = getattr(member, "joined_at", None)
        if not joined_at:
            return 50.0

        try:
            age_days = (utcnow() - joined_at).total_seconds() / 86400
        except Exception:
            return 50.0

        return max(0.0, min(100.0, (age_days / 365.0) * 100.0))

    async def _calculate_approval_rate(self, user_id: int, store: TrustStore) -> float:
        """
        Calculate score based on mod action approval rate.
        Returns 0-100.
        """
        events = await store.get_events(user_id)

        # Filter for positive and negative events
        positive = [e for e in events if e.get("positive")]
        negative = [e for e in events if not e.get("positive")]

        # Apply decay based on age
        now = utcnow()

        positive_weight = 0.0
        for event in positive:
            age_days = self._get_event_age_days(event, now)
            decayed = event.get("weight", 1.0) - (age_days * POSITIVE_EVENT_DECAY_RATE / 100)
            positive_weight += max(0, decayed)

        negative_weight = 0.0
        for event in negative:
            age_days = self._get_event_age_days(event, now)
            decayed = event.get("weight", 1.0) - (age_days * NEGATIVE_EVENT_DECAY_RATE / 100)
            negative_weight += max(0, decayed)

        # Calculate approval rate
        total_weight = positive_weight + negative_weight
        if total_weight == 0:
            return 50.0  # Neutral if no history

        rate = positive_weight / total_weight
        return rate * 100.0

    def _get_event_age_days(self, event: Dict, now: datetime) -> float:
        """Get age of an event in days."""
        timestamp_str = event.get("timestamp", "")
        if not timestamp_str:
            return 0.0

        try:
            timestamp = iso_to_dt(timestamp_str)
            delta = now - timestamp
            return delta.total_seconds() / 86400  # Convert to days
        except Exception:
            return 0.0

    async def record_positive_event(
        self,
        user_id: int,
        guild_id: int,
        event_type: str,
        weight: float = 1.0,
        details: Optional[str] = None,
    ) -> None:
        """Record a positive trust event."""
        store = TrustStore(guild_id)
        await store.initialize()
        await store.add_event(user_id, event_type, weight, positive=True, details=details)

        # Recalculate score
        await self.calculate_score(user_id, guild_id, store=store)

    async def record_negative_event(
        self,
        user_id: int,
        guild_id: int,
        event_type: str,
        weight: float = 1.0,
        details: Optional[str] = None,
    ) -> None:
        """Record a negative trust event."""
        store = TrustStore(guild_id)
        await store.initialize()
        await store.add_event(user_id, event_type, weight, positive=False, details=details)

        # Recalculate score
        await self.calculate_score(user_id, guild_id, store=store)

    async def run_decay(self, guild_id: int) -> int:
        """
        Run decay on all trust scores in a guild.
        Negative events decay at 2x rate of positive events.

        Returns number of scores recalculated.
        """
        store = TrustStore(guild_id)
        await store.initialize()

        scores = await store.get_all_scores()

        count = 0
        for score in scores:
            await self.calculate_score(score.user_id, guild_id, store=store)
            count += 1

        logger.info(f"Ran decay for {count} trust scores in guild {guild_id}")
        return count

    async def get_score(self, user_id: int, guild_id: int) -> Optional[TrustScore]:
        """Get trust score for a user, calculating if needed."""
        store = TrustStore(guild_id)
        await store.initialize()

        score = await store.get_score(user_id)

        # Calculate if doesn't exist or is stale (> 24 hours old)
        if score is None or self._is_stale(score):
            score = await self.calculate_score(user_id, guild_id, store=store)

        return score

    def _is_stale(self, score: TrustScore, hours: int = 24) -> bool:
        """Check if a score is stale and needs recalculation."""
        try:
            last_updated = iso_to_dt(score.last_updated)
            age = utcnow() - last_updated
            return age > timedelta(hours=hours)
        except Exception:
            return True


# Global service instance
_trust_service: Optional[TrustService] = None


def get_trust_service(bot: discord.Client) -> TrustService:
    """Get or create the global trust service instance."""
    global _trust_service
    if _trust_service is None:
        _trust_service = TrustService(bot)
    return _trust_service
