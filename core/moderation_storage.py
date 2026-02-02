"""
Moderation storage - persistent storage for warnings, notes, and mod actions.

Provides per-guild storage for moderation data with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso

# Storage directory
MODERATION_DIR = BASE_DIR / "data" / "moderation"


class ModerationStore:
    """Per-guild storage for moderation data (warnings, notes)."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = MODERATION_DIR / str(guild_id)
        self.warnings_path = self.root / "warnings.json"
        self.notes_path = self.root / "notes.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Warnings ─────────────────────────────────────────────────────────────

    async def _read_warnings(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read warnings file."""
        data = await read_json(self.warnings_path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_warnings(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Write warnings file."""
        await write_json_atomic(self.warnings_path, data)

    async def add_warning(
        self,
        user_id: int,
        mod_id: int,
        reason: str,
        category: str = "general",
        permanent: bool = False,
        expiry_days: int = 30,
    ) -> Dict[str, Any]:
        """
        Add a warning to a user.

        Args:
            user_id: User being warned
            mod_id: Moderator issuing warning
            reason: Warning reason
            category: Warning category (default "general")
            permanent: Whether warning is permanent
            expiry_days: Days until expiry if not permanent

        Returns the created warning record.
        """
        async with self._lock:
            data = await self._read_warnings()
            user_key = str(user_id)

            if user_key not in data:
                data[user_key] = []

            # Generate next ID
            existing_ids = [w.get("id", 0) for w in data[user_key]]
            next_id = max(existing_ids, default=0) + 1

            now = utcnow()
            expires_at = None
            if not permanent and expiry_days > 0:
                from datetime import timedelta
                expiry_dt = now + timedelta(days=expiry_days)
                expires_at = dt_to_iso(expiry_dt)

            warning = {
                "id": next_id,
                "reason": reason,
                "mod_id": str(mod_id),
                "timestamp": dt_to_iso(now),
                "category": category,
                "expires_at": expires_at,
                "permanent": permanent,
            }

            data[user_key].append(warning)
            await self._write_warnings(data)

            return warning

    async def get_warnings(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all warnings for a user."""
        async with self._lock:
            data = await self._read_warnings()
            return data.get(str(user_id), [])

    async def remove_warning(self, user_id: int, warning_id: int) -> bool:
        """
        Remove a specific warning by ID.

        Returns True if removed, False if not found.
        """
        async with self._lock:
            data = await self._read_warnings()
            user_key = str(user_id)

            if user_key not in data:
                return False

            original_len = len(data[user_key])
            data[user_key] = [w for w in data[user_key] if w.get("id") != warning_id]

            if len(data[user_key]) == original_len:
                return False

            # Clean up empty lists
            if not data[user_key]:
                del data[user_key]

            await self._write_warnings(data)
            return True

    async def clear_warnings(self, user_id: int) -> int:
        """
        Clear all warnings for a user.

        Returns the number of warnings removed.
        """
        async with self._lock:
            data = await self._read_warnings()
            user_key = str(user_id)

            if user_key not in data:
                return 0

            count = len(data[user_key])
            del data[user_key]

            await self._write_warnings(data)
            return count

    async def count_warnings(self, user_id: int) -> int:
        """Get the number of warnings for a user."""
        warnings = await self.get_warnings(user_id)
        return len(warnings)

    # ─── Notes ────────────────────────────────────────────────────────────────

    async def _read_notes(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read notes file."""
        data = await read_json(self.notes_path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_notes(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Write notes file."""
        await write_json_atomic(self.notes_path, data)

    async def add_note(
        self,
        user_id: int,
        mod_id: int,
        text: str,
    ) -> Dict[str, Any]:
        """
        Add a note to a user.

        Returns the created note record.
        """
        async with self._lock:
            data = await self._read_notes()
            user_key = str(user_id)

            if user_key not in data:
                data[user_key] = []

            # Generate next ID
            existing_ids = [n.get("id", 0) for n in data[user_key]]
            next_id = max(existing_ids, default=0) + 1

            note = {
                "id": next_id,
                "text": text,
                "mod_id": str(mod_id),
                "timestamp": dt_to_iso(utcnow()),
            }

            data[user_key].append(note)
            await self._write_notes(data)

            return note

    async def get_notes(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all notes for a user."""
        async with self._lock:
            data = await self._read_notes()
            return data.get(str(user_id), [])

    async def remove_note(self, user_id: int, note_id: int) -> bool:
        """
        Remove a specific note by ID.

        Returns True if removed, False if not found.
        """
        async with self._lock:
            data = await self._read_notes()
            user_key = str(user_id)

            if user_key not in data:
                return False

            original_len = len(data[user_key])
            data[user_key] = [n for n in data[user_key] if n.get("id") != note_id]

            if len(data[user_key]) == original_len:
                return False

            # Clean up empty lists
            if not data[user_key]:
                del data[user_key]

            await self._write_notes(data)
            return True

    async def clear_notes(self, user_id: int) -> int:
        """
        Clear all notes for a user.

        Returns the number of notes removed.
        """
        async with self._lock:
            data = await self._read_notes()
            user_key = str(user_id)

            if user_key not in data:
                return 0

            count = len(data[user_key])
            del data[user_key]

            await self._write_notes(data)
            return count

    # ─── Warning Expiry ───────────────────────────────────────────────────────

    async def get_active_warnings(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Get non-expired warnings for a user.

        Filters out expired warnings based on expires_at field.
        """
        from .utils import iso_to_dt

        warnings = await self.get_warnings(user_id)
        now = utcnow()

        active = []
        for warning in warnings:
            # Permanent warnings never expire
            if warning.get("permanent", False):
                active.append(warning)
                continue

            # Check expiry
            expires_at_str = warning.get("expires_at")
            if not expires_at_str:
                # Legacy warnings without expiry are considered active
                active.append(warning)
                continue

            expires_at = iso_to_dt(expires_at_str)
            if expires_at and expires_at > now:
                active.append(warning)

        return active

    async def count_active_warnings(self, user_id: int) -> int:
        """Count non-expired warnings for a user."""
        active = await self.get_active_warnings(user_id)
        return len(active)

    # ─── Escalation Config ────────────────────────────────────────────────────

    async def _read_escalation_config(self) -> Dict[str, Any]:
        """Read escalation configuration."""
        escalation_path = self.root / "escalation_config.json"
        default = {
            "enabled": True,
            "thresholds": [
                {"warnings": 3, "action": "mute", "duration": 3600},
                {"warnings": 5, "action": "mute", "duration": 86400},
                {"warnings": 7, "action": "tempban", "duration": 604800},
                {"warnings": 10, "action": "ban", "duration": None},
            ],
            "category_paths": {},
            "cooldown_hours": 24,
            "dm_on_escalation": True,
            "appeal_info": None,
        }
        data = await read_json(escalation_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_escalation_config(self, data: Dict[str, Any]) -> None:
        """Write escalation configuration."""
        escalation_path = self.root / "escalation_config.json"
        await write_json_atomic(escalation_path, data)

    async def get_escalation_config(self) -> Dict[str, Any]:
        """Get escalation configuration."""
        async with self._lock:
            return await self._read_escalation_config()

    async def update_escalation_config(self, updates: Dict[str, Any]) -> None:
        """Update escalation configuration."""
        async with self._lock:
            data = await self._read_escalation_config()
            data.update(updates)
            await self._write_escalation_config(data)

    async def set_escalation_threshold(
        self,
        warnings: int,
        action: str,
        duration: Optional[int] = None,
    ) -> None:
        """Set or update an escalation threshold."""
        async with self._lock:
            data = await self._read_escalation_config()

            # Find existing threshold
            found = False
            for threshold in data["thresholds"]:
                if threshold["warnings"] == warnings:
                    threshold["action"] = action
                    threshold["duration"] = duration
                    found = True
                    break

            if not found:
                data["thresholds"].append({
                    "warnings": warnings,
                    "action": action,
                    "duration": duration,
                })
                # Sort by warnings count
                data["thresholds"].sort(key=lambda t: t["warnings"])

            await self._write_escalation_config(data)

    # ─── Shadow Mod Log ───────────────────────────────────────────────────────

    async def _read_shadow_log(self) -> Dict[str, Any]:
        """Read shadow log."""
        shadow_log_path = self.root / "shadow_log.json"
        default = {
            "entries": [],
            "next_case_number": 1,
            "channel_id": None,
        }
        data = await read_json(shadow_log_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_shadow_log(self, data: Dict[str, Any]) -> None:
        """Write shadow log."""
        shadow_log_path = self.root / "shadow_log.json"
        await write_json_atomic(shadow_log_path, data)

    async def add_shadow_log_entry(
        self,
        action: str,
        target_id: int,
        moderator_id: int,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add an entry to the shadow mod log.

        Args:
            action: Action type (warn, mute, kick, ban, etc.)
            target_id: User being acted upon
            moderator_id: Moderator performing action
            reason: Optional reason

        Returns:
            The created log entry with case number
        """
        async with self._lock:
            data = await self._read_shadow_log()

            case_number = data["next_case_number"]
            data["next_case_number"] += 1

            entry = {
                "case_number": case_number,
                "action": action,
                "target_id": target_id,
                "moderator_id": moderator_id,
                "reason": reason,
                "timestamp": dt_to_iso(utcnow()),
            }

            data["entries"].append(entry)
            await self._write_shadow_log(data)

            return entry

    async def get_shadow_log_entry(self, case_number: int) -> Optional[Dict[str, Any]]:
        """Get a specific shadow log entry by case number."""
        async with self._lock:
            data = await self._read_shadow_log()
            for entry in data["entries"]:
                if entry.get("case_number") == case_number:
                    return entry
            return None

    async def get_shadow_log_entries(
        self,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get shadow log entries.

        Args:
            limit: Maximum number of entries to return (most recent first)

        Returns:
            List of log entries
        """
        async with self._lock:
            data = await self._read_shadow_log()
            entries = data["entries"]
            # Most recent first
            entries.reverse()
            if limit:
                entries = entries[:limit]
            return entries

    async def search_shadow_log(self, query: str) -> List[Dict[str, Any]]:
        """Search shadow log by action type, target ID, or moderator ID."""
        async with self._lock:
            data = await self._read_shadow_log()
            query_lower = query.lower()

            results = []
            for entry in data["entries"]:
                if (
                    query_lower in entry.get("action", "").lower()
                    or query in str(entry.get("target_id", ""))
                    or query in str(entry.get("moderator_id", ""))
                ):
                    results.append(entry)

            return results

    async def set_shadow_log_channel(self, channel_id: Optional[int]) -> None:
        """Set the channel for shadow log posts."""
        async with self._lock:
            data = await self._read_shadow_log()
            data["channel_id"] = channel_id
            await self._write_shadow_log(data)

    async def get_shadow_log_channel(self) -> Optional[int]:
        """Get the configured shadow log channel."""
        async with self._lock:
            data = await self._read_shadow_log()
            return data.get("channel_id")

    # ─── Probation System ─────────────────────────────────────────────────────

    async def _read_probation(self) -> Dict[str, Any]:
        """Read probation data."""
        probation_path = self.root / "probation.json"
        default = {
            "users": {},
            "auto_triggers": {
                "new_account_days": 7,
                "rejoin_after_kick": True,
                "federation_flag": True,
            },
        }
        data = await read_json(probation_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_probation(self, data: Dict[str, Any]) -> None:
        """Write probation data."""
        probation_path = self.root / "probation.json"
        await write_json_atomic(probation_path, data)

    async def add_to_probation(
        self,
        user_id: int,
        reason: str = "new_account",
        restrictions: Optional[List[str]] = None,
        exit_conditions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Add user to probation.

        Args:
            user_id: User to add to probation
            reason: Reason for probation
            restrictions: List of restrictions to apply
            exit_conditions: Conditions for automatic exit

        Returns:
            The created probation entry
        """
        async with self._lock:
            data = await self._read_probation()

            if restrictions is None:
                restrictions = ["no_dm", "no_embeds", "slowmode"]

            if exit_conditions is None:
                exit_conditions = {
                    "days_clean": 7,
                    "mod_approval": False,
                    "trust_threshold": 50,
                }

            probation_entry = {
                "started_at": dt_to_iso(utcnow()),
                "reason": reason,
                "restrictions": restrictions,
                "exit_conditions": exit_conditions,
            }

            data["users"][str(user_id)] = probation_entry
            await self._write_probation(data)

            return probation_entry

    async def remove_from_probation(self, user_id: int) -> bool:
        """Remove user from probation."""
        async with self._lock:
            data = await self._read_probation()
            user_key = str(user_id)

            if user_key in data["users"]:
                del data["users"][user_key]
                await self._write_probation(data)
                return True
            return False

    async def is_on_probation(self, user_id: int) -> bool:
        """Check if user is on probation."""
        async with self._lock:
            data = await self._read_probation()
            return str(user_id) in data["users"]

    async def get_probation_entry(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get probation entry for a user."""
        async with self._lock:
            data = await self._read_probation()
            return data["users"].get(str(user_id))

    async def get_all_probation_users(self) -> List[Dict[str, Any]]:
        """Get all users on probation."""
        async with self._lock:
            data = await self._read_probation()
            result = []
            for user_id, entry in data["users"].items():
                result.append({
                    "user_id": int(user_id),
                    **entry,
                })
            return result

    async def update_probation_config(self, updates: Dict[str, Any]) -> None:
        """Update probation auto-trigger configuration."""
        async with self._lock:
            data = await self._read_probation()
            data["auto_triggers"].update(updates)
            await self._write_probation(data)

    async def get_probation_config(self) -> Dict[str, Any]:
        """Get probation configuration."""
        async with self._lock:
            data = await self._read_probation()
            return data["auto_triggers"]

    # ─── Mod Action Templates ─────────────────────────────────────────────────

    async def _read_templates(self) -> Dict[str, Any]:
        """Read templates data."""
        templates_path = self.root / "templates.json"
        default = {"templates": {}}
        data = await read_json(templates_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_templates(self, data: Dict[str, Any]) -> None:
        """Write templates data."""
        templates_path = self.root / "templates.json"
        await write_json_atomic(templates_path, data)

    async def add_template(
        self,
        name: str,
        reason: str,
        category: str = "general",
        action: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a mod action template.

        Args:
            name: Template name
            reason: Default reason text
            category: Warning category
            action: Optional action (warn, mute, ban)

        Returns:
            The created template
        """
        async with self._lock:
            data = await self._read_templates()

            template = {
                "reason": reason,
                "category": category,
                "action": action,
                "created_at": dt_to_iso(utcnow()),
            }

            data["templates"][name] = template
            await self._write_templates(data)

            return template

    async def remove_template(self, name: str) -> bool:
        """Remove a template."""
        async with self._lock:
            data = await self._read_templates()

            if name in data["templates"]:
                del data["templates"][name]
                await self._write_templates(data)
                return True
            return False

    async def get_template(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a template by name."""
        async with self._lock:
            data = await self._read_templates()
            return data["templates"].get(name)

    async def get_all_templates(self) -> Dict[str, Dict[str, Any]]:
        """Get all templates."""
        async with self._lock:
            data = await self._read_templates()
            return data["templates"]

    # ─── Action Reversal ──────────────────────────────────────────────────────

    async def _read_recent_actions(self) -> Dict[str, Any]:
        """Read recent actions data."""
        actions_path = self.root / "recent_actions.json"
        default = {
            "actions": [],
            "grace_period_minutes": 5,
        }
        data = await read_json(actions_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_recent_actions(self, data: Dict[str, Any]) -> None:
        """Write recent actions data."""
        actions_path = self.root / "recent_actions.json"
        await write_json_atomic(actions_path, data)

    async def record_action(
        self,
        action_type: str,
        target_id: int,
        moderator_id: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Record an action for potential reversal.

        Args:
            action_type: Type of action (warn, mute, kick)
            target_id: User acted upon
            moderator_id: Moderator performing action
            details: Additional details (warning_id, duration, etc.)

        Returns:
            Action ID for reversal
        """
        import uuid
        from datetime import timedelta

        async with self._lock:
            data = await self._read_recent_actions()

            action_id = str(uuid.uuid4())
            grace_period = data.get("grace_period_minutes", 5)
            expires_at_dt = utcnow() + timedelta(minutes=grace_period)

            action = {
                "id": action_id,
                "action_type": action_type,
                "target_id": target_id,
                "moderator_id": moderator_id,
                "timestamp": dt_to_iso(utcnow()),
                "expires_at": dt_to_iso(expires_at_dt),
                "reversed": False,
                "details": details or {},
            }

            data["actions"].append(action)

            # Clean up expired actions
            from .utils import iso_to_dt
            now = utcnow()
            data["actions"] = [
                a for a in data["actions"]
                if iso_to_dt(a["expires_at"]) > now
            ]

            await self._write_recent_actions(data)

            return action_id

    async def get_recent_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Get a recent action by ID."""
        async with self._lock:
            data = await self._read_recent_actions()
            for action in data["actions"]:
                if action["id"] == action_id:
                    return action
            return None

    async def get_last_action(
        self,
        moderator_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent action.

        Args:
            moderator_id: Optional filter by moderator

        Returns:
            Most recent action or None
        """
        async with self._lock:
            data = await self._read_recent_actions()

            if not data["actions"]:
                return None

            actions = data["actions"]
            if moderator_id:
                actions = [a for a in actions if a["moderator_id"] == moderator_id]

            if not actions:
                return None

            # Return most recent
            return actions[-1]

    async def mark_action_reversed(self, action_id: str) -> bool:
        """Mark an action as reversed."""
        async with self._lock:
            data = await self._read_recent_actions()

            for action in data["actions"]:
                if action["id"] == action_id:
                    action["reversed"] = True
                    await self._write_recent_actions(data)
                    return True

            return False

    async def set_grace_period(self, minutes: int) -> None:
        """Set grace period for action reversal."""
        async with self._lock:
            data = await self._read_recent_actions()
            data["grace_period_minutes"] = minutes
            await self._write_recent_actions(data)


# Cache of ModerationStore instances per guild
_stores: Dict[int, ModerationStore] = {}
_stores_lock = asyncio.Lock()


async def get_moderation_store(guild_id: int) -> ModerationStore:
    """Get or create a ModerationStore for a guild."""
    async with _stores_lock:
        if guild_id not in _stores:
            store = ModerationStore(guild_id)
            await store.initialize()
            _stores[guild_id] = store
        return _stores[guild_id]
