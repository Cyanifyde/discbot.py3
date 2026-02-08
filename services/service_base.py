"""
Base service class for all services that manage storage.

Provides common functionality for store factory pattern and caching.
"""
from __future__ import annotations

from typing import Dict, Generic, Type, TypeVar

# TypeVar for the storage class
T = TypeVar('T')


class ServiceBase(Generic[T]):
    """
    Base class for services with store factory/caching pattern.

    This class eliminates duplicate `_get_store()` methods across services
    by providing a generic, type-safe store factory pattern.

    Usage:
        from core.report_storage import ReportStore
        from services.service_base import ServiceBase

        class ReportService(ServiceBase[ReportStore]):
            def __init__(self) -> None:
                super().__init__(ReportStore)

            async def create_report(self, guild_id: int, ...):
                store = self._get_store(guild_id)
                await store.initialize()
                # ... use store ...

    The service will automatically cache store instances per guild.
    """

    __slots__ = ("_store_class", "_stores")

    def __init__(self, store_class: Type[T]) -> None:
        """
        Initialize the service with a storage class.

        Args:
            store_class: The storage class to instantiate (e.g., ReportStore)
        """
        self._store_class = store_class
        self._stores: Dict[int, T] = {}

    def _get_store(self, guild_id: int) -> T:
        """
        Get or create a store instance for a guild.

        This method caches store instances so that each guild always
        gets the same store object across multiple calls.

        Args:
            guild_id: Discord guild ID

        Returns:
            Store instance for the guild
        """
        if guild_id not in self._stores:
            self._stores[guild_id] = self._store_class(guild_id)
        return self._stores[guild_id]

    async def initialize_store(self, guild_id: int) -> None:
        """
        Initialize storage for a guild.

        This ensures the storage directory exists and any setup is complete.

        Args:
            guild_id: Discord guild ID
        """
        store = self._get_store(guild_id)
        await store.initialize()

    def _clear_cache(self, guild_id: int = None) -> None:
        """
        Clear cached store instances.

        Args:
            guild_id: If provided, clear only this guild's store.
                     If None, clear all stores.
        """
        if guild_id is None:
            self._stores.clear()
        else:
            self._stores.pop(guild_id, None)
