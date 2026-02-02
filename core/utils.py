"""
General utility functions.

Provides date/time helpers, text sanitization, validation functions, and regex utilities.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc

CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

MESSAGE_LINK_RE = re.compile(
    r"https://(?:discord\\.com|discordapp\\.com)/channels/(\\d+)/(\\d+)/(\\d+)",
    re.IGNORECASE,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def dt_to_iso(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    value = value.astimezone(UTC).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def iso_to_dt(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def sanitize_text(text: Any, max_len: int = 1500) -> str:
    if text is None:
        return ""
    text = str(text)
    text = CONTROL_RE.sub("", text)
    text = text.replace("@", "@\u200b")
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_valid_id(value: Any) -> bool:
    return is_int(value) and 1 <= value <= 2**63 - 1


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            try:
                return int(stripped)
            except Exception:
                return default
    return default


def is_sha256_hex(value: str) -> bool:
    return bool(SHA256_RE.fullmatch(value))


def is_safe_relative_path(path_str: str) -> bool:
    try:
        path = Path(path_str)
    except Exception:
        return False
    if path.is_absolute():
        return False
    if path.drive:
        return False
    if ".." in path.parts:
        return False
    return True


def build_cdn_regex(allowed_domains: Iterable[str]) -> re.Pattern[str]:
    domains = [re.escape(domain) for domain in allowed_domains]
    if not domains:
        domains = ["cdn\\.discordapp\\.com", "media\\.discordapp\\.net"]
    pattern = r"https://(?:" + "|".join(domains) + r")/[^\s>]+"
    return re.compile(pattern, re.IGNORECASE)


def extract_first_cdn_url(content: str, cdn_regex: re.Pattern[str]) -> Optional[str]:
    if not content:
        return None
    match = cdn_regex.search(content)
    if not match:
        return None
    return match.group(0)


def extract_first_message_link(content: str, guild_id: int) -> Optional[Tuple[str, str, str]]:
    if not content:
        return None
    for match in MESSAGE_LINK_RE.finditer(content):
        if int(match.group(1)) == guild_id:
            return match.group(1), match.group(2), match.group(3)
    return None


def magic_bytes_valid(data: bytes) -> bool:
    if len(data) < 12:
        return False
    # Allow optional UTF-8 BOM before PNG signature.
    if data.startswith(b"\xEF\xBB\xBF"):
        data = data[3:]
    
    # Check signatures at correct offsets (use startswith to avoid false positives)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return True
    if data.startswith(b"\xFF\xD8\xFF"):  # JPEG
        return True
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):  # GIF
        return True
    if data.startswith(b"BM"):  # BMP
        return True
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):  # TIFF
        return True
    if data.startswith(b"\x00\x00\x01\x00"):  # ICO
        return True
    
    # WEBP: RIFF header at start, WEBP signature at offset 8
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    
    return False


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─── Enhanced Utility Functions ───────────────────────────────────────────


def calculate_trust_tier(score: float) -> str:
    """
    Calculate trust tier from score.

    Tiers:
    - 0-20: untrusted
    - 21-50: neutral
    - 51-80: trusted
    - 81-100: highly_trusted
    """
    if score < 0:
        score = 0
    elif score > 100:
        score = 100

    if score <= 20:
        return "untrusted"
    elif score <= 50:
        return "neutral"
    elif score <= 80:
        return "trusted"
    else:
        return "highly_trusted"


def apply_decay(score: float, days: int, multiplier: float = 1.0) -> float:
    """
    Apply time-based decay to a score.

    Args:
        score: Original score
        days: Number of days elapsed
        multiplier: Decay rate multiplier (default 1.0)

    Returns:
        Decayed score (minimum 0)
    """
    decay_amount = days * multiplier / 100  # 1 point per day at 1.0 multiplier
    decayed = score - decay_amount
    return max(0.0, decayed)


def parse_deadline(text: str) -> Optional[dt.datetime]:
    """
    Parse deadline text into datetime.

    Supports formats:
    - ISO dates: "2026-03-15"
    - Relative: "3d", "2w", "1mo"
    - Human readable: "March 15", "in 3 days"
    """
    text = text.strip()

    # Try ISO format first
    try:
        return dt.datetime.fromisoformat(text)
    except (ValueError, AttributeError):
        pass

    # Try parsing as relative duration
    duration = parse_duration_extended(text)
    if duration:
        return utcnow() + duration

    # Try common date formats
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except Exception:
            continue

    return None


def format_commission_status(commission: dict) -> str:
    """
    Format commission status for display.

    Returns a human-readable status string.
    """
    stage = commission.get("stage", "Unknown")
    payment_status = commission.get("payment_status", "pending")

    stage_emoji = {
        "Inquiry": "",
        "Accepted": "",
        "Queued": "",
        "In Progress": "",
        "WIP Shared": "",
        "Revision": "",
        "Final Delivered": "",
        "Completed": "",
        "Archived": "",
    }

    payment_emoji = {
        "pending": "",
        "partial": "",
        "paid": "",
    }

    emoji = stage_emoji.get(stage, "")
    pay_emoji = payment_emoji.get(payment_status, "")

    return f"{emoji} {stage} {pay_emoji}".strip()


def check_tier_permission(tier: str, action: str) -> bool:
    """
    Check if a trust tier has permission for an action.

    Actions and required tiers:
    - cross_server_sync: trusted (51+)
    - vouch_others: trusted (60+)
    - mediate_disputes: highly_trusted (80+)
    """
    tier_scores = {
        "untrusted": 0,
        "neutral": 35,
        "trusted": 65,
        "highly_trusted": 90,
    }

    action_requirements = {
        "cross_server_sync": 50,
        "vouch_others": 60,
        "mediate_disputes": 80,
    }

    user_score = tier_scores.get(tier, 0)
    required_score = action_requirements.get(action, 0)

    return user_score >= required_score


def is_within_quiet_hours(user_prefs: dict, now: Optional[dt.datetime] = None) -> bool:
    """
    Check if current time is within user's quiet hours.

    Args:
        user_prefs: User profile dict with quiet_hours settings
        now: Current datetime (defaults to utcnow())

    Returns:
        True if within quiet hours, False otherwise
    """
    if now is None:
        now = utcnow()

    quiet_hours = user_prefs.get("quiet_hours", {})
    if not quiet_hours.get("enabled", False):
        return False

    start_str = quiet_hours.get("start", "22:00")
    end_str = quiet_hours.get("end", "08:00")
    tz = quiet_hours.get("timezone") or user_prefs.get("timezone")

    # Parse times
    try:
        start_hour, start_min = map(int, start_str.split(":"))
        end_hour, end_min = map(int, end_str.split(":"))
    except Exception:
        return False

    if tz:
        try:
            now = now.astimezone(ZoneInfo(str(tz)))
        except ZoneInfoNotFoundError:
            pass
        except Exception:
            pass
    current_time = now.time()

    start_time = dt.time(start_hour, start_min)
    end_time = dt.time(end_hour, end_min)

    # Handle overnight quiet hours (e.g., 22:00 - 08:00)
    if start_time > end_time:
        return current_time >= start_time or current_time <= end_time
    else:
        return start_time <= current_time <= end_time


def parse_duration_extended(text: str) -> Optional[dt.timedelta]:
    """
    Parse duration string with extended support.

    Supports:
    - Days: "3d", "5 days"
    - Weeks: "2w", "1 week"
    - Months: "1mo", "2 months" (assumes 30 days)
    - Hours: "12h", "5 hours"
    - Minutes: "30m", "45 minutes"
    - Combined: "1w3d", "2mo1w"

    Returns:
        timedelta if valid, None otherwise
    """
    text = text.strip().lower()

    # Pattern for extended duration
    pattern = re.compile(
        r"(?:(\d+)\s*(?:mo|month|months))?"  # months
        r"(?:(\d+)\s*(?:w|week|weeks))?"     # weeks
        r"(?:(\d+)\s*(?:d|day|days))?"       # days
        r"(?:(\d+)\s*(?:h|hour|hours))?"     # hours
        r"(?:(\d+)\s*(?:m|min|minute|minutes))?"  # minutes
        r"(?:(\d+)\s*(?:s|sec|second|seconds))?",  # seconds
        re.IGNORECASE
    )

    match = pattern.fullmatch(text)
    if not match:
        return None

    months = int(match.group(1) or 0)
    weeks = int(match.group(2) or 0)
    days = int(match.group(3) or 0)
    hours = int(match.group(4) or 0)
    minutes = int(match.group(5) or 0)
    seconds = int(match.group(6) or 0)

    if all(x == 0 for x in [months, weeks, days, hours, minutes, seconds]):
        return None

    # Convert months to days (approximate)
    total_days = days + (weeks * 7) + (months * 30)

    return dt.timedelta(
        days=total_days,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )
