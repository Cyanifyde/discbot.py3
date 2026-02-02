"""
Commission review storage - per-guild reviews with dispute workflow.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import dt_to_iso, utcnow

REVIEWS_DIR = BASE_DIR / "data" / "commission_reviews"


class CommissionReviewStore:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = REVIEWS_DIR / str(guild_id)
        self.data_path = self.root / "reviews.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    async def _read(self) -> Dict[str, Any]:
        data = await read_json(self.data_path, default={"reviews": {}})
        if not isinstance(data, dict):
            return {"reviews": {}}
        if "reviews" not in data or not isinstance(data.get("reviews"), dict):
            data["reviews"] = {}
        return data

    async def _write(self, data: Dict[str, Any]) -> None:
        await write_json_atomic(self.data_path, data)

    async def create_review(
        self,
        artist_id: int,
        client_id: int,
        rating: int,
        text: str,
        commission_id: Optional[str] = None,
    ) -> str:
        async with self._lock:
            data = await self._read()
            rid = str(uuid.uuid4())
            data["reviews"][rid] = {
                "id": rid,
                "artist_id": artist_id,
                "client_id": client_id,
                "rating": rating,
                "text": text,
                "commission_id": commission_id,
                "created_at": dt_to_iso(utcnow()),
                "status": "active",  # active/disputed/removed/amended
                "dispute": None,     # {by, reason, at}
                "resolution": None,  # {by, outcome, note, at}
                "amended_text": None,
            }
            await self._write(data)
            return rid

    async def get_review(self, review_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            data = await self._read()
            review = data["reviews"].get(review_id)
            return review if isinstance(review, dict) else None

    async def list_reviews_for_artist(self, artist_id: int) -> List[Dict[str, Any]]:
        async with self._lock:
            data = await self._read()
            reviews = [
                r for r in data["reviews"].values()
                if isinstance(r, dict) and r.get("artist_id") == artist_id
            ]
            reviews.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            return reviews

    async def list_reviews_by_client(self, client_id: int) -> List[Dict[str, Any]]:
        async with self._lock:
            data = await self._read()
            reviews = [
                r for r in data["reviews"].values()
                if isinstance(r, dict) and r.get("client_id") == client_id
            ]
            reviews.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            return reviews

    async def dispute(self, review_id: str, actor_id: int, reason: str) -> bool:
        async with self._lock:
            data = await self._read()
            review = data["reviews"].get(review_id)
            if not isinstance(review, dict):
                return False
            if review.get("status") in {"removed"}:
                return False
            review["status"] = "disputed"
            review["dispute"] = {"by": actor_id, "reason": reason, "at": dt_to_iso(utcnow())}
            await self._write(data)
            return True

    async def resolve(
        self,
        review_id: str,
        moderator_id: int,
        outcome: str,
        note: Optional[str] = None,
        amended_text: Optional[str] = None,
    ) -> bool:
        """
        outcome: upheld|removed|amended
        """
        if outcome not in {"upheld", "removed", "amended"}:
            return False

        async with self._lock:
            data = await self._read()
            review = data["reviews"].get(review_id)
            if not isinstance(review, dict):
                return False

            if outcome == "upheld":
                review["status"] = "active"
            elif outcome == "removed":
                review["status"] = "removed"
            else:
                review["status"] = "amended"
                if amended_text:
                    review["amended_text"] = amended_text

            review["resolution"] = {
                "by": moderator_id,
                "outcome": outcome,
                "note": note,
                "at": dt_to_iso(utcnow()),
            }
            await self._write(data)
            return True

