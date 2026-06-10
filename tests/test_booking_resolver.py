from __future__ import annotations

from booking_resolver import BookingResolver
from models import BookingSummary, ParsedCommand


def resolver() -> BookingResolver:
    return BookingResolver("America/Los_Angeles")


def test_resolver_picks_closest_booking_time():
    bookings = [
        BookingSummary(uid="ten", title="Intro", status="accepted", start="2026-06-12T17:00:00Z"),
        BookingSummary(uid="ten-thirty", title="Intro", status="accepted", start="2026-06-12T17:30:00Z"),
    ]
    assert resolver().match_uid_from_list(bookings, "2026-06-12T17:00:00Z") == "ten"


def test_resolver_disambiguates_multiple_same_day_meetings():
    bookings = [
        BookingSummary(uid="ten", title="Intro", status="accepted", start="2026-06-12T17:00:00Z"),
        BookingSummary(uid="eleven", title="Intro", status="accepted", start="2026-06-12T18:00:00Z"),
    ]
    uid, hint = resolver().resolve_reference(
        ParsedCommand(start="2026-06-12T12:00:00Z"),
        bookings,
    )
    assert uid is None
    assert "multiple upcoming meetings" in hint.lower()
