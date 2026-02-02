"""
Report storage - persistent storage for user reports and reporter statistics.

Provides per-guild storage for report data with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso
from .types import UserReport

# Storage directory
REPORT_DIR = BASE_DIR / "data" / "moderation"


class ReportStore:
    """Per-guild storage for user reports."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = REPORT_DIR / str(guild_id)
        self.reports_path = self.root / "reports.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Reports ──────────────────────────────────────────────────────────────

    async def _read_reports(self) -> Dict[str, Any]:
        """Read reports file."""
        default = {
            "reports": {},
            "reporter_stats": {},
            "config": {
                "auto_close_days": 14,
                "categories": [
                    "harassment",
                    "scam_attempt",
                    "spam",
                    "nsfw_violation",
                    "impersonation",
                    "other",
                ],
            },
        }
        data = await read_json(self.reports_path, default=default)
        if not isinstance(data, dict):
            return default
        # Ensure all keys exist
        for key in default:
            if key not in data:
                data[key] = default[key]
        return data

    async def _write_reports(self, data: Dict[str, Any]) -> None:
        """Write reports file."""
        await write_json_atomic(self.reports_path, data)

    async def add_report(self, report: UserReport) -> None:
        """Add a new report."""
        async with self._lock:
            data = await self._read_reports()
            data["reports"][report.id] = report.to_dict()

            # Update reporter stats
            reporter_id = str(report.reporter_id)
            if reporter_id not in data["reporter_stats"]:
                data["reporter_stats"][reporter_id] = {
                    "total": 0,
                    "upheld": 0,
                    "dismissed": 0,
                    "flagged": False,
                }

            data["reporter_stats"][reporter_id]["total"] += 1

            await self._write_reports(data)

    async def get_report(self, report_id: str) -> Optional[UserReport]:
        """Get a specific report by ID."""
        async with self._lock:
            data = await self._read_reports()
            report_data = data["reports"].get(report_id)
            if not report_data:
                return None
            return UserReport.from_dict(report_data)

    async def get_reports(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[UserReport]:
        """
        Get reports with optional filters.

        Args:
            status: Filter by status (open/assigned/resolved/dismissed)
            category: Filter by category

        Returns:
            List of reports
        """
        async with self._lock:
            data = await self._read_reports()
            reports = []

            for report_data in data["reports"].values():
                # Apply filters
                if status and report_data.get("status") != status:
                    continue
                if category and report_data.get("category") != category:
                    continue

                reports.append(UserReport.from_dict(report_data))

            # Sort by created_at (most recent first)
            reports.sort(key=lambda r: r.created_at, reverse=True)
            return reports

    async def update_report(self, report_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update a report.

        Returns True if updated, False if not found.
        """
        async with self._lock:
            data = await self._read_reports()

            if report_id not in data["reports"]:
                return False

            data["reports"][report_id].update(updates)
            await self._write_reports(data)
            return True

    async def assign_report(self, report_id: str, mod_id: int) -> bool:
        """Assign a report to a moderator."""
        return await self.update_report(
            report_id,
            {
                "assigned_mod_id": mod_id,
                "status": "assigned",
            }
        )

    async def resolve_report(
        self,
        report_id: str,
        outcome: str,
        notes: Optional[str] = None,
    ) -> bool:
        """Resolve a report."""
        updates = {
            "status": "resolved",
            "resolved_at": dt_to_iso(utcnow()),
            "outcome": outcome,
        }

        if notes:
            async with self._lock:
                data = await self._read_reports()
                if report_id in data["reports"]:
                    report = data["reports"][report_id]
                    if "notes" not in report:
                        report["notes"] = []
                    report["notes"].append(notes)

        success = await self.update_report(report_id, updates)

        if success:
            # Update reporter stats
            await self._update_reporter_stats_on_resolve(report_id, outcome)

        return success

    async def dismiss_report(self, report_id: str, reason: str) -> bool:
        """Dismiss a report."""
        success = await self.update_report(
            report_id,
            {
                "status": "dismissed",
                "resolved_at": dt_to_iso(utcnow()),
                "outcome": f"dismissed: {reason}",
            }
        )

        if success:
            await self._update_reporter_stats_on_resolve(report_id, "dismissed")

        return success

    async def _update_reporter_stats_on_resolve(
        self,
        report_id: str,
        outcome: str,
    ) -> None:
        """Update reporter statistics when a report is resolved."""
        async with self._lock:
            data = await self._read_reports()

            if report_id not in data["reports"]:
                return

            report = data["reports"][report_id]
            reporter_id = str(report["reporter_id"])

            if reporter_id not in data["reporter_stats"]:
                return

            stats = data["reporter_stats"][reporter_id]

            if outcome == "dismissed":
                stats["dismissed"] += 1
            else:
                stats["upheld"] += 1

            # Flag reporters with high false report rate
            if stats["total"] >= 5:
                false_rate = stats["dismissed"] / stats["total"]
                if false_rate >= 0.6:  # 60% false reports
                    stats["flagged"] = True

            await self._write_reports(data)

    async def create_mod_thread(self, report_id: str, thread_id: int) -> bool:
        """Associate a mod thread with a report."""
        return await self.update_report(
            report_id,
            {"mod_thread_id": thread_id}
        )

    # ─── Reporter Stats ───────────────────────────────────────────────────────

    async def get_reporter_stats(self, user_id: int) -> Dict[str, Any]:
        """Get statistics for a reporter."""
        async with self._lock:
            data = await self._read_reports()
            user_key = str(user_id)

            if user_key not in data["reporter_stats"]:
                return {
                    "total": 0,
                    "upheld": 0,
                    "dismissed": 0,
                    "flagged": False,
                }

            return data["reporter_stats"][user_key]

    async def is_reporter_flagged(self, user_id: int) -> bool:
        """Check if a reporter is flagged for false reports."""
        stats = await self.get_reporter_stats(user_id)
        return stats.get("flagged", False)

    # ─── Auto-Close ───────────────────────────────────────────────────────────

    async def get_stale_reports(self, days: int = 14) -> List[UserReport]:
        """
        Get reports that haven't been updated in X days.

        Args:
            days: Number of days to consider stale

        Returns:
            List of stale reports with status "open" or "assigned"
        """
        from datetime import timedelta
        from .utils import iso_to_dt

        async with self._lock:
            data = await self._read_reports()
            now = utcnow()
            stale_threshold = now - timedelta(days=days)

            stale_reports = []

            for report_data in data["reports"].values():
                status = report_data.get("status")
                if status not in ["open", "assigned"]:
                    continue

                created_at = iso_to_dt(report_data.get("created_at"))
                if created_at and created_at < stale_threshold:
                    stale_reports.append(UserReport.from_dict(report_data))

            return stale_reports

    # ─── Configuration ────────────────────────────────────────────────────────

    async def get_config(self) -> Dict[str, Any]:
        """Get report configuration."""
        async with self._lock:
            data = await self._read_reports()
            return data["config"]

    async def update_config(self, updates: Dict[str, Any]) -> None:
        """Update report configuration."""
        async with self._lock:
            data = await self._read_reports()
            data["config"].update(updates)
            await self._write_reports(data)

    async def get_auto_close_days(self) -> int:
        """Get auto-close days setting."""
        config = await self.get_config()
        return config.get("auto_close_days", 14)

    async def get_categories(self) -> List[str]:
        """Get available report categories."""
        config = await self.get_config()
        return config.get("categories", [])
