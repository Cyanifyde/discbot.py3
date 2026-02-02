"""
Trust storage - persistent storage for trust scores, vouches, and trust events.

Provides per-guild storage for trust system data with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso
from .types import TrustScore, Vouch

# Storage directory
TRUST_DIR = BASE_DIR / "data" / "trust"


class TrustStore:
    """Per-guild storage for trust system data."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = TRUST_DIR / str(guild_id)
        self.scores_path = self.root / "trust_scores.json"
        self.vouches_path = self.root / "vouches.json"
        self.events_path = self.root / "events.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Trust Scores ─────────────────────────────────────────────────────────

    async def _read_scores(self) -> Dict[str, Dict[str, Any]]:
        """Read trust scores file."""
        data = await read_json(self.scores_path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_scores(self, data: Dict[str, Dict[str, Any]]) -> None:
        """Write trust scores file."""
        await write_json_atomic(self.scores_path, data)

    async def get_score(self, user_id: int) -> Optional[TrustScore]:
        """Get trust score for a user."""
        async with self._lock:
            data = await self._read_scores()
            score_data = data.get(str(user_id))
            if not score_data:
                return None
            return TrustScore.from_dict(score_data)

    async def save_score(self, score: TrustScore) -> None:
        """Save trust score for a user."""
        async with self._lock:
            data = await self._read_scores()
            data[str(score.user_id)] = score.to_dict()
            await self._write_scores(data)

    async def get_all_scores(self) -> List[TrustScore]:
        """Get all trust scores in the guild."""
        async with self._lock:
            data = await self._read_scores()
            return [TrustScore.from_dict(score_data) for score_data in data.values()]

    # ─── Vouches ──────────────────────────────────────────────────────────────

    async def _read_vouches(self) -> Dict[str, Any]:
        """Read vouches file."""
        data = await read_json(self.vouches_path, default={"vouches": {}, "cooldowns": {}})
        if not isinstance(data, dict):
            return {"vouches": {}, "cooldowns": {}}
        if "vouches" not in data:
            data["vouches"] = {}
        if "cooldowns" not in data:
            data["cooldowns"] = {}
        return data

    async def _write_vouches(self, data: Dict[str, Any]) -> None:
        """Write vouches file."""
        await write_json_atomic(self.vouches_path, data)

    async def add_vouch(self, vouch: Vouch) -> None:
        """Add a vouch."""
        async with self._lock:
            data = await self._read_vouches()
            data["vouches"][vouch.id] = vouch.to_dict()
            await self._write_vouches(data)

    async def get_vouch(self, vouch_id: str) -> Optional[Vouch]:
        """Get a specific vouch by ID."""
        async with self._lock:
            data = await self._read_vouches()
            vouch_data = data["vouches"].get(vouch_id)
            if not vouch_data:
                return None
            return Vouch.from_dict(vouch_data)

    async def get_vouches_for(self, user_id: int) -> List[Vouch]:
        """Get all vouches received by a user."""
        async with self._lock:
            data = await self._read_vouches()
            vouches = []
            for vouch_data in data["vouches"].values():
                if vouch_data.get("to_user_id") == user_id:
                    vouches.append(Vouch.from_dict(vouch_data))
            return vouches

    async def get_vouches_given(self, user_id: int) -> List[Vouch]:
        """Get all vouches given by a user."""
        async with self._lock:
            data = await self._read_vouches()
            vouches = []
            for vouch_data in data["vouches"].values():
                if vouch_data.get("from_user_id") == user_id:
                    vouches.append(Vouch.from_dict(vouch_data))
            return vouches

    async def get_mutual_vouches(self, user_id: int) -> List[Vouch]:
        """Get all mutual vouches for a user."""
        async with self._lock:
            data = await self._read_vouches()
            vouches = []
            for vouch_data in data["vouches"].values():
                if vouch_data.get("to_user_id") == user_id and vouch_data.get("mutual"):
                    vouches.append(Vouch.from_dict(vouch_data))
            return vouches

    async def remove_vouch(self, vouch_id: str) -> bool:
        """Remove a vouch. Returns True if removed, False if not found."""
        async with self._lock:
            data = await self._read_vouches()
            if vouch_id in data["vouches"]:
                del data["vouches"][vouch_id]
                await self._write_vouches(data)
                return True
            return False

    async def update_vouch(self, vouch_id: str, updates: Dict[str, Any]) -> bool:
        """Update a vouch. Returns True if updated, False if not found."""
        async with self._lock:
            data = await self._read_vouches()
            if vouch_id not in data["vouches"]:
                return False
            data["vouches"][vouch_id].update(updates)
            await self._write_vouches(data)
            return True

    async def check_vouch_cooldown(self, from_user_id: int, to_user_id: int) -> Optional[str]:
        """
        Check if a vouch cooldown is active.
        Returns the cooldown expiry timestamp if active, None if not.
        """
        async with self._lock:
            data = await self._read_vouches()
            cooldown_key = f"{from_user_id}_{to_user_id}"
            return data["cooldowns"].get(cooldown_key)

    async def set_vouch_cooldown(self, from_user_id: int, to_user_id: int, expires_at: str) -> None:
        """Set a vouch cooldown."""
        async with self._lock:
            data = await self._read_vouches()
            cooldown_key = f"{from_user_id}_{to_user_id}"
            data["cooldowns"][cooldown_key] = expires_at
            await self._write_vouches(data)

    # ─── Trust Events ─────────────────────────────────────────────────────────

    async def _read_events(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read trust events file."""
        data = await read_json(self.events_path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_events(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Write trust events file."""
        await write_json_atomic(self.events_path, data)

    async def add_event(
        self,
        user_id: int,
        event_type: str,
        weight: float,
        positive: bool,
        details: Optional[str] = None,
    ) -> None:
        """Add a trust event (positive or negative)."""
        async with self._lock:
            data = await self._read_events()
            user_key = str(user_id)

            if user_key not in data:
                data[user_key] = []

            event = {
                "event_type": event_type,
                "weight": weight,
                "positive": positive,
                "details": details,
                "timestamp": dt_to_iso(utcnow()),
            }

            data[user_key].append(event)
            await self._write_events(data)

    async def get_events(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all trust events for a user."""
        async with self._lock:
            data = await self._read_events()
            return data.get(str(user_id), [])

    async def clear_old_events(self, user_id: int, keep_recent: int = 100) -> None:
        """Clear old trust events, keeping only the most recent N."""
        async with self._lock:
            data = await self._read_events()
            user_key = str(user_id)

            if user_key not in data:
                return

            events = data[user_key]
            if len(events) > keep_recent:
                # Sort by timestamp descending and keep most recent
                events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
                data[user_key] = events[:keep_recent]
                await self._write_events(data)
