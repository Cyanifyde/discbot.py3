from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import discord

from classes.response_handlers import BaseResponder, ResponderInput
from core.io_utils import read_json, write_json_atomic
from core.paths import BASE_DIR
from core.utils import safe_int, sanitize_text

PROFILE_DIR = BASE_DIR / "profiles"
DEFAULT_PROFILE_PATH = PROFILE_DIR / "default.json"

DEFAULT_EMBED: Dict[str, Any] = {
    "title": "Artist Profile: {user}",
    "description": "{bio}",
    "color": 0x2B6CB0,
    "fields": [
        {"name": "Pronouns", "value": "{pronouns}", "inline": True},
        {"name": "Specialties", "value": "{specialties}", "inline": True},
        {"name": "Commission Status", "value": "{commission_status}", "inline": True},
        {"name": "Portfolio", "value": "{links}", "inline": False},
    ],
    "footer": {"text": "Use profile setbio / setpronouns / setspecialities / addlink"},
}
DEFAULT_COMMISSION_EMBED: Dict[str, Any] = {
    "title": "Commission Info: {user}",
    "color": 0x2B6CB0,
    "fields": [
        {"name": "Status", "value": "{commission_status}", "inline": True},
        {"name": "Info", "value": "{commission_info}", "inline": False},
    ],
}

MAX_LINKS = 20
LINKS_TEXT_LIMIT = 900
BIO_LIMIT = 800
PRONOUNS_LIMIT = 80
SPECIALTY_ITEM_LIMIT = 40
SPECIALTIES_MAX = 12
COMMISSION_STATUS_LIMIT = 80
COMMISSION_INFO_LIMIT = 1200

SPECIALTY_SPLIT_RE = re.compile(r"[,\n;/]+")
COLOR_HEX_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
EMBED_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x0c\x0e-\x1f\x7f]")


class ProfileResponder(BaseResponder):
    async def run(self, payload: ResponderInput) -> Any:
        command, rest = _split_command(payload.text or "")
        if not command:
            return _help_text()
        command = command.lower()

        if command in {"help", "h", "?"}:
            return _help_text()
        if command in {"view", "show"}:
            return await _handle_view(payload, rest)
        if command == "set":
            return await _handle_set(payload, rest)
        if command == "setbio":
            return await _handle_set_bio(payload, rest)
        if command == "setpronouns":
            return await _handle_set_pronouns(payload, rest)
        if command in {"setspecialities", "setspecialties"}:
            return await _handle_set_specialties(payload, rest)
        if command in {"commissionstatus", "commisionstatus"}:
            return await _handle_commission_status_command(payload, rest)
        if command in {"commissioninfo", "commisioninfo"}:
            return await _handle_commission_info_command(payload, rest)
        if command in {"bio"}:
            return await _handle_bio(payload, rest)
        if command in {"pronouns"}:
            return await _handle_pronouns(payload, rest)
        if command in {"specialities", "specialties"}:
            return await _handle_specialties(payload, rest)
        if command in {"links", "link"}:
            return await _handle_links(payload, rest)
        if command in {"add", "addlink"}:
            return await _handle_add_link(payload, rest)
        if command in {"remove", "removelink", "rm", "delete", "del"}:
            return await _handle_remove_link(payload, rest)
        if command in {"commission", "commision"}:
            return await _handle_commission(payload, rest)
        return _help_text()


def _split_command(text: str) -> Tuple[str, str]:
    text = text.strip()
    if not text:
        return "", ""
    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


async def _handle_set(payload: ResponderInput, rest: str) -> str:
    command, value = _split_command(rest)
    command = command.lower()
    if not command:
        return "Usage: profile set bio|pronouns|specialities <value>"
    if command in {"bio"}:
        return await _handle_set_bio(payload, value)
    if command in {"pronouns"}:
        return await _handle_set_pronouns(payload, value)
    if command in {"specialities", "specialties"}:
        return await _handle_set_specialties(payload, value)
    return "Usage: profile set bio|pronouns|specialities <value>"


