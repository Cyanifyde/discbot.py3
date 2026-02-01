"""
Type definitions and dataclasses for the Discord bot.

Using dataclasses instead of raw dicts provides:
- Type safety and IDE autocomplete
- Self-documenting code
- Easier refactoring
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttachmentInfo:
    """Information about a Discord attachment."""
    url: str
    filename: str
    size: int
    content_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "filename": self.filename,
            "size": self.size,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttachmentInfo:
        return cls(
            url=data.get("url", ""),
            filename=data.get("filename", ""),
            size=data.get("size", 0),
            content_type=data.get("content_type"),
        )


@dataclass
class LinkedMessage:
    """Reference to a linked Discord message."""
    guild_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinkedMessage:
        return cls(
            guild_id=data.get("guild_id"),
            channel_id=data.get("channel_id"),
            message_id=data.get("message_id"),
        )


@dataclass
class ScanJob:
    """
    A job to scan an image for hash matching.
    
    Sources:
    - "attachment": Direct file attachment
    - "discord_cdn_url": URL pointing to Discord CDN
    - "discord_message_link": Link to another Discord message
    """
    guild_id: str
    channel_id: str
    message_id: str
    author_id: str
    source: str
    enqueued_at: str = ""
    v: int = 2
    attachment: AttachmentInfo | None = None
    url: str | None = None
    linked: LinkedMessage = field(default_factory=LinkedMessage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "enqueued_at": self.enqueued_at,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "author_id": self.author_id,
            "source": self.source,
            "attachment": self.attachment.to_dict() if self.attachment else None,
            "url": self.url,
            "linked": self.linked.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanJob:
        attachment_data = data.get("attachment")
        linked_data = data.get("linked") or {}
        return cls(
            v=data.get("v", 2),
            enqueued_at=data.get("enqueued_at", ""),
            guild_id=data.get("guild_id", ""),
            channel_id=data.get("channel_id", ""),
            message_id=data.get("message_id", ""),
            author_id=data.get("author_id", ""),
            source=data.get("source", ""),
            attachment=AttachmentInfo.from_dict(attachment_data) if attachment_data else None,
            url=data.get("url"),
            linked=LinkedMessage.from_dict(linked_data),
        )


@dataclass
class EnforcementResult:
    """Result of an enforcement action (role removal, etc.)."""
    roles_removed: int = 0
    unverified_added: bool = False
    message_deleted: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None
