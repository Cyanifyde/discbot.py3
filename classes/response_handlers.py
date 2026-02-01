"""
Base response handler classes.

Provides the base class and simple implementations for auto-responder handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import discord


@dataclass
class ResponderInput:
    message: discord.Message
    command: str
    text: str
    args: List[str]
    raw: str
    settings: Dict[str, Any]


class BaseResponder:
    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings = settings

    async def run(self, payload: ResponderInput) -> Any:
        raise NotImplementedError


class EchoResponder(BaseResponder):
    async def run(self, payload: ResponderInput) -> Any:
        return payload.text or payload.raw


class UpperResponder(BaseResponder):
    async def run(self, payload: ResponderInput) -> Any:
        text = payload.text or payload.raw
        return text.upper() if text else text


class StaticResponder(BaseResponder):
    async def run(self, payload: ResponderInput) -> Any:
        return self.settings.get("text", "")
