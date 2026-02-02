"""
Automation service - business logic for triggers, schedules, and vacation mode.

Handles automated actions, trigger execution, scheduling, and vacation mode management.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.automation_storage import AutomationStore
from core.utils import utcnow, dt_to_iso

if TYPE_CHECKING:
    import discord


class AutomationService:
    """Business logic for automation features."""

    def __init__(self) -> None:
        self._stores: Dict[int, AutomationStore] = {}

    def _get_store(self, guild_id: int) -> AutomationStore:
        """Get or create an automation store for a guild."""
        if guild_id not in self._stores:
            self._stores[guild_id] = AutomationStore(guild_id)
        return self._stores[guild_id]

    async def initialize_store(self, guild_id: int) -> None:
        """Initialize storage for a guild."""
        store = self._get_store(guild_id)
        await store.initialize()

    # ─── Triggers ─────────────────────────────────────────────────────────────

    async def create_trigger(
        self,
        guild_id: int,
        event: str,
        condition: Dict[str, Any],
        action: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Create an automation trigger.

        Args:
            guild_id: Guild ID
            event: Event type (e.g., "commission_filled", "slots_available")
            condition: Condition to check
            action: Action to execute

        Returns:
            Created trigger
        """
        store = self._get_store(guild_id)
        await store.initialize()

        trigger_id = str(uuid.uuid4())
        return await store.add_trigger(trigger_id, event, condition, action)

    async def get_trigger(
        self,
        guild_id: int,
        trigger_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific trigger."""
        store = self._get_store(guild_id)
        return await store.get_trigger(trigger_id)

    async def get_triggers_for_event(
        self,
        guild_id: int,
        event: str,
    ) -> List[Dict[str, Any]]:
        """Get all enabled triggers for an event."""
        store = self._get_store(guild_id)
        triggers = await store.get_all_triggers(event)
        return [t for t in triggers if t.get("enabled", True)]

    async def update_trigger(
        self,
        guild_id: int,
        trigger_id: str,
        enabled: Optional[bool] = None,
        condition: Optional[Dict[str, Any]] = None,
        action: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update a trigger."""
        store = self._get_store(guild_id)
        updates = {}

        if enabled is not None:
            updates["enabled"] = enabled
        if condition is not None:
            updates["condition"] = condition
        if action is not None:
            updates["action"] = action

        return await store.update_trigger(trigger_id, updates)

    async def delete_trigger(
        self,
        guild_id: int,
        trigger_id: str,
    ) -> bool:
        """Delete a trigger."""
        store = self._get_store(guild_id)
        return await store.remove_trigger(trigger_id)

    async def execute_triggers(
        self,
        guild_id: int,
        event: str,
        context: Dict[str, Any],
        bot: discord.Client,
    ) -> int:
        """
        Execute all matching triggers for an event.

        Returns:
            Number of triggers executed
        """
        triggers = await self.get_triggers_for_event(guild_id, event)
        executed = 0

        for trigger in triggers:
            # Check condition
            if self._check_condition(trigger["condition"], context):
                # Execute action
                await self._execute_action(
                    guild_id,
                    trigger["action"],
                    context,
                    bot,
                )

                # Record execution
                store = self._get_store(guild_id)
                await store.record_trigger_execution(trigger["id"])
                executed += 1

        return executed

    def _check_condition(
        self,
        condition: Dict[str, Any],
        context: Dict[str, Any],
    ) -> bool:
        """Check if a condition is met."""
        condition_type = condition.get("type")

        if condition_type == "always":
            return True
        elif condition_type == "slots_empty":
            return context.get("available_slots", 0) == 0
        elif condition_type == "slots_available":
            return context.get("available_slots", 0) > 0
        elif condition_type == "user_equals":
            return context.get("user_id") == condition.get("user_id")
        elif condition_type == "count_greater_than":
            field = condition.get("field", "count")
            threshold = condition.get("threshold", 0)
            return context.get(field, 0) > threshold

        return False

    async def _execute_action(
        self,
        guild_id: int,
        action: Dict[str, Any],
        context: Dict[str, Any],
        bot: discord.Client,
    ) -> None:
        """Execute an action."""
        action_type = action.get("type")

        if action_type == "send_message":
            await self._send_message_action(guild_id, action, context, bot)
        elif action_type == "auto_close_commissions":
            await self._auto_close_commissions(guild_id, context)
        elif action_type == "auto_open_commissions":
            await self._auto_open_commissions(guild_id, context)
        elif action_type == "promote_waitlist":
            await self._promote_waitlist(guild_id, context)

    async def _send_message_action(
        self,
        guild_id: int,
        action: Dict[str, Any],
        context: Dict[str, Any],
        bot: discord.Client,
    ) -> None:
        """Send a message action."""
        channel_id = action.get("channel_id")
        message = action.get("message", "")

        # Template substitution
        for key, value in context.items():
            message = message.replace(f"{{{key}}}", str(value))

        try:
            channel = bot.get_channel(channel_id)
            if channel:
                await channel.send(message)
        except Exception:
            pass

    async def _auto_close_commissions(
        self,
        guild_id: int,
        context: Dict[str, Any],
    ) -> None:
        """Auto-close commissions."""
        # This would integrate with commission_service
        pass

    async def _auto_open_commissions(
        self,
        guild_id: int,
        context: Dict[str, Any],
    ) -> None:
        """Auto-open commissions."""
        # This would integrate with commission_service
        pass

    async def _promote_waitlist(
        self,
        guild_id: int,
        context: Dict[str, Any],
    ) -> None:
        """Promote from waitlist."""
        # This would integrate with commission_service
        pass

    # ─── Trigger Chains ───────────────────────────────────────────────────────

    async def create_chain(
        self,
        guild_id: int,
        name: str,
        steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create a trigger chain."""
        store = self._get_store(guild_id)
        await store.initialize()

        chain_id = str(uuid.uuid4())
        return await store.add_chain(chain_id, name, steps)

    async def get_chain(
        self,
        guild_id: int,
        chain_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a trigger chain."""
        store = self._get_store(guild_id)
        return await store.get_chain(chain_id)

    async def delete_chain(
        self,
        guild_id: int,
        chain_id: str,
    ) -> bool:
        """Delete a trigger chain."""
        store = self._get_store(guild_id)
        return await store.remove_chain(chain_id)

    # ─── Scheduled Actions ────────────────────────────────────────────────────

    async def schedule_action(
        self,
        guild_id: int,
        action: Dict[str, Any],
        delay_seconds: Optional[int] = None,
        execute_at: Optional[str] = None,
        repeat: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Schedule an action."""
        store = self._get_store(guild_id)
        await store.initialize()

        if delay_seconds and not execute_at:
            execute_time = utcnow() + timedelta(seconds=delay_seconds)
            execute_at = dt_to_iso(execute_time)

        schedule_id = str(uuid.uuid4())
        return await store.add_schedule(schedule_id, action, execute_at, repeat)

    async def get_schedule(
        self,
        guild_id: int,
        schedule_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a schedule."""
        store = self._get_store(guild_id)
        return await store.get_schedule(schedule_id)

    async def cancel_schedule(
        self,
        guild_id: int,
        schedule_id: str,
    ) -> bool:
        """Cancel a scheduled action."""
        store = self._get_store(guild_id)
        return await store.remove_schedule(schedule_id)

    async def process_pending_schedules(
        self,
        guild_id: int,
        bot: discord.Client,
    ) -> int:
        """
        Process all pending schedules.

        Returns:
            Number of schedules executed
        """
        store = self._get_store(guild_id)
        pending = await store.get_pending_schedules()
        executed = 0

        for schedule in pending:
            # Execute action
            await self._execute_action(
                guild_id,
                schedule["action"],
                {},
                bot,
            )

            # Calculate next execution if repeating
            next_execution = None
            if schedule.get("repeat"):
                next_execution = self._calculate_next_execution(
                    schedule["execute_at"],
                    schedule["repeat"],
                )

            # Record execution
            await store.record_schedule_execution(
                schedule["id"],
                next_execution,
            )
            executed += 1

        return executed

    def _calculate_next_execution(self, current: str, repeat: str) -> str:
        """Calculate next execution time for repeating schedules."""
        from datetime import datetime

        current_dt = datetime.fromisoformat(current.replace("Z", "+00:00"))

        if repeat == "daily":
            next_dt = current_dt + timedelta(days=1)
        elif repeat == "weekly":
            next_dt = current_dt + timedelta(weeks=1)
        elif repeat == "monthly":
            next_dt = current_dt + timedelta(days=30)
        else:
            next_dt = current_dt

        return dt_to_iso(next_dt)

    # ─── Vacation Mode ────────────────────────────────────────────────────────

    async def set_vacation(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
        return_date: Optional[str] = None,
        auto_response: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set vacation mode for a user."""
        store = self._get_store(guild_id)
        await store.initialize()

        return await store.set_vacation_mode(
            user_id,
            enabled,
            return_date,
            auto_response,
        )

    async def get_vacation(
        self,
        guild_id: int,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Get vacation mode status."""
        store = self._get_store(guild_id)
        return await store.get_vacation_mode(user_id)

    async def is_on_vacation(
        self,
        guild_id: int,
        user_id: int,
    ) -> bool:
        """Check if user is on vacation."""
        store = self._get_store(guild_id)
        return await store.is_on_vacation(user_id)

    async def get_vacation_response(
        self,
        guild_id: int,
        user_id: int,
    ) -> Optional[str]:
        """Get auto-response for user on vacation."""
        vacation = await self.get_vacation(guild_id, user_id)
        if vacation and vacation.get("enabled"):
            return vacation.get("auto_response")
        return None


# Global service instance
automation_service = AutomationService()
