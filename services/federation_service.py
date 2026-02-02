"""
Federation service - business logic for multi-server federations.

Handles federation management, blocklists, directory, voting, and cross-server sync.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from core.federation_storage import federation_store
from core.types import FederationMember
from core.utils import utcnow, dt_to_iso


class FederationService:
    """Business logic for federation management."""

    def __init__(self) -> None:
        pass

    async def initialize(self) -> None:
        """Initialize federation storage."""
        await federation_store.initialize()

    # ─── Federation Management ────────────────────────────────────────────────

    async def create_federation(
        self,
        name: str,
        parent_guild_id: int,
    ) -> str:
        """
        Create a new federation.

        Args:
            name: Federation name
            parent_guild_id: Parent guild ID

        Returns:
            Federation ID
        """
        federation_id = str(uuid.uuid4())
        await federation_store.create_federation(federation_id, name, parent_guild_id)

        # Add parent as core member
        parent_member = FederationMember(
            guild_id=parent_guild_id,
            guild_name="Parent Server",
            federation_id=federation_id,
            tier="core",
            joined_at=dt_to_iso(utcnow()),
            sync_receive=True,
            sync_send=True,
            vote_enabled=True,
            admin_enabled=True,
        )

        await federation_store.add_member(federation_id, parent_member)

        # Add audit entry
        await federation_store.add_audit_entry(
            federation_id,
            "federation_created",
            parent_guild_id,
            {"name": name},
        )

        return federation_id

    async def join_federation(
        self,
        guild_id: int,
        guild_name: str,
        federation_id: str,
        invite_key: str,
    ) -> bool:
        """
        Join a federation using an invite key.

        Returns True if successful, False otherwise.
        """
        # Validate invite
        invite = await federation_store.validate_invite_key(federation_id, invite_key)
        if not invite:
            return False

        # Create member
        member = FederationMember(
            guild_id=guild_id,
            guild_name=guild_name,
            federation_id=federation_id,
            tier=invite["tier"],
            joined_at=dt_to_iso(utcnow()),
        )

        # Apply tier permissions
        await self._apply_tier_permissions(member)

        # Add member
        await federation_store.add_member(federation_id, member)

        # Mark invite as used
        await federation_store.mark_invite_used(federation_id, invite_key)

        # Add audit entry
        await federation_store.add_audit_entry(
            federation_id,
            "member_joined",
            guild_id,
            {"tier": invite["tier"]},
        )

        return True

    async def leave_federation(
        self,
        guild_id: int,
        federation_id: str,
    ) -> bool:
        """Leave a federation."""
        success = await federation_store.remove_member(federation_id, guild_id)

        if success:
            await federation_store.add_audit_entry(
                federation_id,
                "member_left",
                guild_id,
            )

        return success

    async def get_federation(self, federation_id: str) -> Optional[Dict[str, Any]]:
        """Get federation details."""
        return await federation_store.get_federation(federation_id)

    async def get_all_federations(self) -> List[Dict[str, Any]]:
        """Get all federations."""
        return await federation_store.get_all_federations()

    # ─── Member Management ────────────────────────────────────────────────────

    async def get_member(
        self,
        federation_id: str,
        guild_id: int,
    ) -> Optional[FederationMember]:
        """Get a federation member."""
        return await federation_store.get_member(federation_id, guild_id)

    async def get_all_members(self, federation_id: str) -> List[FederationMember]:
        """Get all federation members."""
        return await federation_store.get_all_members(federation_id)

    async def get_member_tier(
        self,
        federation_id: str,
        guild_id: int,
    ) -> Optional[str]:
        """Get member tier."""
        member = await federation_store.get_member(federation_id, guild_id)
        return member.tier if member else None

    async def set_member_tier(
        self,
        federation_id: str,
        guild_id: int,
        tier: str,
        actor_guild_id: int,
    ) -> bool:
        """Set member tier (requires admin permissions)."""
        # Check if actor has admin permissions
        actor = await federation_store.get_member(federation_id, actor_guild_id)
        if not actor or not actor.admin_enabled:
            return False

        # Get target member
        member = await federation_store.get_member(federation_id, guild_id)
        if not member:
            return False

        # Update tier
        member.tier = tier
        await self._apply_tier_permissions(member)

        updates = member.to_dict()
        success = await federation_store.update_member(federation_id, guild_id, updates)

        if success:
            await federation_store.add_audit_entry(
                federation_id,
                "tier_changed",
                actor_guild_id,
                {"target_guild": guild_id, "new_tier": tier},
            )

        return success

    async def _apply_tier_permissions(self, member: FederationMember) -> None:
        """Apply permissions based on tier."""
        federation = await federation_store.get_federation(member.federation_id)
        if not federation:
            return

        tier_perms = federation["settings"]["tiers"].get(member.tier, {})

        member.sync_receive = tier_perms.get("sync_receive", False)
        member.sync_send = tier_perms.get("sync_send", False)
        member.vote_enabled = tier_perms.get("vote", False)
        member.admin_enabled = tier_perms.get("admin", False)

    # ─── Invite Management ────────────────────────────────────────────────────

    async def create_invite(
        self,
        federation_id: str,
        tier: str = "member",
    ) -> str:
        """Generate an invite key."""
        key = str(uuid.uuid4())[:12]  # Short key
        await federation_store.create_invite_key(federation_id, key, tier)
        return key

    # ─── Blocklist ────────────────────────────────────────────────────────────

    async def check_blocklist(
        self,
        federation_id: str,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Check if user is on federation blocklist."""
        return await federation_store.check_blocklist(federation_id, user_id)

    async def add_to_blocklist(
        self,
        federation_id: str,
        user_id: int,
        reason: str,
        evidence: str,
        guild_id: int,
    ) -> bool:
        """Add user to federation blocklist."""
        # Check if requester has send permissions
        member = await federation_store.get_member(federation_id, guild_id)
        if not member or not member.sync_send:
            return False

        await federation_store.add_to_blocklist(
            federation_id,
            user_id,
            reason,
            evidence,
            guild_id,
        )

        await federation_store.add_audit_entry(
            federation_id,
            "blocklist_add",
            guild_id,
            {"user_id": user_id, "reason": reason},
        )

        return True

    async def remove_from_blocklist(
        self,
        federation_id: str,
        user_id: int,
        guild_id: int,
    ) -> bool:
        """Remove user from blocklist (requires admin)."""
        # Check admin permissions
        member = await federation_store.get_member(federation_id, guild_id)
        if not member or not member.admin_enabled:
            return False

        success = await federation_store.remove_from_blocklist(federation_id, user_id)

        if success:
            await federation_store.add_audit_entry(
                federation_id,
                "blocklist_remove",
                guild_id,
                {"user_id": user_id},
            )

        return success

    async def confirm_blocklist_entry(
        self,
        federation_id: str,
        user_id: int,
        guild_id: int,
    ) -> bool:
        """Add confirmation to a blocklist entry."""
        # Check if requester has vote permissions
        member = await federation_store.get_member(federation_id, guild_id)
        if not member or not member.vote_enabled:
            return False

        return await federation_store.add_blocklist_confirmation(
            federation_id,
            user_id,
            guild_id,
        )

    # ─── Directory ────────────────────────────────────────────────────────────

    async def add_to_directory(
        self,
        federation_id: str,
        user_id: int,
        profile_data: Dict[str, Any],
    ) -> bool:
        """Add artist to federation directory."""
        await federation_store.add_to_directory(federation_id, user_id, profile_data)
        return True

    async def remove_from_directory(
        self,
        federation_id: str,
        user_id: int,
    ) -> bool:
        """Remove artist from directory."""
        return await federation_store.remove_from_directory(federation_id, user_id)

    async def search_directory(
        self,
        federation_id: str,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search federation directory."""
        return await federation_store.search_directory(federation_id, tags)

    # ─── Voting ───────────────────────────────────────────────────────────────

    async def start_vote(
        self,
        federation_id: str,
        topic: str,
        options: List[str],
        duration_hours: int,
        guild_id: int,
    ) -> Optional[str]:
        """Start a federation vote (requires admin)."""
        # Check admin permissions
        member = await federation_store.get_member(federation_id, guild_id)
        if not member or not member.admin_enabled:
            return None

        vote_id = str(uuid.uuid4())
        await federation_store.create_vote(
            federation_id,
            vote_id,
            topic,
            options,
            duration_hours,
        )

        await federation_store.add_audit_entry(
            federation_id,
            "vote_started",
            guild_id,
            {"topic": topic, "vote_id": vote_id},
        )

        return vote_id

    async def cast_vote(
        self,
        federation_id: str,
        vote_id: str,
        guild_id: int,
        option: str,
    ) -> bool:
        """Cast a vote."""
        # Check voting permissions
        member = await federation_store.get_member(federation_id, guild_id)
        if not member or not member.vote_enabled:
            return False

        success = await federation_store.cast_vote(federation_id, vote_id, guild_id, option)

        if success:
            await federation_store.add_audit_entry(
                federation_id,
                "vote_cast",
                guild_id,
                {"vote_id": vote_id, "option": option},
            )

        return success

    async def get_vote(
        self,
        federation_id: str,
        vote_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get vote details."""
        return await federation_store.get_vote(federation_id, vote_id)

    async def get_vote_results(
        self,
        federation_id: str,
        vote_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get vote results with tallies."""
        vote = await federation_store.get_vote(federation_id, vote_id)
        if not vote:
            return None

        # Tally votes
        tallies = {}
        for option in vote["options"]:
            tallies[option] = 0

        for option in vote["votes"].values():
            if option in tallies:
                tallies[option] += 1

        total_votes = len(vote["votes"])
        percentages = {}
        for option, count in tallies.items():
            percentages[option] = (count / total_votes * 100) if total_votes > 0 else 0

        return {
            **vote,
            "tallies": tallies,
            "percentages": percentages,
            "total_votes": total_votes,
        }

    # ─── Sync Permissions ─────────────────────────────────────────────────────

    async def check_sync_permission(
        self,
        federation_id: str,
        guild_id: int,
        action: str,
    ) -> bool:
        """
        Check if guild has permission for sync action.

        Actions: send, receive
        """
        member = await federation_store.get_member(federation_id, guild_id)
        if not member:
            return False

        if action == "send":
            return member.sync_send
        elif action == "receive":
            return member.sync_receive
        elif action == "vote":
            return member.vote_enabled
        elif action == "admin":
            return member.admin_enabled

        return False

    # ─── Propagation ──────────────────────────────────────────────────────────

    async def propagate_action(
        self,
        federation_id: str,
        action_type: str,
        data: Dict[str, Any],
        from_guild_id: int,
    ) -> List[int]:
        """
        Propagate an action to all receiving members.

        Returns list of guild IDs that should receive the action.
        """
        # Check if sender has send permission
        sender = await federation_store.get_member(federation_id, from_guild_id)
        if not sender or not sender.sync_send:
            return []

        # Get all members with receive permission
        all_members = await federation_store.get_all_members(federation_id)
        recipients = [
            m.guild_id
            for m in all_members
            if m.sync_receive and m.guild_id != from_guild_id
        ]

        # Log propagation
        await federation_store.add_audit_entry(
            federation_id,
            f"propagate_{action_type}",
            from_guild_id,
            {
                "action_type": action_type,
                "recipient_count": len(recipients),
            },
        )

        return recipients

    # ─── Statistics ───────────────────────────────────────────────────────────

    async def get_federation_stats(self, federation_id: str) -> Dict[str, Any]:
        """Get federation statistics."""
        members = await federation_store.get_all_members(federation_id)

        # Count by tier
        by_tier = {}
        for member in members:
            by_tier[member.tier] = by_tier.get(member.tier, 0) + 1

        # Get blocklist size
        blocklist_data = await federation_store._read_blocklist(federation_id)
        blocklist_size = len(blocklist_data.get("entries", []))

        # Get directory size
        directory_data = await federation_store._read_directory(federation_id)
        directory_size = len(directory_data.get("artists", {}))

        return {
            "total_members": len(members),
            "by_tier": by_tier,
            "blocklist_size": blocklist_size,
            "directory_size": directory_size,
        }

    # ─── Audit Log ────────────────────────────────────────────────────────────

    async def get_audit_log(
        self,
        federation_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get audit log entries."""
        return await federation_store.get_audit_log(federation_id, limit)

    async def search_audit_log(
        self,
        federation_id: str,
        query: str,
    ) -> List[Dict[str, Any]]:
        """Search audit log."""
        entries = await federation_store.get_audit_log(federation_id, limit=1000)

        query_lower = query.lower()
        results = []

        for entry in entries:
            if (
                query_lower in entry.get("action", "").lower()
                or query in str(entry.get("actor_guild_id", ""))
            ):
                results.append(entry)

        return results


# Global service instance
federation_service = FederationService()
