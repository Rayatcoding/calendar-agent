from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def to_utc_z(value: str) -> str:
    """Convert any ISO 8601 timestamp to UTC with a trailing Z."""
    normalized = value.strip().replace(".000Z", "Z").replace("+00:00", "Z")
    if normalized.endswith("Z"):
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            raise ValueError(f"Timestamp missing timezone: {value}")
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_start_key(value: str) -> str:
    return to_utc_z(value)[:16]


def format_local_time(value: str, tz_name: str) -> str:
    dt = datetime.fromisoformat(to_utc_z(value).replace("Z", "+00:00"))
    local = dt.astimezone(ZoneInfo(tz_name))
    hour = local.strftime("%I").lstrip("0") or "12"
    minute = local.strftime("%M")
    suffix = local.strftime("%p")
    return f"{local.strftime('%a %b %d, %Y')} at {hour}:{minute} {suffix}"
