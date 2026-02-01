"""
Job factory - creates scan jobs from Discord messages.

Centralizes job creation logic that was previously duplicated in bot.py.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import discord

from core.constants import K, JobSource
from core.types import AttachmentInfo, LinkedMessage, ScanJob
from core.utils import dt_to_iso, utcnow


class JobFactory:
    """
    Factory for creating scan jobs from Discord messages.
    
    Provides a clean interface for job creation with proper typing.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._max_image_bytes = int(config.get(K.MAX_IMAGE_BYTES, 0))
        self._enable_cdn_scan = bool(config.get(K.ENABLE_DISCORD_CDN_URL_SCAN, False))
        self._enable_link_scan = bool(config.get(K.ENABLE_DISCORD_MESSAGE_LINK_SCAN, False))
        self._cdn_regex = self._build_cdn_regex(
            config.get(K.ALLOWED_DISCORD_CDN_DOMAINS, [])
        )
        self._guild_id = int(config.get(K.GUILD_ID, 0))

    def update_config(self, config: dict[str, Any]) -> None:
        """Update configuration."""
        self.config = config
        self._max_image_bytes = int(config.get(K.MAX_IMAGE_BYTES, 0))
        self._enable_cdn_scan = bool(config.get(K.ENABLE_DISCORD_CDN_URL_SCAN, False))
        self._enable_link_scan = bool(config.get(K.ENABLE_DISCORD_MESSAGE_LINK_SCAN, False))
        self._cdn_regex = self._build_cdn_regex(
            config.get(K.ALLOWED_DISCORD_CDN_DOMAINS, [])
        )
        self._guild_id = int(config.get(K.GUILD_ID, 0))

    @staticmethod
    def _build_cdn_regex(allowed_domains: list[str]) -> re.Pattern[str]:
        """Build regex for matching Discord CDN URLs."""
        domains = [re.escape(domain) for domain in allowed_domains]
        if not domains:
            domains = [r"cdn\.discordapp\.com", r"media\.discordapp\.net"]
        pattern = r"https://(?:" + "|".join(domains) + r")/[^\s>]+"
        return re.compile(pattern, re.IGNORECASE)

    @staticmethod
    def _create_base(message: discord.Message, source: str) -> ScanJob:
        """Create a base job with common fields."""
        return ScanJob(
            enqueued_at=dt_to_iso(utcnow()) or "",
            guild_id=str(message.guild.id) if message.guild else "",
            channel_id=str(message.channel.id),
            message_id=str(message.id),
            author_id=str(message.author.id),
            source=source,
        )

    def from_attachment(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
    ) -> Optional[ScanJob]:
        """Create a job from a message attachment."""
        if attachment.size and attachment.size > self._max_image_bytes:
            return None
        
        job = self._create_base(message, JobSource.ATTACHMENT)
        job.attachment = AttachmentInfo(
            url=attachment.url,
            filename=attachment.filename,
            size=attachment.size or 0,
            content_type=attachment.content_type,
        )
        return job

    def from_cdn_url(self, message: discord.Message, url: str) -> ScanJob:
        """Create a job from a Discord CDN URL."""
        job = self._create_base(message, JobSource.DISCORD_CDN_URL)
        job.url = url
        return job

    def from_message_link(
        self,
        message: discord.Message,
        linked_guild_id: str,
        linked_channel_id: str,
        linked_message_id: str,
    ) -> ScanJob:
        """Create a job from a Discord message link."""
        job = self._create_base(message, JobSource.DISCORD_MESSAGE_LINK)
        job.linked = LinkedMessage(
            guild_id=linked_guild_id,
            channel_id=linked_channel_id,
            message_id=linked_message_id,
        )
        return job

    def extract_cdn_url(self, content: str) -> Optional[str]:
        """Extract first Discord CDN URL from content."""
        if not content:
            return None
        match = self._cdn_regex.search(content)
        return match.group(0) if match else None

    def extract_message_link(self, content: str) -> Optional[tuple[str, str, str]]:
        """Extract first message link pointing to current guild."""
        if not content:
            return None
        
        pattern = re.compile(
            r"https://(?:discord\.com|discordapp\.com)/channels/(\d+)/(\d+)/(\d+)",
            re.IGNORECASE,
        )
        
        for match in pattern.finditer(content):
            if int(match.group(1)) == self._guild_id:
                return match.group(1), match.group(2), match.group(3)
        return None

    def build_job_for_message(self, message: discord.Message) -> Optional[ScanJob]:
        """
        Build a scan job for a message if applicable.
        
        Checks in order:
        1. Attachments
        2. CDN URLs (if enabled)
        3. Message links (if enabled)
        """
        # Check attachments first
        if message.attachments:
            attachment = message.attachments[0]
            return self.from_attachment(message, attachment)
        
        content = message.content or ""
        
        # Check CDN URLs
        if self._enable_cdn_scan:
            url = self.extract_cdn_url(content)
            if url:
                return self.from_cdn_url(message, url)
        
        # Check message links
        if self._enable_link_scan:
            linked = self.extract_message_link(content)
            if linked:
                return self.from_message_link(message, *linked)
        
        return None
