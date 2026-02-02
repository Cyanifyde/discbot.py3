"""
Custom content storage - persistent storage for custom commands, forms, and responses.

Provides storage for guild-specific custom commands, form templates, and enhanced auto-responders.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso

# Storage directory
CUSTOM_CONTENT_DIR = BASE_DIR / "data" / "custom_content"


class CustomContentStore:
    """Storage for custom content features."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = CUSTOM_CONTENT_DIR / str(guild_id)
        self.commands_path = self.root / "commands.json"
        self.forms_path = self.root / "forms.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Custom Commands ──────────────────────────────────────────────────────

    async def _read_commands(self) -> Dict[str, Any]:
        """Read custom commands file."""
        default = {"commands": {}}
        data = await read_json(self.commands_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_commands(self, data: Dict[str, Any]) -> None:
        """Write custom commands file."""
        await write_json_atomic(self.commands_path, data)

    async def add_custom_command(
        self,
        name: str,
        response: str,
        embed_data: Optional[Dict[str, Any]] = None,
        permissions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a custom command."""
        async with self._lock:
            data = await self._read_commands()

            command = {
                "name": name,
                "response": response,
                "embed_data": embed_data,
                "permissions": permissions,
                "created_at": dt_to_iso(utcnow()),
                "use_count": 0,
            }

            data["commands"][name] = command
            await self._write_commands(data)
            return command

    async def get_custom_command(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a custom command."""
        async with self._lock:
            data = await self._read_commands()
            return data["commands"].get(name)

    async def get_all_custom_commands(self) -> Dict[str, Dict[str, Any]]:
        """Get all custom commands."""
        async with self._lock:
            data = await self._read_commands()
            return data["commands"]

    async def remove_custom_command(self, name: str) -> bool:
        """Remove a custom command."""
        async with self._lock:
            data = await self._read_commands()

            if name in data["commands"]:
                del data["commands"][name]
                await self._write_commands(data)
                return True

            return False

    async def update_custom_command(
        self,
        name: str,
        *,
        response: Optional[str] = None,
        embed_data: Optional[Dict[str, Any]] = None,
        permissions: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing custom command. Returns updated command if found."""
        async with self._lock:
            data = await self._read_commands()
            cmd = data["commands"].get(name)
            if not isinstance(cmd, dict):
                return None
            if response is not None:
                cmd["response"] = response
            if embed_data is not None:
                cmd["embed_data"] = embed_data
            if permissions is not None:
                cmd["permissions"] = permissions
            data["commands"][name] = cmd
            await self._write_commands(data)
            return cmd

    async def increment_command_usage(self, name: str) -> None:
        """Increment usage count for a command."""
        async with self._lock:
            data = await self._read_commands()

            if name in data["commands"]:
                data["commands"][name]["use_count"] = data["commands"][name].get("use_count", 0) + 1
                await self._write_commands(data)

    # ─── Forms ────────────────────────────────────────────────────────────────

    async def _read_forms(self) -> Dict[str, Any]:
        """Read forms file."""
        default = {"forms": {}, "submissions": []}
        data = await read_json(self.forms_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_forms(self, data: Dict[str, Any]) -> None:
        """Write forms file."""
        await write_json_atomic(self.forms_path, data)

    async def add_form(
        self,
        form_id: str,
        name: str,
        fields: List[Dict[str, Any]],
        submit_channel_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Add a form template."""
        async with self._lock:
            data = await self._read_forms()

            form = {
                "id": form_id,
                "name": name,
                "fields": fields,
                "submit_channel_id": submit_channel_id,
                "created_at": dt_to_iso(utcnow()),
            }

            data["forms"][form_id] = form
            await self._write_forms(data)
            return form

    async def get_form(self, form_id: str) -> Optional[Dict[str, Any]]:
        """Get a form template."""
        async with self._lock:
            data = await self._read_forms()
            for fid, form in data["forms"].items():
                if fid.startswith(form_id) or form["name"].lower() == form_id.lower():
                    return form
            return None

    async def get_all_forms(self) -> Dict[str, Dict[str, Any]]:
        """Get all form templates."""
        async with self._lock:
            data = await self._read_forms()
            return data["forms"]

    async def remove_form(self, form_id: str) -> Optional[Dict[str, Any]]:
        """
        Remove a form template by ID prefix or name.

        Also removes submissions for that form.
        Returns the removed form if found.
        """
        async with self._lock:
            data = await self._read_forms()
            forms = data.get("forms", {})
            if not isinstance(forms, dict):
                return None

            target_id: Optional[str] = None
            removed: Optional[Dict[str, Any]] = None
            for fid, form in forms.items():
                if not isinstance(form, dict):
                    continue
                name = form.get("name", "")
                if fid.startswith(form_id) or (isinstance(name, str) and name.lower() == form_id.lower()):
                    target_id = fid
                    removed = form
                    break

            if not target_id or removed is None:
                return None

            del forms[target_id]
            data["forms"] = forms

            submissions = data.get("submissions", [])
            if isinstance(submissions, list):
                data["submissions"] = [s for s in submissions if isinstance(s, dict) and s.get("form_id") != target_id]

            await self._write_forms(data)
            return removed

    async def add_form_submission(
        self,
        submission_id: str,
        form_id: str,
        user_id: int,
        responses: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Add a form submission."""
        async with self._lock:
            data = await self._read_forms()

            submission = {
                "id": submission_id,
                "form_id": form_id,
                "user_id": user_id,
                "responses": responses,
                "submitted_at": dt_to_iso(utcnow()),
            }

            data["submissions"].append(submission)
            await self._write_forms(data)
            return submission

    async def get_form_submissions(
        self,
        form_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get form submissions, optionally filtered by form ID."""
        async with self._lock:
            data = await self._read_forms()
            submissions = data["submissions"]

            if form_id:
                submissions = [s for s in submissions if s["form_id"] == form_id]

            return submissions