async def _handle_commission_status_command(payload: ResponderInput, rest: str) -> str:
    subcommand, value = _split_command(rest)
    if subcommand.lower() == "set":
        return await _handle_set_commission_status(payload, value)
    return "Usage: profile commissionstatus set <text>"


async def _handle_commission_info_command(payload: ResponderInput, rest: str) -> str:
    subcommand, value = _split_command(rest)
    if subcommand.lower() == "set":
        return await _handle_set_commission_info(payload, value)
    return "Usage: profile commissioninfo set <text|embed-json>"


async def _handle_view(payload: ResponderInput, rest: str) -> Any:
    message = payload.message
    target = await _resolve_target(message, rest)
    record = await _load_record(target.id)
    if record is None:
        if target.id != message.author.id:
            name = _safe_name(target)
            return f"No profile found for {name}."
        record = await _load_default_record()
    record = _normalize_record(record)
    embed = _build_profile_embed(record, target)
    return {"embed": embed}


async def _handle_bio(payload: ResponderInput, rest: str) -> str:
    message = payload.message
    target = await _resolve_target(message, rest)
    record = await _load_record(target.id)
    if record is None:
        if target.id != message.author.id:
            return f"No profile found for {_safe_name(target)}."
        record = await _load_default_record()
    record = _normalize_record(record)
    bio = record.get("bio", "").strip()
    if not bio:
        return f"Bio not set for {_safe_name(target)}."
    return bio


async def _handle_pronouns(payload: ResponderInput, rest: str) -> str:
    message = payload.message
    target = await _resolve_target(message, rest)
    record = await _load_record(target.id)
    if record is None:
        if target.id != message.author.id:
            return f"No profile found for {_safe_name(target)}."
        record = await _load_default_record()
    record = _normalize_record(record)
    value = record.get("pronouns", "").strip()
    if not value:
        return f"Pronouns not set for {_safe_name(target)}."
    return value


async def _handle_specialties(payload: ResponderInput, rest: str) -> str:
    message = payload.message
    target = await _resolve_target(message, rest)
    record = await _load_record(target.id)
    if record is None:
        if target.id != message.author.id:
            return f"No profile found for {_safe_name(target)}."
        record = await _load_default_record()
    record = _normalize_record(record)
    specialties = record.get("specialties", [])
    if not specialties:
        return f"Specialties not set for {_safe_name(target)}."
    return ", ".join(specialties)


async def _handle_links(payload: ResponderInput, rest: str) -> str:
    message = payload.message
    target = await _resolve_target(message, rest)
    record = await _load_record(target.id)
    if record is None:
        if target.id != message.author.id:
            return f"No profile found for {_safe_name(target)}."
        record = await _load_default_record()
    record = _normalize_record(record)
    links = record.get("links", [])
    return _format_links(links, LINKS_TEXT_LIMIT) or "_No links yet._"


async def _handle_commission(payload: ResponderInput, rest: str) -> Any:
    rest = rest.strip()
    if rest.lower().startswith("info"):
        rest = rest[4:].strip()
    message = payload.message
    target = await _resolve_target(message, rest)
    record = await _load_record(target.id)
    if record is None:
        if target.id != message.author.id:
            return f"No profile found for {_safe_name(target)}."
        record = await _load_default_record()
    record = _normalize_record(record)
    embed = _build_commission_embed(record, target)
    return {"embed": embed}


