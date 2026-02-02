"""
Portfolio storage - persistent storage for user portfolios.

Provides per-user (global) storage for portfolio entries with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso
from .types import PortfolioEntry

# Storage directory
PORTFOLIO_DIR = BASE_DIR / "data" / "portfolios"


class PortfolioStore:
    """Per-user (global) storage for portfolio entries."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.portfolio_path = PORTFOLIO_DIR / f"{user_id}.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(PORTFOLIO_DIR.mkdir, parents=True, exist_ok=True)

    # ─── Portfolio Data ───────────────────────────────────────────────────────

    async def _read_portfolio(self) -> Dict[str, Any]:
        """Read portfolio file."""
        default = {
            "entries": [],
            "categories": ["illustrations", "icons", "reference_sheets"],
            "custom_order": [],
            "default_privacy": "public",
            "rates": {},
            "rate_card_settings": {
                "title": "Commission Rates",
                "subtitle": "Quality digital artwork tailored to your vision",
                "status": "open",
                "currency": "$",
                "image": None,
            },
        }
        data = await read_json(self.portfolio_path, default=default)
        if not isinstance(data, dict):
            return default
        # Ensure all keys exist
        for key in default:
            if key not in data:
                data[key] = default[key]
        return data

    async def _write_portfolio(self, data: Dict[str, Any]) -> None:
        """Write portfolio file."""
        await write_json_atomic(self.portfolio_path, data)

    # ─── Entries ──────────────────────────────────────────────────────────────

    async def add_entry(self, entry: PortfolioEntry) -> None:
        """Add a new portfolio entry."""
        async with self._lock:
            data = await self._read_portfolio()
            data["entries"].append(entry.to_dict())
            await self._write_portfolio(data)

    async def get_entry(self, entry_id: str) -> Optional[PortfolioEntry]:
        """Get a specific entry by ID."""
        async with self._lock:
            data = await self._read_portfolio()
            for entry_data in data["entries"]:
                if entry_data.get("id") == entry_id:
                    return PortfolioEntry.from_dict(entry_data)
            return None

    async def get_all_entries(self, viewer_id: Optional[int] = None) -> List[PortfolioEntry]:
        """
        Get all portfolio entries.

        Args:
            viewer_id: ID of the viewer (for privacy filtering)

        Returns:
            List of entries (filtered by privacy if viewer_id provided)
        """
        async with self._lock:
            data = await self._read_portfolio()
            entries = [PortfolioEntry.from_dict(e) for e in data["entries"]]

            # Apply privacy filtering
            if viewer_id is not None and viewer_id != self.user_id:
                # Filter out private entries for non-owners
                entries = [e for e in entries if e.privacy != "private"]

            # Apply custom ordering if specified
            if data["custom_order"]:
                ordered = []
                order_map = {eid: i for i, eid in enumerate(data["custom_order"])}
                # Sort by custom order
                entries.sort(key=lambda e: order_map.get(e.id, 999999))

            return entries

    async def get_entries_by_category(
        self,
        category: str,
        viewer_id: Optional[int] = None
    ) -> List[PortfolioEntry]:
        """Get entries filtered by category."""
        entries = await self.get_all_entries(viewer_id)
        return [e for e in entries if e.category == category]

    async def update_entry(self, entry_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update an entry.

        Returns True if updated, False if not found.
        """
        async with self._lock:
            data = await self._read_portfolio()
            for entry_data in data["entries"]:
                if entry_data.get("id") == entry_id:
                    entry_data.update(updates)
                    await self._write_portfolio(data)
                    return True
            return False

    async def remove_entry(self, entry_id: str) -> bool:
        """
        Remove an entry.

        Returns True if removed, False if not found.
        """
        async with self._lock:
            data = await self._read_portfolio()
            original_len = len(data["entries"])
            data["entries"] = [e for e in data["entries"] if e.get("id") != entry_id]

            if len(data["entries"]) < original_len:
                # Also remove from custom order if present
                if entry_id in data["custom_order"]:
                    data["custom_order"].remove(entry_id)
                await self._write_portfolio(data)
                return True
            return False

    async def set_featured(self, entry_id: str) -> bool:
        """
        Set an entry as featured (unsets all others).

        Returns True if successful, False if not found.
        """
        async with self._lock:
            data = await self._read_portfolio()
            found = False

            for entry_data in data["entries"]:
                if entry_data.get("id") == entry_id:
                    entry_data["featured"] = True
                    found = True
                else:
                    entry_data["featured"] = False

            if found:
                await self._write_portfolio(data)
            return found

    async def increment_views(self, entry_id: str) -> bool:
        """
        Increment view count for an entry.

        Returns True if successful, False if not found.
        """
        async with self._lock:
            data = await self._read_portfolio()
            for entry_data in data["entries"]:
                if entry_data.get("id") == entry_id:
                    entry_data["views"] = entry_data.get("views", 0) + 1
                    await self._write_portfolio(data)
                    return True
            return False

    # ─── Ordering ─────────────────────────────────────────────────────────────

    async def set_custom_order(self, order: List[str]) -> None:
        """Set custom display order for entries."""
        async with self._lock:
            data = await self._read_portfolio()
            data["custom_order"] = order
            await self._write_portfolio(data)

    async def reorder_entry(self, entry_id: str, new_position: int) -> bool:
        """
        Move an entry to a new position in custom order.

        Returns True if successful, False if entry not found.
        """
        async with self._lock:
            data = await self._read_portfolio()

            # Check if entry exists
            entry_ids = [e.get("id") for e in data["entries"]]
            if entry_id not in entry_ids:
                return False

            # Build new order
            current_order = data.get("custom_order", [])

            # Remove entry from current position
            if entry_id in current_order:
                current_order.remove(entry_id)
            else:
                # If not in order yet, build order from entries
                current_order = [eid for eid in entry_ids if eid != entry_id]

            # Insert at new position
            new_position = max(0, min(new_position, len(current_order)))
            current_order.insert(new_position, entry_id)

            data["custom_order"] = current_order
            await self._write_portfolio(data)
            return True

    # ─── Categories ───────────────────────────────────────────────────────────

    async def get_categories(self) -> List[str]:
        """Get list of categories."""
        async with self._lock:
            data = await self._read_portfolio()
            return data["categories"]

    async def add_category(self, category: str) -> None:
        """Add a new category."""
        async with self._lock:
            data = await self._read_portfolio()
            if category not in data["categories"]:
                data["categories"].append(category)
                await self._write_portfolio(data)

    async def remove_category(self, category: str) -> bool:
        """
        Remove a category.

        Returns True if removed, False if not found.
        """
        async with self._lock:
            data = await self._read_portfolio()
            if category in data["categories"]:
                data["categories"].remove(category)
                await self._write_portfolio(data)
                return True
            return False

    # ─── Settings ─────────────────────────────────────────────────────────────

    async def get_default_privacy(self) -> str:
        """Get default privacy setting."""
        async with self._lock:
            data = await self._read_portfolio()
            return data["default_privacy"]

    async def set_default_privacy(self, privacy: str) -> None:
        """Set default privacy setting."""
        async with self._lock:
            data = await self._read_portfolio()
            data["default_privacy"] = privacy
            await self._write_portfolio(data)

    # ─── Statistics ───────────────────────────────────────────────────────────

    async def get_total_views(self) -> int:
        """Get total views across all entries."""
        async with self._lock:
            data = await self._read_portfolio()
            return sum(e.get("views", 0) for e in data["entries"])

    async def get_entry_count(self) -> int:
        """Get total number of entries."""
        async with self._lock:
            data = await self._read_portfolio()
            return len(data["entries"])

    # ─── Rates ────────────────────────────────────────────────────────────────

    async def get_rates(self) -> Dict[str, Any]:
        """Get all commission rates."""
        async with self._lock:
            data = await self._read_portfolio()
            return data.get("rates", {})

    async def set_rate(self, name: str, price: float, description: str = "", image: str = None) -> None:
        """Set a commission rate."""
        async with self._lock:
            data = await self._read_portfolio()
            existing = data["rates"].get(name, {})
            data["rates"][name] = {
                "price": price,
                "description": description,
                "image": image if image is not None else existing.get("image"),
            }
            await self._write_portfolio(data)

    async def set_rate_image(self, name: str, image: str) -> bool:
        """Set image for a specific rate."""
        async with self._lock:
            data = await self._read_portfolio()
            if name not in data["rates"]:
                return False
            if isinstance(data["rates"][name], dict):
                data["rates"][name]["image"] = image
            else:
                # Convert old format
                data["rates"][name] = {
                    "price": data["rates"][name],
                    "description": "",
                    "image": image,
                }
            await self._write_portfolio(data)
            return True

    async def remove_rate_image(self, name: str) -> bool:
        """Remove image from a specific rate."""
        async with self._lock:
            data = await self._read_portfolio()
            if name not in data["rates"]:
                return False
            if isinstance(data["rates"][name], dict):
                data["rates"][name]["image"] = None
                await self._write_portfolio(data)
            return True

    async def remove_rate(self, name: str) -> bool:
        """Remove a commission rate."""
        async with self._lock:
            data = await self._read_portfolio()
            if name in data["rates"]:
                del data["rates"][name]
                await self._write_portfolio(data)
                return True
            return False

    async def get_rate_card_settings(self) -> Dict[str, Any]:
        """Get rate card display settings."""
        async with self._lock:
            data = await self._read_portfolio()
            return data.get("rate_card_settings", {
                "title": "Commission Rates",
                "subtitle": "Quality digital artwork tailored to your vision",
                "status": "open",
                "currency": "$",
            })

    async def update_rate_card_settings(self, settings: Dict[str, Any]) -> None:
        """Update rate card display settings."""
        async with self._lock:
            data = await self._read_portfolio()
            current = data.get("rate_card_settings", {})
            current.update(settings)
            data["rate_card_settings"] = current
            await self._write_portfolio(data)
