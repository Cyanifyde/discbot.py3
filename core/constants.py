"""
Configuration key constants.

Using constants instead of string literals provides:
- IDE autocomplete
- Typo protection (caught at import time)
- Single source of truth for key names
"""
from __future__ import annotations


class ConfigKey:
    """All configuration keys used in guild configs."""
    
    # Identity
    GUILD_ID = "guild_id"
    TOKEN = "token"
    
    # Roles
    UNVERIFIED_ROLE_ID = "unverified_role_id"
    EXEMPT_ROLE_IDS = "exempt_role_ids"
    
    # Channels
    ACTION_LOG_CHANNEL_ID = "action_log_channel_id"
    EXCLUDED_CHANNEL_IDS = "excluded_channel_ids"
    IGNORED_CHANNEL_IDS = "ignored_channel_ids"
    
    # User exemptions
    EXEMPTIONS = "exemptions"
    
    # Limits
    MAX_IMAGE_BYTES = "max_image_bytes"
    
    # Timing thresholds
    FIRST_RUN_GRACE_DAYS = "first_run_grace_days"
    INACTIVE_DAYS_THRESHOLD = "inactive_days_threshold"
    INACTIVITY_MESSAGE_THRESHOLD = "inactivity_message_threshold"
    
    # Batch processing
    SNAPSHOT_MEMBERS_PER_RUN = "snapshot_members_per_run"
    ENFORCEMENT_SCAN_MAX_USERS_PER_RUN = "enforcement_scan_max_users_per_run"
    
    # Queue settings
    QUEUE_MAX_JOBS = "queue_max_jobs"
    QUEUE_COMPACT_THRESHOLD_BYTES = "queue_compact_threshold_bytes"
    QUEUE_FLUSH_INTERVAL_SECONDS = "queue_flush_interval_seconds"
    QUEUE_STATE_FLUSH_INTERVAL_SECONDS = "queue_state_flush_interval_seconds"
    ENFORCEMENT_INTERVAL_SECONDS = "enforcement_interval_seconds"
    
    # Workers
    WORKER_COUNT = "worker_count"
    WORKER_JOB_TIMEOUT_SECONDS = "worker_job_timeout_seconds"
    
    # Hash checking
    HASHES_FILES = "hashes_files"
    EXTRA_HASHES = "extra_hashes"
    
    # Feature flags
    ENABLE_DISCORD_CDN_URL_SCAN = "enable_discord_cdn_url_scan"
    ENABLE_DISCORD_MESSAGE_LINK_SCAN = "enable_discord_message_link_scan"
    
    # CDN settings
    ALLOWED_DISCORD_CDN_DOMAINS = "allowed_discord_cdn_domains"


class JobSource:
    """Source types for scan jobs."""
    ATTACHMENT = "attachment"
    DISCORD_CDN_URL = "discord_cdn_url"
    DISCORD_MESSAGE_LINK = "discord_message_link"


class MatchMode:
    """Matching modes for auto-responder triggers."""
    STARTSWITH = "startswith"
    EQUALS = "equals"
    CONTAINS = "contains"
    REGEX = "regex"


# Shorthand alias for cleaner imports
K = ConfigKey
