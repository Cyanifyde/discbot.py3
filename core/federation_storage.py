"""
Federation storage - persistent storage for federation data, members, and sync.

Provides storage for multi-server federations with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso
from .types import FederationMember

# Storage directory
FEDERATION_DIR = BASE_DIR / "data" / "federation"


class FederationStore:
    """Storage for federation data."""

    def __init__(self) -> None:
        self.root = FEDERATION_DIR
        self.federations_path = self.root / "federations.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Federations ──────────────────────────────────────────────────────────

    async def _read_federations(self) -> Dict[str, Any]:
        """Read federations file."""
        default = {"federations": {}}
        data = await read_json(self.federations_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_federations(self, data: Dict[str, Any]) -> None:
        """Write federations file."""
        await write_json_atomic(self.federations_path, data)

    async def create_federation(
        self,
        federation_id: str,
        name: str,
        parent_guild_id: int,
    ) -> Dict[str, Any]:
        """Create a new federation."""
        async with self._lock:
            data = await self._read_federations()

            federation = {
                "id": federation_id,
                "name": name,
                "parent_guild_id": parent_guild_id,
                "created_at": dt_to_iso(utcnow()),
                "settings": {
                    "voting_threshold": 0.6,
                    "min_reputation_to_join": 50,
                    "tiers": {
                        "observer": {"sync_receive": True, "sync_send": False, "vote": False, "admin": False},
                        "member": {"sync_receive": True, "sync_send": True, "vote": False, "admin": False},
                        "trusted": {"sync_receive": True, "sync_send": True, "vote": True, "admin": False},
                        "core": {"sync_receive": True, "sync_send": True, "vote": True, "admin": True},
                    },
                },
            }

            data["federations"][federation_id] = federation
            await self._write_federations(data)

            # Create federation subdirectories
            fed_root = self.root / federation_id
            await asyncio.to_thread(fed_root.mkdir, parents=True, exist_ok=True)

            return federation

    async def get_federation(self, federation_id: str) -> Optional[Dict[str, Any]]:
        """Get a federation by ID."""
        async with self._lock:
            data = await self._read_federations()
            return data["federations"].get(federation_id)

    async def get_all_federations(self) -> List[Dict[str, Any]]:
        """Get all federations."""
        async with self._lock:
            data = await self._read_federations()
            return list(data["federations"].values())

    async def update_federation(
        self,
        federation_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """Update federation settings."""
        async with self._lock:
            data = await self._read_federations()

            if federation_id not in data["federations"]:
                return False

            data["federations"][federation_id].update(updates)
            await self._write_federations(data)
            return True

    async def delete_federation(self, federation_id: str) -> bool:
        """Delete a federation."""
        async with self._lock:
            data = await self._read_federations()

            if federation_id not in data["federations"]:
                return False

            del data["federations"][federation_id]
            await self._write_federations(data)
            return True

    # ─── Members ──────────────────────────────────────────────────────────────

    async def _read_members(self, federation_id: str) -> Dict[str, Any]:
        """Read members file for a federation."""
        members_path = self.root / federation_id / "members.json"
        default = {"members": {}, "invite_keys": {}}
        data = await read_json(members_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_members(self, federation_id: str, data: Dict[str, Any]) -> None:
        """Write members file."""
        members_path = self.root / federation_id / "members.json"
        await write_json_atomic(members_path, data)

    async def add_member(
        self,
        federation_id: str,
        member: FederationMember,
    ) -> None:
        """Add a member to a federation."""
        async with self._lock:
            data = await self._read_members(federation_id)
            data["members"][str(member.guild_id)] = member.to_dict()
            await self._write_members(federation_id, data)

    async def get_member(
        self,
        federation_id: str,
        guild_id: int,
    ) -> Optional[FederationMember]:
        """Get a specific member."""
        async with self._lock:
            data = await self._read_members(federation_id)
            member_data = data["members"].get(str(guild_id))
            if not member_data:
                return None
            return FederationMember.from_dict(member_data)

    async def get_all_members(self, federation_id: str) -> List[FederationMember]:
        """Get all members of a federation."""
        async with self._lock:
            data = await self._read_members(federation_id)
            return [FederationMember.from_dict(m) for m in data["members"].values()]

    async def update_member(
        self,
        federation_id: str,
        guild_id: int,
        updates: Dict[str, Any],
    ) -> bool:
        """Update a member."""
        async with self._lock:
            data = await self._read_members(federation_id)
            guild_key = str(guild_id)

            if guild_key not in data["members"]:
                return False

            data["members"][guild_key].update(updates)
            await self._write_members(federation_id, data)
            return True

    async def remove_member(self, federation_id: str, guild_id: int) -> bool:
        """Remove a member from federation."""
        async with self._lock:
            data = await self._read_members(federation_id)
            guild_key = str(guild_id)

            if guild_key not in data["members"]:
                return False

            del data["members"][guild_key]
            await self._write_members(federation_id, data)
            return True

    # ─── Invite Keys ──────────────────────────────────────────────────────────

    async def create_invite_key(
        self,
        federation_id: str,
        key: str,
        tier: str = "member",
    ) -> Dict[str, Any]:
        """Create an invite key."""
        async with self._lock:
            data = await self._read_members(federation_id)

            invite = {
                "key": key,
                "tier": tier,
                "created_at": dt_to_iso(utcnow()),
                "used": False,
            }

            data["invite_keys"][key] = invite
            await self._write_members(federation_id, data)
            return invite

    async def validate_invite_key(
        self,
        federation_id: str,
        key: str,
    ) -> Optional[Dict[str, Any]]:
        """Validate an invite key."""
        async with self._lock:
            data = await self._read_members(federation_id)
            invite = data["invite_keys"].get(key)

            if not invite or invite.get("used"):
                return None

            return invite

    async def mark_invite_used(self, federation_id: str, key: str) -> bool:
        """Mark an invite key as used."""
        async with self._lock:
            data = await self._read_members(federation_id)

            if key not in data["invite_keys"]:
                return False

            data["invite_keys"][key]["used"] = True
            await self._write_members(federation_id, data)
            return True

    # ─── Blocklist ────────────────────────────────────────────────────────────

    async def _read_blocklist(self, federation_id: str) -> Dict[str, Any]:
        """Read blocklist file."""
        blocklist_path = self.root / federation_id / "blocklist.json"
        default = {"entries": []}
        data = await read_json(blocklist_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_blocklist(self, federation_id: str, data: Dict[str, Any]) -> None:
        """Write blocklist file."""
        blocklist_path = self.root / federation_id / "blocklist.json"
        await write_json_atomic(blocklist_path, data)

    async def add_to_blocklist(
        self,
        federation_id: str,
        user_id: int,
        reason: str,
        evidence: str,
        reported_by_guild: int,
    ) -> Dict[str, Any]:
        """Add user to federation blocklist."""
        async with self._lock:
            data = await self._read_blocklist(federation_id)

            entry = {
                "user_id": user_id,
                "reason": reason,
                "evidence": evidence,
                "reported_by_guild": reported_by_guild,
                "added_at": dt_to_iso(utcnow()),
                "confirmations": [reported_by_guild],
            }

            data["entries"].append(entry)
            await self._write_blocklist(federation_id, data)
            return entry

    async def check_blocklist(
        self,
        federation_id: str,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Check if user is on blocklist."""
        async with self._lock:
            data = await self._read_blocklist(federation_id)

            for entry in data["entries"]:
                if entry.get("user_id") == user_id:
                    return entry

            return None

    async def remove_from_blocklist(
        self,
        federation_id: str,
        user_id: int,
    ) -> bool:
        """Remove user from blocklist."""
        async with self._lock:
            data = await self._read_blocklist(federation_id)
            original_len = len(data["entries"])

            data["entries"] = [
                e for e in data["entries"]
                if e.get("user_id") != user_id
            ]

            if len(data["entries"]) < original_len:
                await self._write_blocklist(federation_id, data)
                return True

            return False

    async def add_blocklist_confirmation(
        self,
        federation_id: str,
        user_id: int,
        guild_id: int,
    ) -> bool:
        """Add confirmation from another guild for a blocklist entry."""
        async with self._lock:
            data = await self._read_blocklist(federation_id)

            for entry in data["entries"]:
                if entry.get("user_id") == user_id:
                    if guild_id not in entry["confirmations"]:
                        entry["confirmations"].append(guild_id)
                        await self._write_blocklist(federation_id, data)
                    return True

            return False

    # ─── Directory ────────────────────────────────────────────────────────────

    async def _read_directory(self, federation_id: str) -> Dict[str, Any]:
        """Read directory file."""
        directory_path = self.root / federation_id / "directory.json"
        default = {"artists": {}}
        data = await read_json(directory_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_directory(self, federation_id: str, data: Dict[str, Any]) -> None:
        """Write directory file."""
        directory_path = self.root / federation_id / "directory.json"
        await write_json_atomic(directory_path, data)

    async def add_to_directory(
        self,
        federation_id: str,
        user_id: int,
        profile_data: Dict[str, Any],
    ) -> None:
        """Add artist to directory."""
        async with self._lock:
            data = await self._read_directory(federation_id)
            data["artists"][str(user_id)] = {
                **profile_data,
                "updated_at": dt_to_iso(utcnow()),
            }
            await self._write_directory(federation_id, data)

    async def remove_from_directory(self, federation_id: str, user_id: int) -> bool:
        """Remove artist from directory."""
        async with self._lock:
            data = await self._read_directory(federation_id)
            user_key = str(user_id)

            if user_key in data["artists"]:
                del data["artists"][user_key]
                await self._write_directory(federation_id, data)
                return True

            return False

    async def search_directory(
        self,
        federation_id: str,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search directory by tags."""
        async with self._lock:
            data = await self._read_directory(federation_id)
            artists = list(data["artists"].values())

            if tags:
                # Filter by tags
                filtered = []
                for artist in artists:
                    artist_tags = artist.get("tags", [])
                    if any(tag.lower() in [t.lower() for t in artist_tags] for tag in tags):
                        filtered.append(artist)
                return filtered

            return artists

    # ─── Votes ────────────────────────────────────────────────────────────────

    async def _read_votes(self, federation_id: str) -> Dict[str, Any]:
        """Read votes file."""
        votes_path = self.root / federation_id / "votes.json"
        default = {"votes": {}}
        data = await read_json(votes_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_votes(self, federation_id: str, data: Dict[str, Any]) -> None:
        """Write votes file."""
        votes_path = self.root / federation_id / "votes.json"
        await write_json_atomic(votes_path, data)

    async def create_vote(
        self,
        federation_id: str,
        vote_id: str,
        topic: str,
        options: List[str],
        duration_hours: int = 72,
    ) -> Dict[str, Any]:
        """Create a federation vote."""
        from datetime import timedelta

        async with self._lock:
            data = await self._read_votes(federation_id)

            expires_at = utcnow() + timedelta(hours=duration_hours)

            vote = {
                "id": vote_id,
                "topic": topic,
                "options": options,
                "votes": {},  # guild_id -> option
                "created_at": dt_to_iso(utcnow()),
                "expires_at": dt_to_iso(expires_at),
                "closed": False,
            }

            data["votes"][vote_id] = vote
            await self._write_votes(federation_id, data)
            return vote

    async def cast_vote(
        self,
        federation_id: str,
        vote_id: str,
        guild_id: int,
        option: str,
    ) -> bool:
        """Cast a vote."""
        async with self._lock:
            data = await self._read_votes(federation_id)

            if vote_id not in data["votes"]:
                return False

            vote = data["votes"][vote_id]
            if vote.get("closed"):
                return False

            vote["votes"][str(guild_id)] = option
            await self._write_votes(federation_id, data)
            return True

    async def get_vote(self, federation_id: str, vote_id: str) -> Optional[Dict[str, Any]]:
        """Get vote details."""
        async with self._lock:
            data = await self._read_votes(federation_id)
            return data["votes"].get(vote_id)

    async def close_vote(self, federation_id: str, vote_id: str) -> bool:
        """Close a vote."""
        async with self._lock:
            data = await self._read_votes(federation_id)

            if vote_id not in data["votes"]:
                return False

            data["votes"][vote_id]["closed"] = True
            await self._write_votes(federation_id, data)
            return True

    # ─── Audit Log ────────────────────────────────────────────────────────────

    async def _read_audit(self, federation_id: str) -> Dict[str, Any]:
        """Read audit log file."""
        audit_path = self.root / federation_id / "audit.json"
        default = {"entries": []}
        data = await read_json(audit_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_audit(self, federation_id: str, data: Dict[str, Any]) -> None:
        """Write audit log file."""
        audit_path = self.root / federation_id / "audit.json"
        await write_json_atomic(audit_path, data)

    async def add_audit_entry(
        self,
        federation_id: str,
        action: str,
        actor_guild_id: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add an audit log entry."""
        async with self._lock:
            data = await self._read_audit(federation_id)

            entry = {
                "action": action,
                "actor_guild_id": actor_guild_id,
                "timestamp": dt_to_iso(utcnow()),
                "details": details or {},
            }

            data["entries"].append(entry)

            # Keep only last 1000 entries
            if len(data["entries"]) > 1000:
                data["entries"] = data["entries"][-1000:]

            await self._write_audit(federation_id, data)
            return entry

    async def get_audit_log(
        self,
        federation_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get recent audit log entries."""
        async with self._lock:
            data = await self._read_audit(federation_id)
            entries = data["entries"]
            # Most recent first
            entries.reverse()
            return entries[:limit]


# Global federation store instance
federation_store = FederationStore()
