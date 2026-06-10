from __future__ import annotations

import json
from typing import Any

from config import Settings, settings
from models import BookingSummary, SlotChoice
from prompts import RESPONSE_SYSTEM_PROMPT
from time_utils import format_local_time


TEMPLATE_ONLY_ACTIONS = frozenset(
    {
        "list",
        "book_success",
        "cancel_success",
        "reschedule_success",
        "confirm_cancel",
        "confirm_reschedule",
        "slots",
    }
)


class ResponseFormatter:
    """Turn structured agent outcomes into user-facing chat text.

    Uses an LLM to polish wording when available; otherwise renders deterministic
    templates with local-friendly times.
    """

    def __init__(self, cfg: Settings = settings):
        self.cfg = cfg
        self.client = None
        if cfg.openai_api_key:
            from openai import OpenAI

            self.client = OpenAI(
                api_key=cfg.openai_api_key,
                base_url=cfg.openai_base_url or "https://api.openai.com/v1",
            )

    def format(self, user_message: str, event: dict[str, Any]) -> str:
        baseline = self._format_template(event)
        action = event.get("action", "info")
        if not self.client or action in TEMPLATE_ONLY_ACTIONS:
            return baseline
        try:
            return self._format_with_llm(user_message, event, baseline)
        except Exception:
            return baseline

    def _format_with_llm(self, user_message: str, event: dict[str, Any], baseline: str) -> str:
        payload = {
            "timezone": self.cfg.default_timezone,
            "user_message": user_message,
            "event": self._serialize_event(event),
            "baseline_response": baseline,
        }
        response = self.client.chat.completions.create(
            model=self.cfg.openai_model,
            messages=[
                {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.3,
        )
        text = (response.choices[0].message.content or "").strip()
        return text or baseline

    def _serialize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        serialized = dict(event)
        if bookings := serialized.get("bookings"):
            serialized["bookings"] = [self._booking_payload(b) for b in bookings]
        if booking := serialized.get("booking"):
            serialized["booking"] = self._booking_payload(booking)
        if slots := serialized.get("slot_choices"):
            serialized["slot_choices"] = [s.model_dump() for s in slots]
        return serialized

    def _booking_payload(self, booking: BookingSummary) -> dict[str, Any]:
        return {
            "uid": booking.uid,
            "title": booking.title,
            "status": booking.status,
            "when": self._format_when(booking),
            "attendee": booking.attendee,
            "location": booking.location,
        }

    def _format_template(self, event: dict[str, Any]) -> str:
        action = event.get("action", "info")
        if action == "help":
            return event.get("message", "")
        if action == "info":
            return event.get("message", "")
        if action == "need_info":
            return self._template_need_info(event)
        if action == "list":
            return self._template_list(event.get("bookings", []))
        if action == "book_success":
            return self._template_success("Booked", event.get("booking"))
        if action == "cancel_success":
            return self._template_success("Cancelled", event.get("booking"))
        if action == "reschedule_success":
            return self._template_success("Rescheduled", event.get("booking"))
        if action == "confirm_cancel":
            return (
                f"Please confirm: cancel your meeting on **{event.get('when', 'that time')}** "
                f"(UID: `{event.get('uid')}`)? Reply `yes` or `no`."
            )
        if action == "confirm_reschedule":
            return (
                f"Please confirm: move booking `{event.get('uid')}` to "
                f"**{event.get('when', 'the new time')}**? Reply `yes` or `no`."
            )
        if action == "slots":
            return self._template_slots(event.get("slot_choices", []), event.get("message"))
        if action == "error":
            return self._template_error(event)
        return event.get("message", "I couldn't complete that request.")

    def _template_list(self, bookings: list[BookingSummary]) -> str:
        active = [b for b in bookings if b.status not in {"cancelled", "rejected"}]
        if not active:
            return "I don't see any upcoming meetings."
        lines = [f"You have **{len(active)} upcoming meeting{'s' if len(active) != 1 else ''}**:\n"]
        for index, booking in enumerate(active, start=1):
            lines.append(f"{index}. {self._booking_line(booking)}")
        return "\n".join(lines)

    def _template_success(self, verb: str, booking: BookingSummary | None) -> str:
        if not booking:
            return f"{verb} successfully."
        return f"**{verb} successfully.**\n\n{self._booking_block(booking)}"

    def _template_slots(self, choices: list[SlotChoice], prefix: str | None) -> str:
        lines = [prefix or "I found these available slots:"]
        for choice in choices:
            lines.append(f"{choice.index}. `{choice.label}`")
        lines.append("\nReply with a number, or paste one of the times above.")
        return "\n".join(lines)

    def _template_error(self, event: dict[str, Any]) -> str:
        code = event.get("error_code")
        if code == "cancelled_booking":
            when = event.get("when")
            suffix = f" at **{when}**" if when else ""
            return (
                f"I can't reschedule that meeting{suffix} because it has already been cancelled. "
                "Ask me to `show my meetings` to see what's still upcoming."
            )
        if code == "calcom_api":
            return event.get("message") or "Cal.com rejected that request. Try another time or meeting."
        return event.get("message") or "Something went wrong while talking to Cal.com."

    def _template_need_info(self, event: dict[str, Any]) -> str:
        intent = event.get("intent", "book")
        missing = event.get("missing_fields", [])
        if intent == "book":
            prompts = {
                "a date/time or time range": "What day and time should I book?",
                "the attendee name": "Who is the meeting with?",
                "the attendee email": "What is their email address?",
            }
            questions = [prompts.get(field, f"Please provide {field}.") for field in missing]
            intro = "I can book that. I just need a few details:"
            return intro + "\n" + "\n".join(f"- {question}" for question in questions)
        return event.get("message", "I need a bit more information before I can do that.")

    def _booking_line(self, booking: BookingSummary) -> str:
        when = self._format_when(booking)
        title = (booking.title or "Meeting").strip()
        attendee = (booking.attendee or "").strip()
        if attendee and attendee.lower() not in title.lower():
            summary = f"{title} with {attendee}"
        else:
            summary = title
        status = f" · _{booking.status}_" if booking.status else ""
        return f"**{when}** — {summary}{status} · UID: `{booking.uid}`"

    def _booking_block(self, booking: BookingSummary) -> str:
        when = self._format_when(booking)
        lines = [
            f"**{booking.title or 'Meeting'}**",
            f"- **When:** {when}",
            f"- **Status:** {booking.status or 'unknown'}",
            f"- **Attendee:** {booking.attendee or 'unknown'}",
            f"- **UID:** `{booking.uid}`",
        ]
        if booking.location:
            lines.append(f"- **Location:** {booking.location}")
        return "\n".join(lines)

    def _format_when(self, booking: BookingSummary) -> str:
        if not booking.start:
            return "unknown time"
        start = format_local_time(booking.start, self.cfg.default_timezone)
        if not booking.end:
            return start
        end_time = format_local_time(booking.end, self.cfg.default_timezone).split(" at ", 1)[-1]
        return f"{start} → {end_time}"
