from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Optional

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
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


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
    haystack = data[:512]
    signatures = [
        b"\x89PNG\r\n\x1a\n",
        b"\xFF\xD8\xFF",
        b"GIF87a",
        b"GIF89a",
        b"BM",  # BMP
        b"II*\x00",  # TIFF (LE)
        b"MM\x00*",  # TIFF (BE)
        b"\x00\x00\x01\x00",  # ICO
    ]
    for sig in signatures:
        if sig in haystack:
            return True
    # Looser WEBP check: both markers appear somewhere in the header window.
    if b"RIFF" in haystack and b"WEBP" in haystack:
        return True
    return False


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
