"""
Portfolio service - business logic for portfolio management.

Handles portfolio entry creation, categorization, privacy, and before/after comparisons.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from core.portfolio_storage import PortfolioStore
from core.types import PortfolioEntry
from core.utils import utcnow, dt_to_iso


class PortfolioService:
    """Business logic for portfolio management."""

    def __init__(self) -> None:
        self._stores: Dict[int, PortfolioStore] = {}

    def _get_store(self, user_id: int) -> PortfolioStore:
        """Get or create a portfolio store for a user."""
        if user_id not in self._stores:
            self._stores[user_id] = PortfolioStore(user_id)
        return self._stores[user_id]

    async def initialize_store(self, user_id: int) -> None:
        """Initialize storage for a user."""
        store = self._get_store(user_id)
        await store.initialize()

    # ─── Entry Management ─────────────────────────────────────────────────────

    async def add_entry(
        self,
        user_id: int,
        url: str,
        title: str,
        category: str = "general",
        tags: Optional[List[str]] = None,
    ) -> PortfolioEntry:
        """
        Add a new portfolio entry.

        Args:
            user_id: User ID
            url: Image URL
            title: Entry title
            category: Entry category
            tags: Optional tags

        Returns:
            Created PortfolioEntry
        """
        store = self._get_store(user_id)
        await store.initialize()

        # Get default privacy
        default_privacy = await store.get_default_privacy()

        entry = PortfolioEntry(
            id=str(uuid.uuid4()),
            user_id=user_id,
            image_url=url,
            title=title,
            category=category,
            tags=tags or [],
            privacy=default_privacy,
            created_at=dt_to_iso(utcnow()),
        )

        await store.add_entry(entry)
        return entry

    async def remove_entry(self, user_id: int, entry_id: str) -> bool:
        """Remove an entry."""
        store = self._get_store(user_id)
        return await store.remove_entry(entry_id)

    async def update_entry(
        self,
        user_id: int,
        entry_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """Update entry fields."""
        store = self._get_store(user_id)
        return await store.update_entry(entry_id, updates)

    async def get_entry(self, user_id: int, entry_id: str) -> Optional[PortfolioEntry]:
        """Get a specific entry."""
        store = self._get_store(user_id)
        return await store.get_entry(entry_id)

    async def get_portfolio(
        self,
        user_id: int,
        viewer_id: Optional[int] = None,
    ) -> List[PortfolioEntry]:
        """
        Get all portfolio entries for a user.

        Args:
            user_id: Portfolio owner
            viewer_id: ID of viewer (for privacy filtering)

        Returns:
            List of entries
        """
        store = self._get_store(user_id)
        return await store.get_all_entries(viewer_id)

    async def get_portfolio_by_category(
        self,
        user_id: int,
        category: str,
        viewer_id: Optional[int] = None,
    ) -> List[PortfolioEntry]:
        """Get portfolio entries filtered by category."""
        store = self._get_store(user_id)
        return await store.get_entries_by_category(category, viewer_id)

    # ─── Featured Entry ───────────────────────────────────────────────────────

    async def set_featured(self, user_id: int, entry_id: str) -> bool:
        """
        Set an entry as featured.

        Only one entry can be featured at a time.
        """
        store = self._get_store(user_id)
        return await store.set_featured(entry_id)

    async def get_featured_entry(self, user_id: int) -> Optional[PortfolioEntry]:
        """Get the featured entry."""
        store = self._get_store(user_id)
        entries = await store.get_all_entries()
        for entry in entries:
            if entry.featured:
                return entry
        return None

    # ─── Categories ───────────────────────────────────────────────────────────

    async def get_categories(self, user_id: int) -> List[str]:
        """Get list of categories."""
        store = self._get_store(user_id)
        return await store.get_categories()

    async def add_category(self, user_id: int, category: str) -> None:
        """Add a new category."""
        store = self._get_store(user_id)
        await store.add_category(category)

    async def remove_category(self, user_id: int, category: str) -> bool:
        """Remove a category."""
        store = self._get_store(user_id)
        return await store.remove_category(category)

    # ─── Privacy ──────────────────────────────────────────────────────────────

    async def set_entry_privacy(
        self,
        user_id: int,
        entry_id: str,
        privacy: str,
    ) -> bool:
        """
        Set privacy level for an entry.

        Privacy levels: public, federation, private
        """
        if privacy not in ["public", "federation", "private"]:
            return False

        store = self._get_store(user_id)
        return await store.update_entry(entry_id, {"privacy": privacy})

    async def set_default_privacy(self, user_id: int, privacy: str) -> None:
        """Set default privacy for new entries."""
        if privacy not in ["public", "federation", "private"]:
            privacy = "public"

        store = self._get_store(user_id)
        await store.set_default_privacy(privacy)

    # ─── Ordering ─────────────────────────────────────────────────────────────

    async def reorder(self, user_id: int, entry_id: str, new_position: int) -> bool:
        """Move entry to a new position."""
        store = self._get_store(user_id)
        return await store.reorder_entry(entry_id, new_position)

    async def set_custom_order(self, user_id: int, order: List[str]) -> None:
        """Set custom display order."""
        store = self._get_store(user_id)
        await store.set_custom_order(order)

    # ─── Before/After ─────────────────────────────────────────────────────────

    async def add_before_after(
        self,
        user_id: int,
        before_url: str,
        after_url: str,
        title: str,
        category: str = "general",
        tags: Optional[List[str]] = None,
    ) -> PortfolioEntry:
        """
        Add a before/after comparison entry.

        Args:
            user_id: User ID
            before_url: URL of "before" image
            after_url: URL of "after" image
            title: Entry title
            category: Entry category
            tags: Optional tags

        Returns:
            Created PortfolioEntry
        """
        store = self._get_store(user_id)
        await store.initialize()

        default_privacy = await store.get_default_privacy()

        entry = PortfolioEntry(
            id=str(uuid.uuid4()),
            user_id=user_id,
            image_url=after_url,  # Primary image is "after"
            title=title,
            category=category,
            tags=tags or [],
            privacy=default_privacy,
            before_after={"before": before_url, "after": after_url},
            created_at=dt_to_iso(utcnow()),
        )

        await store.add_entry(entry)
        return entry

    # ─── Views ────────────────────────────────────────────────────────────────

    async def increment_views(self, user_id: int, entry_id: str) -> bool:
        """Increment view count for an entry."""
        store = self._get_store(user_id)
        return await store.increment_views(entry_id)

    async def get_total_views(self, user_id: int) -> int:
        """Get total views across all entries."""
        store = self._get_store(user_id)
        return await store.get_total_views()

    # ─── Batch Operations ─────────────────────────────────────────────────────

    async def batch_add(
        self,
        user_id: int,
        urls: List[str],
        category: str = "general",
        tags: Optional[List[str]] = None,
    ) -> List[PortfolioEntry]:
        """
        Add multiple entries at once.

        Args:
            user_id: User ID
            urls: List of image URLs
            category: Category for all entries
            tags: Tags for all entries

        Returns:
            List of created entries
        """
        entries = []
        for i, url in enumerate(urls):
            title = f"Entry {i + 1}"
            entry = await self.add_entry(user_id, url, title, category, tags)
            entries.append(entry)
        return entries

    # ─── Search ───────────────────────────────────────────────────────────────

    async def search_by_tag(
        self,
        user_id: int,
        tag: str,
        viewer_id: Optional[int] = None,
    ) -> List[PortfolioEntry]:
        """Search entries by tag."""
        store = self._get_store(user_id)
        entries = await store.get_all_entries(viewer_id)
        return [e for e in entries if tag.lower() in [t.lower() for t in e.tags]]

    # ─── Statistics ───────────────────────────────────────────────────────────

    async def get_stats(self, user_id: int) -> Dict[str, Any]:
        """Get portfolio statistics."""
        store = self._get_store(user_id)

        entries = await store.get_all_entries()
        total_views = await store.get_total_views()

        # Count by category
        category_counts = {}
        for entry in entries:
            category_counts[entry.category] = category_counts.get(entry.category, 0) + 1

        return {
            "total_entries": len(entries),
            "total_views": total_views,
            "categories": category_counts,
            "featured_count": sum(1 for e in entries if e.featured),
        }

    # ─── Federation Sync ──────────────────────────────────────────────────────

    async def sync_to_federation(self, user_id: int) -> List[PortfolioEntry]:
        """
        Get entries suitable for federation sync.

        Returns entries with privacy set to "public" or "federation".
        """
        store = self._get_store(user_id)
        entries = await store.get_all_entries()
        return [e for e in entries if e.privacy in ["public", "federation"]]

    # ─── Rate Card ────────────────────────────────────────────────────────────

    async def get_rates(self, user_id: int) -> Dict[str, Any]:
        """Get all commission rates for a user."""
        store = self._get_store(user_id)
        await store.initialize()
        return await store.get_rates()

    async def set_rate(
        self,
        user_id: int,
        name: str,
        price: float,
        description: str = "",
    ) -> None:
        """Set a commission rate."""
        store = self._get_store(user_id)
        await store.initialize()
        await store.set_rate(name, price, description)

    async def remove_rate(self, user_id: int, name: str) -> bool:
        """Remove a commission rate."""
        store = self._get_store(user_id)
        return await store.remove_rate(name)

    async def get_rate_card_settings(self, user_id: int) -> Dict[str, Any]:
        """Get rate card display settings."""
        store = self._get_store(user_id)
        await store.initialize()
        return await store.get_rate_card_settings()

    async def update_rate_card_settings(
        self,
        user_id: int,
        settings: Dict[str, Any],
    ) -> None:
        """Update rate card display settings."""
        store = self._get_store(user_id)
        await store.initialize()
        await store.update_rate_card_settings(settings)


# Global service instance
portfolio_service = PortfolioService()
