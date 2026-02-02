"""
Automation storage - persistent storage for automation rules and triggers.

Provides storage for automated actions, triggers, schedules, and vacation mode.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso

# Storage directory
AUTOMATION_DIR = BASE_DIR / "data" / "automation"


class AutomationStore:
    """Storage for automation features."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = AUTOMATION_DIR / str(guild_id)
        self.triggers_path = self.root / "triggers.json"
        self.schedules_path = self.root / "schedules.json"
        self.vacation_path = self.root / "vacation.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Triggers & Chains ────────────────────────────────────────────────────

    async def _read_triggers(self) -> Dict[str, Any]:
        """Read triggers file."""
        default = {"triggers": [], "chains": []}
        data = await read_json(self.triggers_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_triggers(self, data: Dict[str, Any]) -> None:
        """Write triggers file."""
        await write_json_atomic(self.triggers_path, data)

    async def add_trigger(
        self,
        trigger_id: str,
        event: str,
        condition: Dict[str, Any],
        action: Dict[str, Any],
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """Add an automation trigger."""
        async with self._lock:
            data = await self._read_triggers()

            trigger = {
                "id": trigger_id,
                "event": event,
                "condition": condition,
                "action": action,
                "enabled": enabled,
                "created_at": dt_to_iso(utcnow()),
                "last_triggered": None,
                "trigger_count": 0,
            }

            data["triggers"].append(trigger)
            await self._write_triggers(data)
            return trigger

    async def get_trigger(self, trigger_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific trigger."""
        async with self._lock:
            data = await self._read_triggers()
            for trigger in data["triggers"]:
                if trigger["id"].startswith(trigger_id):
                    return trigger
            return None

    async def get_all_triggers(self, event: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all triggers, optionally filtered by event."""
        async with self._lock:
            data = await self._read_triggers()
            triggers = data["triggers"]

            if event:
                triggers = [t for t in triggers if t["event"] == event]

            return triggers

    async def update_trigger(
        self,
        trigger_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """Update a trigger."""
        async with self._lock:
            data = await self._read_triggers()

            for trigger in data["triggers"]:
                if trigger["id"].startswith(trigger_id):
                    trigger.update(updates)
                    await self._write_triggers(data)
                    return True

            return False

    async def remove_trigger(self, trigger_id: str) -> bool:
        """Remove a trigger."""
        async with self._lock:
            data = await self._read_triggers()
            original_len = len(data["triggers"])

            data["triggers"] = [
                t for t in data["triggers"]
                if not t["id"].startswith(trigger_id)
            ]

            if len(data["triggers"]) < original_len:
                await self._write_triggers(data)
                return True

            return False

    async def record_trigger_execution(self, trigger_id: str) -> None:
        """Record that a trigger was executed."""
        async with self._lock:
            data = await self._read_triggers()

            for trigger in data["triggers"]:
                if trigger["id"] == trigger_id:
                    trigger["last_triggered"] = dt_to_iso(utcnow())
                    trigger["trigger_count"] = trigger.get("trigger_count", 0) + 1
                    await self._write_triggers(data)
                    break

    # ─── Trigger Chains ───────────────────────────────────────────────────────

    async def add_chain(
        self,
        chain_id: str,
        name: str,
        steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Add a trigger chain (sequence of actions)."""
        async with self._lock:
            data = await self._read_triggers()

            chain = {
                "id": chain_id,
                "name": name,
                "steps": steps,
                "created_at": dt_to_iso(utcnow()),
            }

            data["chains"].append(chain)
            await self._write_triggers(data)
            return chain

    async def get_chain(self, chain_id: str) -> Optional[Dict[str, Any]]:
        """Get a trigger chain."""
        async with self._lock:
            data = await self._read_triggers()
            for chain in data["chains"]:
                if chain["id"].startswith(chain_id):
                    return chain
            return None

    async def get_all_chains(self) -> List[Dict[str, Any]]:
        """Get all trigger chains."""
        async with self._lock:
            data = await self._read_triggers()
            return data["chains"]

    async def remove_chain(self, chain_id: str) -> bool:
        """Remove a trigger chain."""
        async with self._lock:
            data = await self._read_triggers()
            original_len = len(data["chains"])

            data["chains"] = [
                c for c in data["chains"]
                if not c["id"].startswith(chain_id)
            ]

            if len(data["chains"]) < original_len:
                await self._write_triggers(data)
                return True

            return False

    # ─── Scheduled Actions ────────────────────────────────────────────────────

    async def _read_schedules(self) -> Dict[str, Any]:
        """Read schedules file."""
        default = {"schedules": []}
        data = await read_json(self.schedules_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_schedules(self, data: Dict[str, Any]) -> None:
        """Write schedules file."""
        await write_json_atomic(self.schedules_path, data)

    async def add_schedule(
        self,
        schedule_id: str,
        action: Dict[str, Any],
        execute_at: str,
        repeat: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a scheduled action."""
        async with self._lock:
            data = await self._read_schedules()

            schedule = {
                "id": schedule_id,
                "action": action,
                "execute_at": execute_at,
                "repeat": repeat,  # None, "daily", "weekly", "monthly"
                "created_at": dt_to_iso(utcnow()),
                "last_executed": None,
                "enabled": True,
            }

            data["schedules"].append(schedule)
            await self._write_schedules(data)
            return schedule

    async def get_schedule(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific schedule."""
        async with self._lock:
            data = await self._read_schedules()
            for schedule in data["schedules"]:
                if schedule["id"].startswith(schedule_id):
                    return schedule
            return None

    async def get_all_schedules(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """Get all schedules."""
        async with self._lock:
            data = await self._read_schedules()
            schedules = data["schedules"]

            if enabled_only:
                schedules = [s for s in schedules if s.get("enabled", True)]

            return schedules

    async def get_pending_schedules(self) -> List[Dict[str, Any]]:
        """Get schedules that should be executed now."""
        from datetime import datetime

        async with self._lock:
            data = await self._read_schedules()
            now = utcnow()

            pending = []
            for schedule in data["schedules"]:
                if not schedule.get("enabled", True):
                    continue

                execute_at = datetime.fromisoformat(schedule["execute_at"].replace("Z", "+00:00"))
                if execute_at <= now:
                    pending.append(schedule)

            return pending

    async def update_schedule(
        self,
        schedule_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """Update a schedule."""
        async with self._lock:
            data = await self._read_schedules()

            for schedule in data["schedules"]:
                if schedule["id"].startswith(schedule_id):
                    schedule.update(updates)
                    await self._write_schedules(data)
                    return True

            return False

    async def remove_schedule(self, schedule_id: str) -> bool:
        """Remove a schedule."""
        async with self._lock:
            data = await self._read_schedules()
            original_len = len(data["schedules"])

            data["schedules"] = [
                s for s in data["schedules"]
                if not s["id"].startswith(schedule_id)
            ]

            if len(data["schedules"]) < original_len:
                await self._write_schedules(data)
                return True

            return False

    async def record_schedule_execution(
        self,
        schedule_id: str,
        next_execution: Optional[str] = None,
    ) -> None:
        """Record schedule execution and optionally set next execution time."""
        async with self._lock:
            data = await self._read_schedules()

            for schedule in data["schedules"]:
                if schedule["id"] == schedule_id:
                    schedule["last_executed"] = dt_to_iso(utcnow())

                    if next_execution:
                        schedule["execute_at"] = next_execution
                    elif not schedule.get("repeat"):
                        # One-time schedule, disable after execution
                        schedule["enabled"] = False

                    await self._write_schedules(data)
                    break

    # ─── Vacation Mode ────────────────────────────────────────────────────────

    async def _read_vacation(self) -> Dict[str, Any]:
        """Read vacation file."""
        default = {"users": {}}
        data = await read_json(self.vacation_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_vacation(self, data: Dict[str, Any]) -> None:
        """Write vacation file."""
        await write_json_atomic(self.vacation_path, data)

    async def set_vacation_mode(
        self,
        user_id: int,
        enabled: bool,
        return_date: Optional[str] = None,
        auto_response: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set vacation mode for a user."""
        async with self._lock:
            data = await self._read_vacation()

            user_key = str(user_id)
            vacation = {
                "enabled": enabled,
                "started_at": dt_to_iso(utcnow()) if enabled else None,
                "return_date": return_date,
                "auto_response": auto_response,
            }

            data["users"][user_key] = vacation
            await self._write_vacation(data)
            return vacation

    async def get_vacation_mode(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get vacation mode status for a user."""
        async with self._lock:
            data = await self._read_vacation()
            user_key = str(user_id)
            return data["users"].get(user_key)

    async def is_on_vacation(self, user_id: int) -> bool:
        """Check if a user is on vacation."""
        vacation = await self.get_vacation_mode(user_id)
        return vacation is not None and vacation.get("enabled", False)

    async def get_all_vacation_users(self) -> List[int]:
        """Get all users currently on vacation."""
        async with self._lock:
            data = await self._read_vacation()
            return [
                int(uid)
                for uid, vac in data["users"].items()
                if vac.get("enabled", False)
            ]
