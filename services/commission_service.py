"""
Commission service - business logic for commission management.

Handles commission creation, stage advancement, waitlist management, and automation.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.commission_storage import CommissionStore
from core.types import Commission, WaitlistEntry
from core.utils import utcnow, dt_to_iso, iso_to_dt, format_commission_status

if TYPE_CHECKING:
    import discord


class CommissionService:
    """Business logic for commission management."""

    def __init__(self) -> None:
        self._stores: Dict[tuple[int, int], CommissionStore] = {}

    def _get_store(self, guild_id: int, artist_id: int) -> CommissionStore:
        """Get or create a commission store for an artist in a guild."""
        key = (guild_id, artist_id)
        if key not in self._stores:
            self._stores[key] = CommissionStore(guild_id, artist_id)
        return self._stores[key]

    async def initialize_store(self, guild_id: int, artist_id: int) -> None:
        """Initialize storage for an artist."""
        store = self._get_store(guild_id, artist_id)
        await store.initialize()

    # ─── Commission Management ────────────────────────────────────────────────

    async def create_commission(
        self,
        artist_id: int,
        client_id: int,
        guild_id: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> Commission:
        """
        Create a new commission.

        Args:
            artist_id: Artist's user ID
            client_id: Client's user ID
            guild_id: Guild ID
            details: Optional commission details (price, notes, deadline, etc.)

        Returns:
            Created Commission object
        """
        store = self._get_store(guild_id, artist_id)
        await store.initialize()

        details = details or {}
        now = dt_to_iso(utcnow())

        # Get custom stages
        custom_stages = await store.get_custom_stages()
        initial_stage = custom_stages[0] if custom_stages else "Inquiry"

        commission = Commission(
            id=str(uuid.uuid4()),
            artist_id=artist_id,
            client_id=client_id,
            guild_id=guild_id,
            stage=initial_stage,
            created_at=now,
            updated_at=now,
            price=details.get("price", 0.0),
            currency=details.get("currency", "USD"),
            notes=details.get("notes", ""),
            deadline=details.get("deadline"),
            tags=details.get("tags", []),
            revisions_limit=details.get("revisions_limit", 3),
            payment_status=details.get("payment_status", "pending"),
            incognito=details.get("incognito", False),
        )

        await store.add_commission(commission)
        return commission

    async def get_commission(self, artist_id: int, guild_id: int, commission_id: str) -> Optional[Commission]:
        """Get a specific commission by ID."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_commission(commission_id)

    async def get_active_commissions(self, artist_id: int, guild_id: int) -> List[Commission]:
        """Get all active commissions for an artist."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_active_commissions()

    async def advance_stage(
        self,
        artist_id: int,
        guild_id: int,
        commission_id: str,
        new_stage: str,
        actor_id: int,
    ) -> bool:
        """
        Advance commission to a new stage.

        Returns True if successful, False if commission not found.
        """
        store = self._get_store(guild_id, artist_id)
        commission = await store.get_commission(commission_id)

        if not commission:
            return False

        # Check if new stage is valid
        custom_stages = await store.get_custom_stages()
        if new_stage not in custom_stages:
            return False

        success = await store.update_commission(commission_id, {"stage": new_stage})

        # If moving to Completed or Archived, archive it
        if success and new_stage in ["Completed", "Archived"]:
            await store.remove_commission(commission_id, archive=True)

        return success

    async def update_commission(
        self,
        artist_id: int,
        guild_id: int,
        commission_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """Update commission fields."""
        store = self._get_store(guild_id, artist_id)
        return await store.update_commission(commission_id, updates)

    async def add_revision(
        self,
        artist_id: int,
        guild_id: int,
        commission_id: str,
    ) -> bool:
        """
        Log a revision request.

        Returns False if revision limit exceeded, True otherwise.
        """
        store = self._get_store(guild_id, artist_id)
        commission = await store.get_commission(commission_id)

        if not commission:
            return False

        if commission.revisions_used >= commission.revisions_limit:
            return False

        await store.update_commission(
            commission_id,
            {"revisions_used": commission.revisions_used + 1}
        )
        return True

    async def confirm_payment(
        self,
        artist_id: int,
        guild_id: int,
        commission_id: str,
        confirmed_by: int,
    ) -> bool:
        """Confirm payment received."""
        store = self._get_store(guild_id, artist_id)
        return await store.update_commission(
            commission_id,
            {"payment_status": "paid"}
        )

    async def get_commission_history(
        self,
        artist_id: int,
        guild_id: int,
        limit: Optional[int] = None,
    ) -> List[Commission]:
        """Get commission history."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_history(limit)

    async def get_completed_count(self, artist_id: int, guild_id: int) -> int:
        """Get count of completed commissions."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_completed_count()

    # ─── Slots Management ─────────────────────────────────────────────────────

    async def get_slots_config(self, artist_id: int, guild_id: int) -> Dict[str, Any]:
        """Get slot configuration."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_slots_config()

    async def update_slots(self, artist_id: int, guild_id: int, slots_total: int) -> None:
        """Update total slots count."""
        store = self._get_store(guild_id, artist_id)
        await store.update_slots(slots_total)

    async def set_auto_close(self, artist_id: int, guild_id: int, enabled: bool) -> None:
        """Enable/disable auto-close when slots full."""
        store = self._get_store(guild_id, artist_id)
        await store.set_auto_close(enabled)

    async def auto_manage_slots(self, artist_id: int, guild_id: int) -> None:
        """
        Automatically manage slots based on config.

        Called after commission state changes.
        """
        store = self._get_store(guild_id, artist_id)
        config = await store.get_slots_config()

        if not config["auto_close"]:
            return

        # Auto-promote from waitlist if slots available
        if config["slots_available"] > 0:
            await self.promote_from_waitlist(artist_id, guild_id)

    # ─── Custom Stages ────────────────────────────────────────────────────────

    async def get_custom_stages(self, artist_id: int, guild_id: int) -> List[str]:
        """Get custom stage names."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_custom_stages()

    async def set_custom_stages(self, artist_id: int, guild_id: int, stages: List[str]) -> None:
        """Set custom stage names."""
        store = self._get_store(guild_id, artist_id)
        await store.set_custom_stages(stages)

    # ─── Waitlist Management ──────────────────────────────────────────────────

    async def add_to_waitlist(
        self,
        artist_id: int,
        client_id: int,
        guild_id: int,
        notes: str = "",
    ) -> WaitlistEntry:
        """Add client to waitlist."""
        store = self._get_store(guild_id, artist_id)
        await store.initialize()

        # Get current waitlist to determine position
        waitlist = await store.get_waitlist()
        position = len(waitlist) + 1

        entry = WaitlistEntry(
            id=str(uuid.uuid4()),
            artist_id=artist_id,
            client_id=client_id,
            guild_id=guild_id,
            position=position,
            notes=notes,
            created_at=dt_to_iso(utcnow()),
        )

        await store.add_to_waitlist(entry)
        return entry

    async def get_waitlist(self, artist_id: int, guild_id: int) -> List[WaitlistEntry]:
        """Get waitlist entries."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_waitlist()

    async def remove_from_waitlist(
        self,
        artist_id: int,
        guild_id: int,
        entry_id: str,
    ) -> Optional[WaitlistEntry]:
        """Remove entry from waitlist."""
        store = self._get_store(guild_id, artist_id)
        return await store.remove_from_waitlist(entry_id)

    async def promote_from_waitlist(
        self,
        artist_id: int,
        guild_id: int,
    ) -> Optional[WaitlistEntry]:
        """
        Promote next entry from waitlist.

        Returns the promoted entry or None if waitlist is empty.
        """
        store = self._get_store(guild_id, artist_id)
        entry = await store.get_next_waitlist_entry()

        if not entry:
            return None

        # Mark as notified
        await store.update_waitlist_entry(
            entry.id,
            {"notified_at": dt_to_iso(utcnow())}
        )

        return entry

    # ─── Blacklist ────────────────────────────────────────────────────────────

    async def add_to_blacklist(
        self,
        artist_id: int,
        guild_id: int,
        user_id: int,
        reason: str,
    ) -> None:
        """Add user to artist's blacklist."""
        store = self._get_store(guild_id, artist_id)
        await store.add_to_blacklist(user_id, reason)

    async def remove_from_blacklist(
        self,
        artist_id: int,
        guild_id: int,
        user_id: int,
    ) -> bool:
        """Remove user from blacklist."""
        store = self._get_store(guild_id, artist_id)
        return await store.remove_from_blacklist(user_id)

    async def check_blacklist(
        self,
        artist_id: int,
        guild_id: int,
        client_id: int,
    ) -> bool:
        """Check if client is blacklisted."""
        store = self._get_store(guild_id, artist_id)
        return await store.is_blacklisted(client_id)

    async def get_blacklist(self, artist_id: int, guild_id: int) -> List[Dict[str, Any]]:
        """Get blacklisted users."""
        store = self._get_store(guild_id, artist_id)
        return await store.get_blacklist()

    # ─── Statistics ───────────────────────────────────────────────────────────

    async def get_repeat_client_count(
        self,
        artist_id: int,
        guild_id: int,
        client_id: int,
    ) -> int:
        """Get number of completed commissions from a client."""
        store = self._get_store(guild_id, artist_id)
        history = await store.get_history()
        return sum(1 for c in history if c.client_id == client_id)

    async def get_commission_stats(
        self,
        artist_id: int,
        guild_id: int,
    ) -> Dict[str, Any]:
        """Get commission statistics summary."""
        store = self._get_store(guild_id, artist_id)

        active = await store.get_active_commissions()
        history = await store.get_history()
        waitlist = await store.get_waitlist()
        config = await store.get_slots_config()

        # Calculate total earnings from completed
        total_earnings = sum(c.price for c in history)

        return {
            "active_count": len(active),
            "completed_count": len(history),
            "waitlist_count": len(waitlist),
            "total_earnings": total_earnings,
            "slots_total": config["slots_total"],
            "slots_available": config["slots_available"],
        }

    # ─── Deadline Checking ────────────────────────────────────────────────────

    async def check_deadlines(self, artist_id: int, guild_id: int) -> List[Commission]:
        """
        Check for upcoming or overdue deadlines.

        Returns list of commissions with deadlines within 3 days or overdue.
        """
        store = self._get_store(guild_id, artist_id)
        active = await store.get_active_commissions()

        now = utcnow()
        approaching = []

        for commission in active:
            if not commission.deadline:
                continue

            deadline_dt = iso_to_dt(commission.deadline)
            if not deadline_dt:
                continue

            # Check if within 3 days or overdue
            time_until = deadline_dt - now
            if time_until.total_seconds() < (3 * 24 * 60 * 60):  # 3 days
                approaching.append(commission)

        return approaching


# Global service instance
commission_service = CommissionService()
