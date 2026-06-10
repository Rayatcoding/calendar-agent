from __future__ import annotations

import json
import httpx

from calcom_client import CalcomClient
from config import Settings
from models import BookingRequest


TEST_SETTINGS = Settings(
    calcom_api_key="cal_test",
    calcom_event_type_id=123,
    default_timezone="America/Los_Angeles",
)


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return CalcomClient(TEST_SETTINGS, http_client=httpx.Client(transport=transport))


def test_list_bookings_maps_response_and_uses_list_api_version():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v2/bookings"
        assert request.headers["authorization"] == "Bearer cal_test"
        assert request.headers["cal-api-version"] == "2026-05-01"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "uid": "abc",
                        "title": "Intro",
                        "status": "accepted",
                        "start": "2026-06-11T20:00:00Z",
                        "end": "2026-06-11T20:30:00Z",
                        "attendees": [{"name": "Jane"}],
                    }
                ],
            },
        )

    client = make_client(handler)
    bookings = client.list_bookings(status="upcoming")
    assert len(bookings) == 1
    assert bookings[0].uid == "abc"
    assert bookings[0].attendee == "Jane"


def test_create_booking_sends_expected_payload_and_write_api_version():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/bookings"
        assert request.headers["cal-api-version"] == "2026-02-25"
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(
            201,
            json={
                "status": "success",
                "data": {
                    "uid": "new_uid",
                    "title": "Consultation",
                    "status": "accepted",
                    "start": "2026-06-11T20:00:00Z",
                    "end": "2026-06-11T20:30:00Z",
                    "attendees": [{"name": "Jane Doe", "email": "jane@example.com"}],
                },
            },
        )

    client = make_client(handler)
    booking = client.create_booking(
        BookingRequest(
            start="2026-06-11T20:00:00Z",
            attendee_name="Jane Doe",
            attendee_email="jane@example.com",
            attendee_timezone="America/Los_Angeles",
            duration_minutes=30,
        )
    )

    assert booking.uid == "new_uid"
    assert captured["eventTypeId"] == 123
    assert captured["attendee"]["email"] == "jane@example.com"
    assert captured["lengthInMinutes"] == 30
    assert captured["start"] == "2026-06-11T20:00:00Z"


def test_create_booking_normalizes_offset_start_to_utc():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(
            201,
            json={
                "status": "success",
                "data": {
                    "uid": "new_uid",
                    "title": "Consultation",
                    "status": "accepted",
                    "start": "2026-06-12T16:00:00Z",
                    "end": "2026-06-12T16:30:00Z",
                    "attendees": [{"name": "Jane Doe", "email": "jane@example.com"}],
                },
            },
        )

    client = make_client(handler)
    client.create_booking(
        BookingRequest(
            start="2026-06-12T09:00:00.000-07:00",
            attendee_name="Jane Doe",
            attendee_email="jane@example.com",
            attendee_timezone="America/Los_Angeles",
        )
    )
    assert captured["start"] == "2026-06-12T16:00:00Z"


def test_get_slots_sends_event_type_and_slot_api_version():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v2/slots"
        assert request.headers["cal-api-version"] == "2024-09-04"
        assert request.url.params["eventTypeId"] == "123"
        assert request.url.params["duration"] == "30"
        return httpx.Response(
            200,
            json={"status": "success", "data": {"slots": [{"start": "2026-06-11T20:00:00Z"}]}},
        )

    client = make_client(handler)
    slots = client.get_slots("2026-06-11T00:00:00Z", "2026-06-12T00:00:00Z", 30)
    assert slots["slots"][0]["start"] == "2026-06-11T20:00:00Z"


def test_reserve_slot_calls_reservation_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/slots/reservations"
        assert request.headers["cal-api-version"] == "2024-09-04"
        captured.update(json.loads(request.content.decode()))
        return httpx.Response(201, json={"status": "success", "data": {"reservationUid": "res_123"}})

    client = make_client(handler)
    result = client.reserve_slot("2026-06-11T20:00:00Z", slot_duration=30)
    assert captured["eventTypeId"] == 123
    assert captured["slotStart"] == "2026-06-11T20:00:00Z"
    assert result["reservationUid"] == "res_123"


def test_cancel_booking_calls_cancel_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/bookings/abc/cancel"
        return httpx.Response(
            200,
            json={"status": "success", "data": {"uid": "abc", "status": "cancelled"}},
        )

    client = make_client(handler)
    booking = client.cancel_booking("abc", "No longer needed")
    assert booking.uid == "abc"
    assert booking.status == "cancelled"


def test_reschedule_booking_calls_reschedule_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/bookings/abc/reschedule"
        return httpx.Response(
            201,
            json={
                "status": "success",
                "data": {
                    "uid": "new_abc",
                    "status": "accepted",
                    "start": "2026-06-12T22:00:00Z",
                },
            },
        )

    client = make_client(handler)
    booking = client.reschedule_booking("abc", "2026-06-12T22:00:00Z")
    assert booking.uid == "new_abc"
    assert booking.start == "2026-06-12T22:00:00Z"
