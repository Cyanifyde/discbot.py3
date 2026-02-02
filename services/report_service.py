"""
Report service - business logic for user report management.

Handles report creation, assignment, resolution, and auto-closing.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.report_storage import ReportStore
from core.types import UserReport
from core.utils import utcnow, dt_to_iso

if TYPE_CHECKING:
    import discord


class ReportService:
    """Business logic for report management."""

    def __init__(self) -> None:
        self._stores: Dict[int, ReportStore] = {}

    def _get_store(self, guild_id: int) -> ReportStore:
        """Get or create a report store for a guild."""
        if guild_id not in self._stores:
            self._stores[guild_id] = ReportStore(guild_id)
        return self._stores[guild_id]

    async def initialize_store(self, guild_id: int) -> None:
        """Initialize storage for a guild."""
        store = self._get_store(guild_id)
        await store.initialize()

    # ─── Report Management ────────────────────────────────────────────────────

    async def create_report(
        self,
        reporter_id: int,
        target_id: int,
        message_id: int,
        guild_id: int,
        category: str,
        priority: str = "normal",
    ) -> UserReport:
        """
        Create a new report.

        Args:
            reporter_id: User filing the report
            target_id: User being reported
            message_id: Message ID being reported
            guild_id: Guild ID
            category: Report category
            priority: Report priority (urgent/normal/low)

        Returns:
            Created UserReport
        """
        store = self._get_store(guild_id)
        await store.initialize()

        # Check if reporter is flagged
        is_flagged = await store.is_reporter_flagged(reporter_id)
        if is_flagged:
            priority = "low"  # Downgrade priority for flagged reporters

        report = UserReport(
            id=str(uuid.uuid4()),
            reporter_id=reporter_id,
            target_id=target_id,
            target_message_id=message_id,
            guild_id=guild_id,
            category=category,
            priority=priority,
            status="open",
            created_at=dt_to_iso(utcnow()),
        )

        await store.add_report(report)
        return report

    async def get_report(self, guild_id: int, report_id: str) -> Optional[UserReport]:
        """Get a specific report by ID."""
        store = self._get_store(guild_id)
        return await store.get_report(report_id)

    async def get_reports(
        self,
        guild_id: int,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[UserReport]:
        """Get reports with optional filters."""
        store = self._get_store(guild_id)
        return await store.get_reports(status, category)

    async def assign_report(
        self,
        guild_id: int,
        report_id: str,
        mod_id: int,
    ) -> bool:
        """Assign a report to a moderator."""
        store = self._get_store(guild_id)
        return await store.assign_report(report_id, mod_id)

    async def resolve_report(
        self,
        guild_id: int,
        report_id: str,
        outcome: str,
        notes: Optional[str] = None,
    ) -> bool:
        """Resolve a report with outcome."""
        store = self._get_store(guild_id)
        return await store.resolve_report(report_id, outcome, notes)

    async def dismiss_report(
        self,
        guild_id: int,
        report_id: str,
        reason: str,
    ) -> bool:
        """Dismiss a report."""
        store = self._get_store(guild_id)
        return await store.dismiss_report(report_id, reason)

    # ─── Mod Threads ──────────────────────────────────────────────────────────

    async def create_mod_thread(
        self,
        guild_id: int,
        report_id: str,
        thread_id: int,
    ) -> bool:
        """Associate a private thread with a report."""
        store = self._get_store(guild_id)
        return await store.create_mod_thread(report_id, thread_id)

    # ─── Reporter Stats ───────────────────────────────────────────────────────

    async def get_reporter_stats(
        self,
        guild_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """Get reporter statistics."""
        store = self._get_store(guild_id)
        return await store.get_reporter_stats(user_id)

    async def check_reporter_stats(
        self,
        guild_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """
        Check reporter stats and return warnings if needed.

        Returns:
            Dict with stats and any warnings
        """
        stats = await self.get_reporter_stats(guild_id, user_id)

        warnings = []
        if stats.get("flagged"):
            warnings.append("Flagged for frequent false reports")

        if stats["total"] >= 3:
            false_rate = stats["dismissed"] / stats["total"]
            if false_rate >= 0.5:
                warnings.append(f"High false report rate: {false_rate:.0%}")

        return {
            **stats,
            "warnings": warnings,
        }

    # ─── Auto-Close ───────────────────────────────────────────────────────────

    async def auto_close_stale(self, guild_id: int) -> List[UserReport]:
        """
        Auto-close stale reports.

        Returns:
            List of reports that were auto-closed
        """
        store = self._get_store(guild_id)

        auto_close_days = await store.get_auto_close_days()
        stale_reports = await store.get_stale_reports(auto_close_days)

        closed = []
        for report in stale_reports:
            success = await store.dismiss_report(
                report.id,
                f"Auto-closed after {auto_close_days} days of inactivity"
            )
            if success:
                closed.append(report)

        return closed

    # ─── Statistics ───────────────────────────────────────────────────────────

    async def get_report_stats(self, guild_id: int) -> Dict[str, Any]:
        """Get report statistics."""
        store = self._get_store(guild_id)

        all_reports = await store.get_reports()

        # Count by status
        by_status = {}
        for report in all_reports:
            status = report.status
            by_status[status] = by_status.get(status, 0) + 1

        # Count by category
        by_category = {}
        for report in all_reports:
            category = report.category
            by_category[category] = by_category.get(category, 0) + 1

        # Count by priority
        by_priority = {}
        for report in all_reports:
            priority = report.priority
            by_priority[priority] = by_priority.get(priority, 0) + 1

        return {
            "total": len(all_reports),
            "by_status": by_status,
            "by_category": by_category,
            "by_priority": by_priority,
        }

    # ─── Configuration ────────────────────────────────────────────────────────

    async def get_categories(self, guild_id: int) -> List[str]:
        """Get available report categories."""
        store = self._get_store(guild_id)
        return await store.get_categories()

    async def get_auto_close_days(self, guild_id: int) -> int:
        """Get auto-close days setting."""
        store = self._get_store(guild_id)
        return await store.get_auto_close_days()

    async def set_auto_close_days(self, guild_id: int, days: int) -> None:
        """Set auto-close days setting."""
        store = self._get_store(guild_id)
        await store.update_config({"auto_close_days": days})


# Global service instance
report_service = ReportService()
