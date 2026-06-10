from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from time_utils import to_utc_z


def extract_email(text: str) -> str | None:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match else None


def extract_uid(text: str) -> str | None:
    blocked = {"schedule", "reschedule", "tomorrow", "meeting", "bookings", "booking"}

    explicit = re.search(
        r"\b(?:booking\s+)?uid\s*:?\s*([A-Za-z0-9_-]{10,})\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        candidate = explicit.group(1)
        if candidate.lower() not in blocked:
            return candidate

    explicit = re.search(
        r"\bbooking\s+(?:uid\s+)?([A-Za-z0-9_-]{10,})\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        candidate = explicit.group(1)
        if candidate.lower() not in blocked:
            return candidate

    for match in re.finditer(r"\b([A-Za-z0-9_-]{15,})\b", text):
        candidate = match.group(1)
        if candidate.lower() not in blocked and "@" not in candidate:
            return candidate
    return None


def looks_like_uid_only(text: str, uid: str) -> bool:
    cleaned = re.sub(r"[`'\"]", "", text.strip())
    cleaned = re.sub(r"^(?:uid|booking)\s*:?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned == uid


def looks_like_book_intent(text: str) -> bool:
    lower = text.lower().strip()
    if lower in {"book", "booking", "schedule"}:
        return True
    if is_reschedule_intent(lower) or re.search(r"\b(cancel|delete)\b", lower):
        return False
    return bool(
        re.search(r"\b(book|schedule)\b", lower)
        and re.search(r"\b(meeting|call|slot|intro|appointment|time)\b", lower)
    )


def looks_like_list_intent(text: str) -> bool:
    lower = text.lower()
    return bool(
        re.search(r"\b(check|show|view|see|list|what's on|whats on)\b", lower)
        and re.search(
            r"\b(meeting|meetings|calendar|booking|bookings|schedule|event|events|upcoming)\b",
            lower,
        )
    )


def is_reschedule_intent(lower: str) -> bool:
    return bool(
        re.search(r"\breschedule\b", lower)
        or re.search(r"res\w*dule", lower)
        or re.search(r"\bmove\b", lower)
    )


def looks_like_booking_time(text: str, iso_start: str | None) -> bool:
    if not iso_start:
        return False
    lower = text.lower()
    return bool(
        re.search(r"\bat\s+\d", lower)
        or re.search(r"\b\d{1,2}:\d{2}\s*(?:am|pm)\b", lower)
        or re.search(r"\b\d{1,2}\s*(?:am|pm)\b", lower)
        or re.search(r"\b\d{4}-\d{2}-\d{2}\b", lower)
        or re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text)
        or re.search(r"\b(book|schedule)\b", lower)
    )


def extract_datetime_as_utc(text: str, tz_name: str) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", text)
    if match:
        value = match.group(0)
        if value.endswith("Z"):
            return value
        dt = datetime.fromisoformat(value)
        return to_utc_z(dt.isoformat())

    if not re.search(r"\d", text):
        return None

    local_tz = ZoneInfo(tz_name)
    cleaned = re.sub(r"^(?:book|schedule)\s+", "", text.strip(), flags=re.IGNORECASE)
    try:
        dt = date_parser.parse(
            cleaned,
            fuzzy=True,
            default=datetime.now(local_tz).replace(hour=9, minute=0, second=0, microsecond=0),
        )
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return to_utc_z(dt.isoformat())
