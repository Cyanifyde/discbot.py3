"""
Type definitions and dataclasses for the Discord bot.

Using dataclasses instead of raw dicts provides:
- Type safety and IDE autocomplete
- Self-documenting code
- Easier refactoring
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AttachmentInfo:
    """Information about a Discord attachment."""
    url: str
    filename: str
    size: int
    content_type: Optional[str] = None

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
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    message_id: Optional[str] = None

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
    attachment: Optional[AttachmentInfo] = None
    url: Optional[str] = None
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
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class TrustScore:
    """Trust score calculation for a user in a guild."""
    user_id: int
    guild_id: int
    children_count_score: float  # 15% weight
    upflow_status_score: float   # 20% weight
    vouches_score: float         # 25% weight
    link_age_score: float        # 15% weight
    approval_rate_score: float   # 25% weight
    total_score: float
    tier: str  # untrusted/neutral/trusted/highly_trusted
    last_updated: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "guild_id": self.guild_id,
            "children_count_score": self.children_count_score,
            "upflow_status_score": self.upflow_status_score,
            "vouches_score": self.vouches_score,
            "link_age_score": self.link_age_score,
            "approval_rate_score": self.approval_rate_score,
            "total_score": self.total_score,
            "tier": self.tier,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustScore:
        return cls(
            user_id=data.get("user_id", 0),
            guild_id=data.get("guild_id", 0),
            children_count_score=data.get("children_count_score", 0.0),
            upflow_status_score=data.get("upflow_status_score", 0.0),
            vouches_score=data.get("vouches_score", 0.0),
            link_age_score=data.get("link_age_score", 0.0),
            approval_rate_score=data.get("approval_rate_score", 0.0),
            total_score=data.get("total_score", 0.0),
            tier=data.get("tier", "untrusted"),
            last_updated=data.get("last_updated", ""),
        )


@dataclass
class Commission:
    """Commission tracking information."""
    id: str
    artist_id: int
    client_id: int
    guild_id: int
    stage: str
    created_at: str
    updated_at: str
    deadline: Optional[str] = None
    revisions_used: int = 0
    revisions_limit: int = 3
    tags: list[str] = field(default_factory=list)
    payment_status: str = "pending"  # pending/partial/paid
    price: float = 0.0
    currency: str = "USD"
    notes: str = ""
    incognito: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artist_id": self.artist_id,
            "client_id": self.client_id,
            "guild_id": self.guild_id,
            "stage": self.stage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline": self.deadline,
            "revisions_used": self.revisions_used,
            "revisions_limit": self.revisions_limit,
            "tags": self.tags,
            "payment_status": self.payment_status,
            "price": self.price,
            "currency": self.currency,
            "notes": self.notes,
            "incognito": self.incognito,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Commission:
        return cls(
            id=data.get("id", ""),
            artist_id=data.get("artist_id", 0),
            client_id=data.get("client_id", 0),
            guild_id=data.get("guild_id", 0),
            stage=data.get("stage", "Inquiry"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            deadline=data.get("deadline"),
            revisions_used=data.get("revisions_used", 0),
            revisions_limit=data.get("revisions_limit", 3),
            tags=data.get("tags", []),
            payment_status=data.get("payment_status", "pending"),
            price=data.get("price", 0.0),
            currency=data.get("currency", "USD"),
            notes=data.get("notes", ""),
            incognito=data.get("incognito", False),
        )


@dataclass
class PortfolioEntry:
    """Portfolio entry for an artist."""
    id: str
    user_id: int
    image_url: str
    title: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    featured: bool = False
    privacy: str = "public"  # public/private
    commission_example: bool = False
    commission_type: Optional[str] = None
    before_after: Optional[dict[str, str]] = None  # {"before": url, "after": url}
    created_at: str = ""
    views: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "image_url": self.image_url,
            "title": self.title,
            "category": self.category,
            "tags": self.tags,
            "featured": self.featured,
            "privacy": self.privacy,
            "commission_example": self.commission_example,
            "commission_type": self.commission_type,
            "before_after": self.before_after,
            "created_at": self.created_at,
            "views": self.views,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortfolioEntry:
        privacy = data.get("privacy", "public")
        if privacy == "federation":
            privacy = "private"
        return cls(
            id=data.get("id", ""),
            user_id=data.get("user_id", 0),
            image_url=data.get("image_url", ""),
            title=data.get("title", ""),
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            featured=data.get("featured", False),
            privacy=privacy,
            commission_example=data.get("commission_example", False),
            commission_type=data.get("commission_type"),
            before_after=data.get("before_after"),
            created_at=data.get("created_at", ""),
            views=data.get("views", 0),
        )


@dataclass
class UserReport:
    """User report for moderation."""
    id: str
    reporter_id: int
    target_id: int
    target_message_id: int
    guild_id: int
    category: str  # harassment/scam_attempt/spam/nsfw_violation/impersonation/other
    priority: str = "normal"  # urgent/normal/low
    status: str = "open"  # open/assigned/resolved/dismissed
    assigned_mod_id: Optional[int] = None
    mod_thread_id: Optional[int] = None
    created_at: str = ""
    resolved_at: Optional[str] = None
    outcome: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reporter_id": self.reporter_id,
            "target_id": self.target_id,
            "target_message_id": self.target_message_id,
            "guild_id": self.guild_id,
            "category": self.category,
            "priority": self.priority,
            "status": self.status,
            "assigned_mod_id": self.assigned_mod_id,
            "mod_thread_id": self.mod_thread_id,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "outcome": self.outcome,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserReport:
        return cls(
            id=data.get("id", ""),
            reporter_id=data.get("reporter_id", 0),
            target_id=data.get("target_id", 0),
            target_message_id=data.get("target_message_id", 0),
            guild_id=data.get("guild_id", 0),
            category=data.get("category", "other"),
            priority=data.get("priority", "normal"),
            status=data.get("status", "open"),
            assigned_mod_id=data.get("assigned_mod_id"),
            mod_thread_id=data.get("mod_thread_id"),
            created_at=data.get("created_at", ""),
            resolved_at=data.get("resolved_at"),
            outcome=data.get("outcome"),
            notes=data.get("notes", []),
        )


@dataclass
class Vouch:
    """Vouch from one user to another."""
    id: str
    from_user_id: int
    to_user_id: int
    guild_id: int
    proof_type: str  # screenshot/payment_confirmation/mod_verified
    proof_url: str
    transaction_type: str = "commission"  # commission/trade/other
    created_at: str = ""
    mutual: bool = False
    verified_by_mod: Optional[int] = None
    verified_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_user_id": self.from_user_id,
            "to_user_id": self.to_user_id,
            "guild_id": self.guild_id,
            "proof_type": self.proof_type,
            "proof_url": self.proof_url,
            "transaction_type": self.transaction_type,
            "created_at": self.created_at,
            "mutual": self.mutual,
            "verified_by_mod": self.verified_by_mod,
            "verified_at": self.verified_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Vouch:
        return cls(
            id=data.get("id", ""),
            from_user_id=data.get("from_user_id", 0),
            to_user_id=data.get("to_user_id", 0),
            guild_id=data.get("guild_id", 0),
            proof_type=data.get("proof_type", "screenshot"),
            proof_url=data.get("proof_url", ""),
            transaction_type=data.get("transaction_type", "commission"),
            created_at=data.get("created_at", ""),
            mutual=data.get("mutual", False),
            verified_by_mod=data.get("verified_by_mod"),
            verified_at=data.get("verified_at"),
        )


@dataclass
class WaitlistEntry:
    """Waitlist entry for commission queue."""
    id: str
    artist_id: int
    client_id: int
    guild_id: int
    position: int
    notes: str = ""
    notify_on_open: bool = True
    notify_method: str = "dm"  # dm/channel
    created_at: str = ""
    notified_at: Optional[str] = None
    timeout_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artist_id": self.artist_id,
            "client_id": self.client_id,
            "guild_id": self.guild_id,
            "position": self.position,
            "notes": self.notes,
            "notify_on_open": self.notify_on_open,
            "notify_method": self.notify_method,
            "created_at": self.created_at,
            "notified_at": self.notified_at,
            "timeout_at": self.timeout_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WaitlistEntry:
        return cls(
            id=data.get("id", ""),
            artist_id=data.get("artist_id", 0),
            client_id=data.get("client_id", 0),
            guild_id=data.get("guild_id", 0),
            position=data.get("position", 0),
            notes=data.get("notes", ""),
            notify_on_open=data.get("notify_on_open", True),
            notify_method=data.get("notify_method", "dm"),
            created_at=data.get("created_at", ""),
            notified_at=data.get("notified_at"),
            timeout_at=data.get("timeout_at"),
        )


@dataclass
class Bookmark:
    """Bookmarked message for later retrieval."""
    id: str
    user_id: int
    guild_id: int
    channel_id: int
    message_id: int
    message_link: str
    note: str = ""
    created_at: str = ""
    deliver_at: Optional[str] = None  # For delayed delivery
    delivered: bool = False
    delivery_method: str = "dm"  # dm/channel
    notify_channel_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "message_link": self.message_link,
            "note": self.note,
            "created_at": self.created_at,
            "deliver_at": self.deliver_at,
            "delivered": self.delivered,
            "delivery_method": self.delivery_method,
            "notify_channel_id": self.notify_channel_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Bookmark:
        return cls(
            id=data.get("id", ""),
            user_id=data.get("user_id", 0),
            guild_id=data.get("guild_id", 0),
            channel_id=data.get("channel_id", 0),
            message_id=data.get("message_id", 0),
            message_link=data.get("message_link", ""),
            note=data.get("note", ""),
            created_at=data.get("created_at", ""),
            deliver_at=data.get("deliver_at"),
            delivered=data.get("delivered", False),
            delivery_method=data.get("delivery_method", "dm"),
            notify_channel_id=data.get("notify_channel_id"),
        )
