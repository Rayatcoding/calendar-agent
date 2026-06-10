from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from time_utils import format_local_time, normalize_start_key, to_utc_z
from models import BookingSummary, ParsedCommand


class BookingResolver:
    """Resolve booking UIDs from natural-language time references."""

    def __init__(self, tz_name: str):
        self.tz_name = tz_name

    def match_uid_from_list(
        self,
        bookings: list[BookingSummary],
        target_start: str,
    ) -> str | None:
        active = [b for b in bookings if b.status not in {"cancelled", "rejected"}]
        target_key = normalize_start_key(target_start)
        for booking in active:
            if booking.start and normalize_start_key(booking.start) == target_key:
                return booking.uid

        target_day = self._local_date_key(target_start)
        same_day = [
            booking
            for booking in active
            if booking.start and self._local_date_key(booking.start) == target_day
        ]
        if not same_day:
            return None

        target_minutes = self._local_minutes(target_start)
        ranked = sorted(
            same_day,
            key=lambda booking: abs(self._local_minutes(booking.start or "") - target_minutes),
        )
        if ranked and abs(self._local_minutes(ranked[0].start or "") - target_minutes) <= 45:
            return ranked[0].uid
        return None

    def bookings_on_same_local_day(
        self,
        bookings: list[BookingSummary],
        target_start: str,
    ) -> list[BookingSummary]:
        active = [b for b in bookings if b.status not in {"cancelled", "rejected"}]
        target_day = self.local_hour_key(target_start).split("T", 1)[0]
        return [
            booking
            for booking in active
            if booking.start and self.local_hour_key(booking.start).startswith(target_day)
        ]

    def resolve_reference(
        self,
        cmd: ParsedCommand,
        upcoming: list[BookingSummary],
    ) -> tuple[str | None, str | None]:
        if cmd.booking_uid:
            return cmd.booking_uid, None

        if cmd.start:
            uid = self.match_uid_from_list(upcoming, cmd.start)
            if uid:
                return uid, None
            same_day = self.bookings_on_same_local_day(upcoming, cmd.start)
            if len(same_day) == 1:
                return same_day[0].uid, None
            if len(same_day) > 1:
                return None, self.format_disambiguation_prompt(same_day, "cancel or reschedule")

        if len(upcoming) == 1:
            return upcoming[0].uid, None

        if upcoming:
            return None, self.format_disambiguation_prompt(upcoming, "cancel or reschedule")

        return None, None

    def format_disambiguation_prompt(
        self,
        bookings: list[BookingSummary],
        action: str,
    ) -> str:
        lines = [f"I found multiple upcoming meetings. Which one should I {action}?"]
        for booking in bookings[:5]:
            local_time = (
                format_local_time(booking.start, self.tz_name) if booking.start else "unknown time"
            )
            lines.append(f"- {local_time} (UID: `{booking.uid}`)")
        lines.append("\nReply with the time, e.g. `cancel my 10am meeting on June 12`.")
        return "\n".join(lines)

    def local_hour_key(self, value: str) -> str:
        return f"{self._local_date_key(value)}T{self._local_minutes(value) // 60:02d}"

    def _local_date_key(self, value: str) -> str:
        dt = datetime.fromisoformat(to_utc_z(value).replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo(self.tz_name))
        return local.date().isoformat()

    def _local_minutes(self, value: str) -> int:
        dt = datetime.fromisoformat(to_utc_z(value).replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo(self.tz_name))
        return local.hour * 60 + local.minute
