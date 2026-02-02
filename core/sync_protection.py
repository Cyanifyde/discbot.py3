"""
Sync protection - burst detection and circuit breaker for cross-server syncing.

When a server suddenly does many moderation actions, syncing is paused and
affected servers must approve to resume. Protects against:
- Mistakes (accidental mass actions)
- Hackers (compromised admin accounts)
- Bad actors (malicious parent servers)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Literal

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso, iso_to_dt

# Storage directory
PROTECTION_DIR = BASE_DIR / "data" / "protection"

# Default thresholds (can be overridden per-guild)
DEFAULT_WINDOW_SECONDS = 300  # 5 minutes
DEFAULT_MAX_ACTIONS = 10  # Actions before triggering protection

# All action types that are tracked
TRACKED_ACTIONS = {"ban", "unban", "kick", "mute", "unmute", "warning"}

# Circuit breaker states
CircuitState = Literal["closed", "open", "pending_approval"]


@dataclass
class ActionRecord:
    """Record of an action for burst detection."""
    action_type: str
    user_id: int
    timestamp: str


@dataclass
class CircuitBreaker:
    """Circuit breaker state for a link between two guilds."""
    state: CircuitState = "closed"
    triggered_at: Optional[str] = None
    trigger_reason: Optional[str] = None
    approval_message_id: Optional[int] = None
    queued_actions: List[Dict[str, Any]] = field(default_factory=list)


class SyncProtection:
    """Handles burst detection and circuit breaker for sync."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._action_history: Dict[int, List[ActionRecord]] = {}
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}  # "parent_id:child_id" -> state

    async def initialize(self) -> None:
        """Ensure storage directory exists and load state."""
        await asyncio.to_thread(PROTECTION_DIR.mkdir, parents=True, exist_ok=True)
        await self._load_state()

    def _state_path(self) -> Path:
        return PROTECTION_DIR / "protection_state.json"

    def _circuit_key(self, from_guild: int, to_guild: int) -> str:
        """Create a unique key for a directional link."""
        return f"{from_guild}:{to_guild}"

    async def _load_state(self) -> None:
        """Load protection state from disk."""
        data = await read_json(self._state_path(), default={})

        if isinstance(data.get("action_history"), dict):
            for guild_id_str, actions in data["action_history"].items():
                try:
                    guild_id = int(guild_id_str)
                    self._action_history[guild_id] = [
                        ActionRecord(
                            action_type=a.get("action_type", ""),
                            user_id=a.get("user_id", 0),
                            timestamp=a.get("timestamp", ""),
                        )
                        for a in actions if isinstance(a, dict)
                    ]
                except (ValueError, TypeError):
                    pass

        if isinstance(data.get("circuit_breakers"), dict):
            for key, cb_data in data["circuit_breakers"].items():
                if isinstance(cb_data, dict):
                    self._circuit_breakers[key] = CircuitBreaker(
                        state=cb_data.get("state", "closed"),
                        triggered_at=cb_data.get("triggered_at"),
                        trigger_reason=cb_data.get("trigger_reason"),
                        approval_message_id=cb_data.get("approval_message_id"),
                        queued_actions=cb_data.get("queued_actions", []),
                    )

    async def _save_state(self) -> None:
        """Save protection state to disk."""
        data = {
            "action_history": {
                str(guild_id): [
                    {
                        "action_type": a.action_type,
                        "user_id": a.user_id,
                        "timestamp": a.timestamp,
                    }
                    for a in actions[-100:]  # Keep last 100 per guild
                ]
                for guild_id, actions in self._action_history.items()
            },
            "circuit_breakers": {
                key: {
                    "state": cb.state,
                    "triggered_at": cb.triggered_at,
                    "trigger_reason": cb.trigger_reason,
                    "approval_message_id": cb.approval_message_id,
                    "queued_actions": cb.queued_actions[-50:],  # Keep last 50 queued
                }
                for key, cb in self._circuit_breakers.items()
            },
        }
        await write_json_atomic(self._state_path(), data)

    async def get_guild_thresholds(self, guild_id: int) -> tuple[int, int]:
        """
        Get the protection thresholds for a guild.

        Returns (window_seconds, max_actions).
        """
        from .link_storage import get_link_storage
        storage = await get_link_storage()
        settings = await storage.get_protection_settings(guild_id)

        window_seconds = settings.get("window_seconds")
        max_actions = settings.get("max_actions")

        if not isinstance(window_seconds, int) or window_seconds <= 0:
            window_seconds = DEFAULT_WINDOW_SECONDS
        if not isinstance(max_actions, int) or max_actions <= 0:
            max_actions = DEFAULT_MAX_ACTIONS

        return window_seconds, max_actions

    def _cleanup_old_actions(self, guild_id: int, window_seconds: int) -> None:
        """Remove actions outside the detection window."""
        if guild_id not in self._action_history:
            return

        now = utcnow()
        cutoff = now - timedelta(seconds=window_seconds)

        self._action_history[guild_id] = [
            a for a in self._action_history[guild_id]
            if iso_to_dt(a.timestamp) and iso_to_dt(a.timestamp) > cutoff
        ]

    async def record_action(
        self,
        origin_guild_id: int,
        action_type: str,
        user_id: int,
    ) -> None:
        """Record an action for burst detection."""
        if action_type not in TRACKED_ACTIONS:
            return

        async with self._lock:
            if origin_guild_id not in self._action_history:
                self._action_history[origin_guild_id] = []

            self._action_history[origin_guild_id].append(ActionRecord(
                action_type=action_type,
                user_id=user_id,
                timestamp=dt_to_iso(utcnow()),
            ))

            await self._save_state()

    async def check_burst(self, origin_guild_id: int) -> tuple[bool, int, int]:
        """
        Check if a guild has exceeded burst threshold.

        Returns (is_burst, action_count, threshold).
        """
        async with self._lock:
            window_seconds, max_actions = await self.get_guild_thresholds(origin_guild_id)
            self._cleanup_old_actions(origin_guild_id, window_seconds)

            actions = self._action_history.get(origin_guild_id, [])
            count = len(actions)

            return count > max_actions, count, max_actions

    async def get_circuit_state(
        self,
        from_guild: int,
        to_guild: int,
    ) -> CircuitBreaker:
        """Get the circuit breaker state for a link."""
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)
            if key not in self._circuit_breakers:
                self._circuit_breakers[key] = CircuitBreaker()
            return self._circuit_breakers[key]

    async def trip_circuit(
        self,
        from_guild: int,
        to_guild: int,
        reason: str,
        approval_message_id: Optional[int] = None,
    ) -> None:
        """
        Trip (open) the circuit breaker for a link.

        This pauses syncing until the receiving guild approves.
        """
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)

            self._circuit_breakers[key] = CircuitBreaker(
                state="pending_approval",
                triggered_at=dt_to_iso(utcnow()),
                trigger_reason=reason,
                approval_message_id=approval_message_id,
                queued_actions=[],
            )

            await self._save_state()

    async def set_approval_message_id(
        self,
        from_guild: int,
        to_guild: int,
        message_id: int,
    ) -> None:
        """Attach an approval message ID to an existing circuit."""
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)
            cb = self._circuit_breakers.get(key)
            if not cb:
                return
            cb.approval_message_id = message_id
            await self._save_state()

    async def queue_action(
        self,
        from_guild: int,
        to_guild: int,
        action_data: Dict[str, Any],
    ) -> None:
        """Queue an action while circuit is open."""
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)

            if key not in self._circuit_breakers:
                return

            cb = self._circuit_breakers[key]
            if cb.state != "pending_approval":
                return

            cb.queued_actions.append(action_data)
            await self._save_state()

    async def approve_circuit(
        self,
        from_guild: int,
        to_guild: int,
        apply_queued: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Approve and close the circuit breaker.

        Args:
            from_guild: The guild that triggered the protection
            to_guild: The guild that is approving
            apply_queued: Whether to return queued actions to apply

        Returns:
            List of queued actions if apply_queued=True, else empty list.
        """
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)

            if key not in self._circuit_breakers:
                return []

            cb = self._circuit_breakers[key]
            queued = cb.queued_actions if apply_queued else []

            # Reset to closed state
            self._circuit_breakers[key] = CircuitBreaker(state="closed")

            # Clear action history for fresh start
            if from_guild in self._action_history:
                self._action_history[from_guild] = []

            await self._save_state()
            return queued

    async def decline_circuit(
        self,
        from_guild: int,
        to_guild: int,
    ) -> None:
        """
        Decline the circuit - keeps it open permanently until unlinked.
        """
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)

            if key not in self._circuit_breakers:
                return

            cb = self._circuit_breakers[key]
            cb.state = "open"
            cb.queued_actions = []  # Discard queued actions

            await self._save_state()

    async def reset_circuit(
        self,
        from_guild: int,
        to_guild: int,
    ) -> None:
        """Reset a circuit breaker to closed state."""
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)

            if key in self._circuit_breakers:
                del self._circuit_breakers[key]

            await self._save_state()

    async def is_sync_allowed(
        self,
        from_guild: int,
        to_guild: int,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if syncing is allowed between two guilds.

        Returns (allowed, reason_if_blocked).
        """
        async with self._lock:
            key = self._circuit_key(from_guild, to_guild)

            if key not in self._circuit_breakers:
                return True, None

            cb = self._circuit_breakers[key]

            if cb.state == "closed":
                return True, None
            elif cb.state == "pending_approval":
                return False, f"⚠️ Protection triggered: {cb.trigger_reason}. Awaiting approval."
            else:  # open
                return False, f"🚫 Sync blocked: {cb.trigger_reason}. Declined by admin."

    async def get_action_count(self, guild_id: int) -> int:
        """Get current action count in detection window."""
        async with self._lock:
            window_seconds, _ = await self.get_guild_thresholds(guild_id)
            self._cleanup_old_actions(guild_id, window_seconds)
            return len(self._action_history.get(guild_id, []))

    async def get_all_tripped_circuits(self, guild_id: int) -> List[tuple[int, CircuitBreaker]]:
        """
        Get all circuits that are tripped involving this guild.

        Returns list of (other_guild_id, circuit_breaker) for circuits
        where this guild needs to approve.
        """
        async with self._lock:
            results = []
            for key, cb in self._circuit_breakers.items():
                if cb.state != "pending_approval":
                    continue

                parts = key.split(":")
                if len(parts) != 2:
                    continue

                from_id, to_id = int(parts[0]), int(parts[1])
                if to_id == guild_id:
                    results.append((from_id, cb))

            return results

    async def find_circuit_by_message_id(
        self,
        message_id: int,
        to_guild_id: Optional[int] = None,
    ) -> Optional[tuple[int, int, CircuitBreaker]]:
        """
        Find a pending circuit by approval message ID.

        Returns (from_guild_id, to_guild_id, circuit_breaker) if found.
        """
        async with self._lock:
            for key, cb in self._circuit_breakers.items():
                if cb.state != "pending_approval":
                    continue
                if cb.approval_message_id != message_id:
                    continue
                parts = key.split(":")
                if len(parts) != 2:
                    continue
                from_id, to_id = int(parts[0]), int(parts[1])
                if to_guild_id is not None and to_id != to_guild_id:
                    continue
                return from_id, to_id, cb
            return None


# Global singleton
_protection: Optional[SyncProtection] = None
_protection_lock = asyncio.Lock()


async def get_sync_protection() -> SyncProtection:
    """Get or create the global SyncProtection instance."""
    global _protection
    async with _protection_lock:
        if _protection is None:
            _protection = SyncProtection()
            await _protection.initialize()
        return _protection
