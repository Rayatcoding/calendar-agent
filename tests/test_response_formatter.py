from __future__ import annotations

from config import Settings
from models import BookingSummary
from response_formatter import ResponseFormatter


def formatter() -> ResponseFormatter:
    return ResponseFormatter(Settings(openai_api_key=None, default_timezone="America/Los_Angeles"))


def test_list_template_uses_local_times_not_utc():
    bookings = [
        BookingSummary(
            uid="abc123",
            title="30 min meeting",
            status="accepted",
            start="2026-06-12T17:00:00.000Z",
            end="2026-06-12T17:30:00.000Z",
            attendee="Demo User",
        )
    ]
    text = formatter().format("show my meetings", {"action": "list", "bookings": bookings})
    assert "10:00 AM" in text
    assert "2026-06-12T17:00:00" not in text
    assert "abc123" in text


def test_need_info_prompt_lists_booking_questions():
    text = formatter().format(
        "book a meeting",
        {
            "action": "need_info",
            "intent": "book",
            "missing_fields": ["a date/time or time range", "the attendee email"],
        },
    )
    assert "day and time" in text.lower()
    assert "email" in text.lower()


def test_list_line_does_not_repeat_attendee_in_title():
    bookings = [
        BookingSummary(
            uid="abc123",
            title="30 min meeting between Rayray and Demo User",
            status="accepted",
            start="2026-06-12T17:00:00.000Z",
            end="2026-06-12T17:30:00.000Z",
            attendee="Demo User",
        )
    ]
    text = formatter().format("show my meetings", {"action": "list", "bookings": bookings})
    assert text.count("Demo User") == 1


def test_cancelled_booking_error_is_readable():
    text = formatter().format(
        "reschedule my 1pm meeting",
        {
            "action": "error",
            "error_code": "cancelled_booking",
            "when": "Fri Jun 12, 2026 at 1:00 PM",
        },
    )
    assert "cancelled" in text.lower()
    assert "show my meetings" in text.lower()
    assert "400" not in text
