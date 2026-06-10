from __future__ import annotations

from typing import Any
import httpx

from config import Settings, settings
from models import BookingRequest, BookingSummary
from time_utils import to_utc_z


class CalcomAPIError(RuntimeError):
    pass


class CalcomClient:
    """Thin, testable wrapper around Cal.com API v2."""

    BASE_URL = "https://api.cal.com/v2"

    # Cal.com currently documents different required API versions across endpoints.
    BOOKINGS_WRITE_API_VERSION = "2026-02-25"
    BOOKINGS_LIST_API_VERSION = "2026-05-01"
    SLOTS_API_VERSION = "2024-09-04"

    def __init__(self, cfg: Settings = settings, http_client: httpx.Client | None = None):
        self.cfg = cfg
        self.http = http_client or httpx.Client(timeout=30)

    def _headers(self, api_version: str) -> dict[str, str]:
        if not self.cfg.calcom_api_key:
            raise CalcomAPIError("CALCOM_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {self.cfg.calcom_api_key}",
            "Content-Type": "application/json",
            "cal-api-version": api_version,
        }

    @staticmethod
    def _raise_for_api_error(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise CalcomAPIError(f"Cal.com API error {response.status_code}: {detail}")

    def _event_type_payload(self) -> dict[str, Any]:
        if self.cfg.calcom_event_type_id:
            return {"eventTypeId": self.cfg.calcom_event_type_id}
        if self.cfg.calcom_event_type_slug and self.cfg.calcom_username:
            return {
                "eventTypeSlug": self.cfg.calcom_event_type_slug,
                "username": self.cfg.calcom_username,
            }
        raise CalcomAPIError(
            "Configure CALCOM_EVENT_TYPE_ID, or CALCOM_EVENT_TYPE_SLUG + CALCOM_USERNAME"
        )

    def get_slots(
        self,
        start: str,
        end: str,
        duration_minutes: int | None = None,
        booking_uid_to_reschedule: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "timeZone": self.cfg.default_timezone,
            "format": "range",
        }
        if self.cfg.calcom_event_type_id:
            params["eventTypeId"] = self.cfg.calcom_event_type_id
        elif self.cfg.calcom_event_type_slug and self.cfg.calcom_username:
            params["eventTypeSlug"] = self.cfg.calcom_event_type_slug
            params["username"] = self.cfg.calcom_username
        else:
            raise CalcomAPIError("Missing Cal.com event type configuration")
        if duration_minutes:
            params["duration"] = duration_minutes
        if booking_uid_to_reschedule:
            params["bookingUidToReschedule"] = booking_uid_to_reschedule

        response = self.http.get(
            f"{self.BASE_URL}/slots",
            headers=self._headers(self.SLOTS_API_VERSION),
            params=params,
        )
        self._raise_for_api_error(response)
        return response.json().get("data", {})

    def list_bookings(
        self,
        status: str = "upcoming",
        after_start: str | None = None,
        before_end: str | None = None,
    ) -> list[BookingSummary]:
        params: dict[str, Any] = {"status": status}
        if after_start:
            params["afterStart"] = after_start
        if before_end:
            params["beforeEnd"] = before_end

        response = self.http.get(
            f"{self.BASE_URL}/bookings",
            headers=self._headers(self.BOOKINGS_LIST_API_VERSION),
            params=params,
        )
        self._raise_for_api_error(response)
        raw = response.json().get("data", [])
        if isinstance(raw, dict) and "bookings" in raw:
            raw = raw["bookings"]
        return [self._summarize_booking(item) for item in raw]

    def create_booking(self, request: BookingRequest) -> BookingSummary:
        payload: dict[str, Any] = {
            "start": to_utc_z(request.start),
            "attendee": {
                "name": request.attendee_name,
                "email": str(request.attendee_email),
                "timeZone": request.attendee_timezone,
            },
            **self._event_type_payload(),
        }
        if request.duration_minutes:
            payload["lengthInMinutes"] = request.duration_minutes
        if request.guests:
            payload["guests"] = [str(email) for email in request.guests]
        if request.metadata:
            payload["metadata"] = request.metadata

        response = self.http.post(
            f"{self.BASE_URL}/bookings",
            headers=self._headers(self.BOOKINGS_WRITE_API_VERSION),
            json=payload,
        )
        self._raise_for_api_error(response)
        return self._summarize_booking(response.json().get("data", {}))

    def cancel_booking(self, booking_uid: str, reason: str | None = None) -> BookingSummary:
        payload = {"cancellationReason": reason or "Cancelled by scheduling assistant"}
        response = self.http.post(
            f"{self.BASE_URL}/bookings/{booking_uid}/cancel",
            headers=self._headers(self.BOOKINGS_WRITE_API_VERSION),
            json=payload,
        )
        self._raise_for_api_error(response)
        return self._summarize_booking(response.json().get("data", {}))

    def reschedule_booking(
        self,
        booking_uid: str,
        new_start: str,
        reason: str | None = None,
        rescheduled_by: str | None = None,
    ) -> BookingSummary:
        payload = {
            "start": to_utc_z(new_start),
            "reschedulingReason": reason or "Rescheduled by scheduling assistant",
        }
        if rescheduled_by:
            payload["rescheduledBy"] = rescheduled_by
        response = self.http.post(
            f"{self.BASE_URL}/bookings/{booking_uid}/reschedule",
            headers=self._headers(self.BOOKINGS_WRITE_API_VERSION),
            json=payload,
        )
        self._raise_for_api_error(response)
        return self._summarize_booking(response.json().get("data", {}))


    def reserve_slot(
        self,
        slot_start: str,
        slot_duration: int | None = None,
        reservation_duration: int = 5,
    ) -> dict[str, Any]:
        """Temporarily reserve a slot before creating a booking.

        The Streamlit UI treats this as optional; booking still works without it.
        """
        payload: dict[str, Any] = {
            **self._event_type_payload(),
            "slotStart": to_utc_z(slot_start),
            "reservationDuration": reservation_duration,
        }
        if slot_duration:
            payload["slotDuration"] = slot_duration
        response = self.http.post(
            f"{self.BASE_URL}/slots/reservations",
            headers=self._headers(self.SLOTS_API_VERSION),
            json=payload,
        )
        self._raise_for_api_error(response)
        return response.json().get("data", response.json())

    def get_reserved_slot(self, reservation_uid: str) -> dict[str, Any]:
        response = self.http.get(
            f"{self.BASE_URL}/slots/reservations/{reservation_uid}",
            headers=self._headers(self.SLOTS_API_VERSION),
        )
        self._raise_for_api_error(response)
        return response.json().get("data", response.json())

    def update_reserved_slot(
        self,
        reservation_uid: str,
        slot_start: str,
        slot_duration: int | None = None,
        reservation_duration: int = 5,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self._event_type_payload(),
            "slotStart": to_utc_z(slot_start),
            "reservationDuration": reservation_duration,
        }
        if slot_duration:
            payload["slotDuration"] = slot_duration
        response = self.http.patch(
            f"{self.BASE_URL}/slots/reservations/{reservation_uid}",
            headers=self._headers(self.SLOTS_API_VERSION),
            json=payload,
        )
        self._raise_for_api_error(response)
        return response.json().get("data", response.json())

    def delete_reserved_slot(self, reservation_uid: str) -> dict[str, Any]:
        response = self.http.delete(
            f"{self.BASE_URL}/slots/reservations/{reservation_uid}",
            headers=self._headers(self.SLOTS_API_VERSION),
        )
        self._raise_for_api_error(response)
        return response.json().get("data", response.json())

    @staticmethod
    def _summarize_booking(raw: dict[str, Any]) -> BookingSummary:
        attendees = raw.get("attendees") or []
        attendee = None
        if attendees and isinstance(attendees, list):
            first = attendees[0]
            attendee = first.get("name") or first.get("email")
        return BookingSummary(
            uid=raw.get("uid") or raw.get("bookingUid") or raw.get("id", "unknown"),
            title=raw.get("title"),
            status=raw.get("status"),
            start=raw.get("start"),
            end=raw.get("end"),
            attendee=attendee,
            location=raw.get("location") or raw.get("meetingUrl"),
        )
