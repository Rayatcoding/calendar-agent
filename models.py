from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field


class Intent(str, Enum):
    BOOK = "book"
    LIST = "list"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    SLOTS = "slots"
    HELP = "help"
    UNKNOWN = "unknown"


class ParsedCommand(BaseModel):
    """Structured intent extracted from one user message."""

    intent: Intent = Intent.UNKNOWN
    start: str | None = None  # Exact UTC start timestamp for book/reschedule.
    after_start: str | None = None  # UTC range start for list/availability queries.
    before_end: str | None = None  # UTC range end for list/availability queries.
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    attendee_name: str | None = None
    attendee_email: str | None = None
    booking_uid: str | None = None
    reason: str | None = None
    time_phrase: str | None = None
    summary: str | None = None
    missing_fields: list[str] = Field(default_factory=list)

    def merge(self, newer: "ParsedCommand") -> "ParsedCommand":
        """Merge a follow-up command into a pending command.

        New non-null fields win. The original intent is preserved unless the user
        clearly supplied a new non-unknown intent.
        """
        data = self.model_dump()
        incoming = newer.model_dump()
        for key, value in incoming.items():
            if key == "intent":
                if newer.intent != Intent.UNKNOWN:
                    data[key] = newer.intent
                continue
            if value not in (None, [], ""):
                data[key] = value
        return ParsedCommand.model_validate(data)


class BookingRequest(BaseModel):
    start: str
    attendee_name: str
    attendee_email: EmailStr
    attendee_timezone: str
    duration_minutes: int | None = None
    guests: list[EmailStr] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BookingSummary(BaseModel):
    uid: str
    title: str | None = None
    status: str | None = None
    start: str | None = None
    end: str | None = None
    attendee: str | None = None
    location: str | None = None


class SlotChoice(BaseModel):
    index: int
    start: str
    end: str | None = None
    label: str | None = None


class ConfirmationAction(BaseModel):
    action: Literal["cancel", "reschedule"]
    booking_uid: str
    start: str | None = None
    reason: str | None = None


class AgentState(BaseModel):
    pending_command: ParsedCommand | None = None
    candidate_slots: list[SlotChoice] = Field(default_factory=list)
    awaiting_confirmation: ConfirmationAction | None = None
    recent_bookings: list[BookingSummary] = Field(default_factory=list)
