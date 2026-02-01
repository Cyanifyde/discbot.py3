from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

import discord

from classes.response_handlers import BaseResponder, ResponderInput

_REMINDER_TASKS: set[asyncio.Task] = set()


class RemindMeResponder(BaseResponder):
    async def run(self, payload: ResponderInput) -> Any:
        text = payload.text or ""
        delay_data = _parse_delay(text)
        if delay_data is None:
            return (
                "Usage: remindme <minutes>:<hours>:<days> <message>. "
                "Example: remindme 10:0:0 take a break"
            )
        delay_seconds, reminder_text = delay_data
        if delay_seconds <= 0:
            return "Reminder delay must be greater than 0."
        if not reminder_text:
            reminder_text = "Reminder!"
        task = asyncio.create_task(
            _send_reminder(payload.message, payload.message.author, delay_seconds, reminder_text)
        )
        _REMINDER_TASKS.add(task)
        task.add_done_callback(_REMINDER_TASKS.discard)
        human = _format_delay(delay_seconds)
        return f"I will remind you in {human}."


def _parse_delay(text: str) -> Optional[Tuple[int, str]]:
    tokens = text.split()
    if not tokens:
        return None
    time_index = None
    for idx, token in enumerate(tokens):
        if re.fullmatch(r"\d+:\d+:\d+", token):
            time_index = idx
            break
    if time_index is None:
        return None
    minutes, hours, days = (int(part) for part in tokens[time_index].split(":"))
    delay_seconds = minutes * 60 + hours * 3600 + days * 86400
    reminder_text = " ".join(tokens[time_index + 1 :]).strip()
    return delay_seconds, reminder_text


async def _send_reminder(
    message: discord.Message,
    target: discord.abc.User,
    delay_seconds: int,
    reminder_text: str,
) -> None:
    await asyncio.sleep(delay_seconds)
    content = f"{target.mention} {reminder_text}".strip()
    try:
        await message.channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(users=[target]),
        )
    except Exception:
        return


def _format_delay(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: List[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts) or "0 seconds"