async def get_commission_embed_for(
    viewer: discord.abc.User,
    target: Optional[discord.abc.User] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    resolved = target or viewer
    record = await _load_record(resolved.id)
    if record is None:
        if resolved.id != viewer.id:
            return None, f"No profile found for {_safe_name(resolved)}."
        record = await _load_default_record()
    record = _normalize_record(record)
    embed = _build_commission_embed(record, resolved)
    return embed, None


async def _handle_set_bio(payload: ResponderInput, rest: str) -> str:
    text = rest.strip()
    if not text:
        return "Usage: profile setbio <text>"
    record = await _load_or_default(payload.message.author.id)
    record["bio"] = _trim(text, BIO_LIMIT)
    await _save_record(payload.message.author.id, record)
    return "Bio updated."


async def _handle_set_pronouns(payload: ResponderInput, rest: str) -> str:
    text = rest.strip()
    if not text:
        return "Usage: profile setpronouns <text>"
    record = await _load_or_default(payload.message.author.id)
    record["pronouns"] = _trim(text, PRONOUNS_LIMIT)
    await _save_record(payload.message.author.id, record)
    return "Pronouns updated."


async def _handle_set_specialties(payload: ResponderInput, rest: str) -> str:
    text = rest.strip()
    if not text:
        return "Usage: profile setspecialities <list>"
    specialties = _parse_specialties(text)
    if not specialties:
        return "Usage: profile setspecialities <list>"
    record = await _load_or_default(payload.message.author.id)
    record["specialties"] = specialties[:SPECIALTIES_MAX]
    await _save_record(payload.message.author.id, record)
    return f"Specialties updated ({len(record['specialties'])})."


async def _handle_set_commission_status(payload: ResponderInput, rest: str) -> str:
    text = rest.strip()
    if not text:
        return "Usage: profile setcommissionstatus <text>"
    record = await _load_or_default(payload.message.author.id)
    record["commission_status"] = _trim(text, COMMISSION_STATUS_LIMIT)
    await _save_record(payload.message.author.id, record)
    return "Commission status updated."


async def _handle_set_commission_info(payload: ResponderInput, rest: str) -> str:
    raw = rest.strip()
    if not raw:
        return "Usage: profile setcommissioninfo <text|embed-json>"
    parsed = _parse_embed_json(raw)
    record = await _load_or_default(payload.message.author.id)
    if parsed is not None:
        normalized = _sanitize_embed(parsed)
        record["commission_embed"] = normalized if isinstance(normalized, dict) else parsed
        await _save_record(payload.message.author.id, record)
        return "Commission embed updated."
    record["commission_info"] = _trim(raw, COMMISSION_INFO_LIMIT)
    await _save_record(payload.message.author.id, record)
    return "Commission info updated."


async def _handle_add_link(payload: ResponderInput, rest: str) -> str:
    link, text = _parse_link_text(rest)
    if not link:
        return "Usage: profile addlink <link> [text]"
    record = await _load_or_default(payload.message.author.id)
    links = record.get("links", [])
    if len(links) >= MAX_LINKS:
        return f"Link limit reached ({MAX_LINKS}). Remove a link first."
    links.append({"link": link, "text": text})
    record["links"] = links
    await _save_record(payload.message.author.id, record)
    return f"Added link #{len(links)}."


async def _handle_remove_link(payload: ResponderInput, rest: str) -> str:
    index = safe_int(rest.strip())
    if index is None or index <= 0:
        return "Usage: profile removelink <line>"
    record = await _load_record(payload.message.author.id)
    if not record or not record.get("links"):
        return "Your links list is empty."
    record = _normalize_record(record)
    links = record["links"]
    if index > len(links):
        return f"Line {index} is out of range (1-{len(links)})."
    removed = links.pop(index - 1)
    await _save_record(payload.message.author.id, record)
    title = sanitize_text(removed.get("text") or removed.get("link") or "link")
    return f"Removed link {index}: {title}"


def _help_text() -> str:
    return (
        "Profile commands:\n"
        "profile view [@user]\n"
        "profile set bio|pronouns|specialities <value>\n"
        "profile setbio <text>\n"
        "profile setpronouns <text>\n"
        "profile setspecialities <list>\n"
        "profile commissionstatus set <text>\n"
        "profile commissioninfo set <text|embed-json>\n"
        "profile addlink <link> [text]\n"
        "profile removelink <line>\n"
        "profile commission [@user]\n"
        "profile commission info [@user]\n"
        "profile bio [@user]\n"
        "profile pronouns [@user]\n"
        "profile specialties [@user]\n"
        "profile links [@user]\n"
        "profile help"
    )


def _default_record() -> Dict[str, Any]:
    return {
        "bio": "",
        "pronouns": "",
        "specialties": [],
        "links": [],
        "commission_status": "",
        "commission_info": "",
        "commission_embed": None,
        "embed": dict(DEFAULT_EMBED),
    }


async def _load_or_default(user_id: int) -> Dict[str, Any]:
    record = await _load_record(user_id)
    if record is None:
        record = await _load_default_record()
    return _normalize_record(record)


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    bio = record.get("bio") if isinstance(record.get("bio"), str) else ""
    pronouns = record.get("pronouns") if isinstance(record.get("pronouns"), str) else ""
    specialties_raw = record.get("specialties")
    specialties: List[str] = []
    if isinstance(specialties_raw, str):
        specialties = _parse_specialties(specialties_raw)
    elif isinstance(specialties_raw, list):
        for item in specialties_raw:
            if isinstance(item, str) and item.strip():
                specialties.append(_trim(item, SPECIALTY_ITEM_LIMIT))
    links_raw = record.get("links")
    links: List[Dict[str, str]] = []
    if isinstance(links_raw, list):
        for item in links_raw:
            if not isinstance(item, dict):
                continue
            link = item.get("link")
            if not isinstance(link, str) or not link.strip():
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                text = link
            links.append({"link": link.strip(), "text": _trim(text.strip(), 200)})
    commission_status = (
        record.get("commission_status") if isinstance(record.get("commission_status"), str) else ""
    )
    commission_info = (
        record.get("commission_info") if isinstance(record.get("commission_info"), str) else ""
    )
    commission_embed = record.get("commission_embed")
    if not isinstance(commission_embed, dict):
        commission_embed = None
    embed = record.get("embed")
    if not isinstance(embed, dict):
        embed = dict(DEFAULT_EMBED)
    return {
        "bio": _trim(bio, BIO_LIMIT),
        "pronouns": _trim(pronouns, PRONOUNS_LIMIT),
        "specialties": specialties[:SPECIALTIES_MAX],
        "links": links[:MAX_LINKS],
        "commission_status": _trim(commission_status, COMMISSION_STATUS_LIMIT),
        "commission_info": _trim(commission_info, COMMISSION_INFO_LIMIT),
        "commission_embed": commission_embed,
        "embed": embed,
    }


def _parse_specialties(text: str) -> List[str]:
    parts = [part.strip() for part in SPECIALTY_SPLIT_RE.split(text) if part.strip()]
    if not parts:
        return []
    return [_trim(part, SPECIALTY_ITEM_LIMIT) for part in parts]


def _parse_link_text(rest: str) -> Tuple[str, str]:
    rest = rest.strip()
    if not rest:
        return "", ""
    parts = rest.split(maxsplit=1)
    link = _clean_link(parts[0])
    text = ""
    if len(parts) > 1:
        text = parts[1].strip()
    if not text:
        text = link
    return link, text


def _clean_link(link: str) -> str:
    link = link.strip()
    if link.startswith("<") and link.endswith(">"):
        link = link[1:-1].strip()
    return link


def _parse_embed_json(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            cleaned = "\n".join(lines[1:-1]).strip()
        elif len(lines) > 1:
            cleaned = "\n".join(lines[1:]).strip()
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


async def _resolve_target(message: discord.Message, rest: str) -> discord.abc.User:
    if message.mentions:
        return message.mentions[0]
    maybe_id = ""
    if rest:
        maybe_id = rest.split(maxsplit=1)[0]
    user_id = safe_int(maybe_id)
    if user_id and message.guild is not None:
        member = message.guild.get_member(user_id)
        if member:
            return member
        try:
            fetched = await message.guild.fetch_member(user_id)
        except Exception:
            fetched = None
        if fetched:
            return fetched
    return message.author


def _safe_name(user: discord.abc.User) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", None) or "User"
    return sanitize_text(name, max_len=64)


def _format_links(links: List[Dict[str, str]], limit: int) -> str:
    if not links:
        return "_No links yet._"
    lines: List[str] = []
    for idx, item in enumerate(links, start=1):
        text = sanitize_text(item.get("text") or item.get("link") or "Link", max_len=200)
        link = item.get("link", "").strip()
        if link:
            line = f"{idx}. [{text}]({link})"
        else:
            line = f"{idx}. {text}"
        lines.append(line)
    return _truncate_lines(lines, limit)


def _truncate_lines(lines: List[str], limit: int) -> str:
    output: List[str] = []
    total = 0
    for line in lines:
        extra = len(line) + (1 if output else 0)
        if total + extra > limit:
            output.append("...")
            break
        output.append(line)
        total += extra
    return "\n".join(output) if output else ""


def _render_placeholders(value: Any, placeholders: Dict[str, str]) -> Any:
    if isinstance(value, str):
        rendered = value
        for key, replacement in placeholders.items():
            rendered = rendered.replace(f"{{{key}}}", replacement)
        return rendered
    if isinstance(value, dict):
        return {k: _render_placeholders(v, placeholders) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_placeholders(item, placeholders) for item in value]
    return value


def _build_profile_embed(record: Dict[str, Any], target: discord.abc.User) -> Dict[str, Any]:
    placeholders = _profile_placeholders(record, target, include_commission_info=False)
    template = record.get("embed")
    if not isinstance(template, dict):
        template = dict(DEFAULT_EMBED)
    rendered = _render_placeholders(template, placeholders)
    return _sanitize_embed(rendered)


def _build_commission_embed(record: Dict[str, Any], target: discord.abc.User) -> Dict[str, Any]:
    template = record.get("commission_embed")
    allow_auto_fields = False
    if not isinstance(template, dict):
        template = dict(DEFAULT_COMMISSION_EMBED)
        allow_auto_fields = True
    placeholders = _profile_placeholders(record, target, include_commission_info=True)
    rendered = _render_placeholders(template, placeholders)
    if allow_auto_fields:
        has_status = _template_has_placeholder(template, "commission_status")
        has_info = _template_has_placeholder(template, "commission_info")
        if not has_status or not has_info:
            existing_fields = rendered.get("fields")
            fields: List[Dict[str, Any]] = []
            if isinstance(existing_fields, list):
                for item in existing_fields:
                    if isinstance(item, dict):
                        fields.append(dict(item))
            has_status_field = _fields_have_label(fields, ["status"])
            has_info_field = _fields_have_label(fields, ["info"])
            if not has_status and not has_status_field:
                fields.append(
                    {"name": "Status", "value": placeholders["commission_status"], "inline": True}
                )
            if not has_info and not has_info_field:
                fields.append(
                    {"name": "Info", "value": placeholders["commission_info"], "inline": False}
                )
            rendered["fields"] = fields
    return _sanitize_embed(rendered)


def _profile_placeholders(
    record: Dict[str, Any],
    target: discord.abc.User,
    include_commission_info: bool,
) -> Dict[str, str]:
    bio = _clean_text(record.get("bio", ""), BIO_LIMIT, "_No bio set._")
    pronouns = _clean_text(record.get("pronouns", ""), PRONOUNS_LIMIT, "_Not set._")
    specialties = record.get("specialties", [])
    if specialties:
        specialties_text = ", ".join(specialties)
    else:
        specialties_text = "_Not set._"
    links = _format_links(record.get("links", []), LINKS_TEXT_LIMIT)
    commission_status = _clean_text(record.get("commission_status", ""), COMMISSION_STATUS_LIMIT, "_Not set._")
    if include_commission_info:
        commission_info = _clean_text(
            record.get("commission_info", ""), COMMISSION_INFO_LIMIT, "_Not set._"
        )
    else:
        commission_info = "_Use profile commission._"
    return {
        "user": _safe_name(target),
        "bio": bio,
        "pronouns": pronouns,
        "specialties": specialties_text,
        "links": links,
        "commission_status": commission_status,
        "commission_info": commission_info,
    }


def _template_has_placeholder(value: Any, placeholder: str) -> bool:
    needle = f"{{{placeholder}}}"
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_template_has_placeholder(v, placeholder) for v in value.values())
    if isinstance(value, list):
        return any(_template_has_placeholder(item, placeholder) for item in value)
    return False


def _fields_have_label(fields: List[Dict[str, Any]], labels: List[str]) -> bool:
    if not fields or not labels:
        return False
    needles = [label.lower() for label in labels]
    for field in fields:
        name = field.get("name")
        if not isinstance(name, str):
            continue
        lowered = name.lower()
        for needle in needles:
            if needle in lowered:
                return True
    return False


def _clean_text(value: str, limit: int, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if not text:
        return fallback
    return _trim(text, limit)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _coerce_color(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return value
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lower().startswith("0x"):
            text = text[2:]
        if COLOR_HEX_RE.fullmatch(text):
            if text.startswith("#"):
                text = text[1:]
            if len(text) == 3:
                text = "".join(char * 2 for char in text)
            try:
                return int(text, 16)
            except Exception:
                return None
        if text.isdigit():
            try:
                number = int(text)
            except Exception:
                return None
            if 0 <= number <= 0xFFFFFF:
                return number
            return None
    return None


def _timestamp_to_iso(value: float) -> Optional[str]:
    try:
        seconds = float(value)
    except Exception:
        return None
    if seconds < 0:
        return None
    if seconds > 1e11:
        seconds = seconds / 1000.0
    try:
        stamp = dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
    except Exception:
        return None
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_timestamp(value: Any) -> Optional[str]:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        return _timestamp_to_iso(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _timestamp_to_iso(int(text))
        try:
            number = float(text)
        except Exception:
            return text
        return _timestamp_to_iso(number)
    return None


def _sanitize_embed_text(text: Any, max_len: int = 4096) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = EMBED_CONTROL_RE.sub("", text)
    text = text.replace("@", "@\u200b")
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def _normalize_embed_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    color_value = value.get("color")
    if color_value is not None:
        coerced = _coerce_color(color_value)
        if coerced is None:
            value.pop("color", None)
        else:
            value["color"] = coerced
    colour_value = value.get("colour")
    if colour_value is not None:
        coerced = _coerce_color(colour_value)
        if coerced is None:
            value.pop("colour", None)
        else:
            value["colour"] = coerced
    ts_value = value.get("timestamp")
    if ts_value is not None:
        coerced = _coerce_timestamp(ts_value)
        if coerced is None:
            value.pop("timestamp", None)
        else:
            value["timestamp"] = coerced
    return value


def _sanitize_embed(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_embed_text(value, max_len=4096)
    if isinstance(value, dict):
        sanitized = {k: _sanitize_embed(v) for k, v in value.items()}
        return _normalize_embed_dict(sanitized)
    if isinstance(value, list):
        return [_sanitize_embed(item) for item in value]
    return value


async def _load_record(user_id: int) -> Optional[Dict[str, Any]]:
    path = PROFILE_DIR / f"{user_id}.json"
    data = await read_json(path, default=None)
    if not isinstance(data, dict):
        return None
    return data


async def _load_default_record() -> Dict[str, Any]:
    data = await read_json(DEFAULT_PROFILE_PATH, default=None)
    if isinstance(data, dict):
        return _normalize_record(data)
    return _default_record()


async def _save_record(user_id: int, record: Dict[str, Any]) -> None:
    path = PROFILE_DIR / f"{user_id}.json"
    await write_json_atomic(path, record)
