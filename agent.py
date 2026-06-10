from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from booking_resolver import BookingResolver
from calcom_client import CalcomAPIError, CalcomClient
from config import Settings, settings
from llm_router import MISSING_OPENAI_KEY, PARSE_ERROR_PREFIX, CommandParser
from parse_utils import (
    extract_datetime_as_utc,
    extract_uid,
    is_reschedule_intent,
    looks_like_book_intent,
    looks_like_booking_time,
    looks_like_list_intent,
)
from response_formatter import ResponseFormatter
from time_utils import format_local_time, normalize_start_key, to_utc_z
from models import (
    AgentState,
    BookingRequest,
    BookingSummary,
    ConfirmationAction,
    Intent,
    ParsedCommand,
    SlotChoice,
)


class SchedulingAgent:
    """Small tool-using agent for the Cal.com take-home assignment.

    The LLM/parser extracts intent and fields. This class owns workflow logic:
    missing-field handling, slot choices, confirmations, and deterministic tool
    execution through CalcomClient.
    """

    def __init__(
        self,
        cfg: Settings = settings,
        client: CalcomClient | None = None,
        parser: CommandParser | None = None,
    ):
        self.cfg = cfg
        self.client = client or CalcomClient(cfg)
        self.parser = parser or CommandParser(cfg)
        self.bookings = BookingResolver(cfg.default_timezone)
        self.formatter = ResponseFormatter(cfg)

    def _present(self, user_text: str, event: dict) -> str:
        return self.formatter.format(user_text, event)

    def handle_message(
        self,
        user_text: str,
        state: AgentState | None = None,
        history: list[dict] | None = None,
    ) -> tuple[str, AgentState]:
        state = state or AgentState()
        normalized = user_text.strip().lower()

        # Confirmation branch for destructive actions.
        if state.awaiting_confirmation:
            if self._is_yes(normalized):
                action = state.awaiting_confirmation
                state.awaiting_confirmation = None
                try:
                    return self._execute_confirmed_action(action, user_text, state), state
                except CalcomAPIError as exc:
                    return self._format_calcom_error(exc, user_text, action.booking_uid, state), state
            if self._is_no(normalized):
                state.awaiting_confirmation = None
                return self._present(user_text, {"action": "info", "message": "No problem — I did not change the booking."}), state
            return self._present(
                user_text,
                {"action": "info", "message": "Please reply `yes` to confirm or `no` to cancel this action."},
            ), state

        # Slot selection branch after an availability search.
        if state.candidate_slots and state.pending_command:
            selected = self._match_slot_choice(user_text, state.candidate_slots)
            if selected:
                cmd = state.pending_command
                cmd.start = to_utc_z(selected.start)
                state.candidate_slots = []
                return self._execute_command(cmd, state, user_text), state

        parsed = self.parser.parse(user_text, history or [], state)
        if parsed.summary == MISSING_OPENAI_KEY:
            return self._parse_error_message(parsed) or "", state

        parsed = self._apply_context_hints(parsed, user_text, state, history or [])
        parsed = self._coerce_datetime_follow_up(parsed, user_text, state)
        parsed = self._coerce_list_intent(parsed, user_text)
        parsed = self._coerce_book_intent(parsed, user_text)

        if parsed.intent == Intent.UNKNOWN:
            parse_error = self._parse_error_message(parsed)
            if parse_error:
                return parse_error, state

        # Merge short follow-up answers into a pending command.
        if state.pending_command and self._is_distinct_intent(parsed, user_text):
            state.pending_command = None
        if state.pending_command and parsed.intent == Intent.UNKNOWN:
            parsed = state.pending_command.merge(parsed)
        elif state.pending_command and parsed.intent == state.pending_command.intent:
            parsed = state.pending_command.merge(parsed)

        return self._execute_command(parsed, state, user_text), state

    def _execute_command(self, cmd: ParsedCommand, state: AgentState, user_text: str = "") -> str:
        if cmd.intent == Intent.HELP:
            return self._present(user_text, {"action": "help", "message": self.help_text()})

        if cmd.intent == Intent.UNKNOWN:
            return self._present(
                user_text,
                {
                    "action": "info",
                    "message": "I’m not sure what you want to do. Try asking me to book, list, cancel, or reschedule a meeting.",
                },
            )

        if not self.cfg.has_calcom_auth:
            return self._present(
                user_text,
                {"action": "info", "message": "Missing `CALCOM_API_KEY`. Add it to `.env` and restart the app."},
            )

        if cmd.intent in {Intent.BOOK, Intent.SLOTS, Intent.RESCHEDULE} and not self.cfg.has_event_type_config:
            return self._present(
                user_text,
                {
                    "action": "info",
                    "message": "Missing event type config. Set `CALCOM_EVENT_TYPE_ID`, or `CALCOM_EVENT_TYPE_SLUG` + `CALCOM_USERNAME`.",
                },
            )

        if cmd.intent == Intent.LIST:
            bookings = self.client.list_bookings(after_start=cmd.after_start, before_end=cmd.before_end)
            state.recent_bookings = bookings
            active = [b for b in bookings if b.status not in {"cancelled", "rejected"}]
            if active:
                return self._present(user_text, {"action": "list", "bookings": bookings})
            return self._present(user_text, {"action": "info", "message": self._render_empty_booking_list()})

        if cmd.intent == Intent.SLOTS:
            return self._handle_slots(cmd, state, user_text)

        if cmd.intent == Intent.BOOK:
            return self._handle_book(cmd, state, user_text)

        if cmd.intent == Intent.CANCEL:
            return self._handle_cancel(cmd, state, user_text)

        if cmd.intent == Intent.RESCHEDULE:
            return self._handle_reschedule(cmd, state, user_text)

        return self._present(
            user_text,
            {"action": "info", "message": "I’m not sure what you want to do. Try: book, list, cancel, or reschedule."},
        )

    def _handle_book(self, cmd: ParsedCommand, state: AgentState, user_text: str) -> str:
        cmd = self._with_booking_defaults(cmd)
        missing = self._missing_booking_fields(cmd)
        if missing:
            state.pending_command = cmd
            return self._present(
                user_text,
                {
                    "action": "need_info",
                    "intent": "book",
                    "missing_fields": missing,
                },
            )

        # If the user supplied a time window rather than an exact start, show choices.
        if not cmd.start and cmd.after_start and cmd.before_end:
            return self._show_slot_choices(cmd, state, user_text=user_text)

        resolved_start = self._resolve_available_slot_start(cmd.start or "", cmd.duration_minutes)
        if not resolved_start:
            day_start, day_end = self._slot_day_range(cmd.start or "")
            slot_cmd = cmd.model_copy(
                update={"after_start": day_start, "before_end": day_end, "start": None}
            )
            state.pending_command = slot_cmd
            return self._show_slot_choices(
                slot_cmd,
                state,
                prefix=(
                    f"That exact time isn't available on {self._format_local_day(cmd.start or '')} in Cal.com. "
                    "Pick one of these open slots instead:"
                ),
                user_text=user_text,
            )

        try:
            booking = self.client.create_booking(
                BookingRequest(
                    start=resolved_start,
                    attendee_name=cmd.attendee_name or "",
                    attendee_email=cmd.attendee_email or "",
                    attendee_timezone=self.cfg.default_timezone,
                    duration_minutes=cmd.duration_minutes,
                    metadata={"source": "calcom-scheduling-agent"},
                )
            )
        except CalcomAPIError as exc:
            if self._is_schedule_conflict(exc):
                state.pending_command = None
                return self._explain_booking_conflict(cmd.model_copy(update={"start": resolved_start}))
            raise
        self._finalize_success(state, booking)
        return self._present(user_text, {"action": "book_success", "booking": booking})

    def _handle_slots(self, cmd: ParsedCommand, state: AgentState, user_text: str) -> str:
        if not cmd.after_start or not cmd.before_end:
            state.pending_command = cmd
            return self._present(
                user_text,
                {"action": "info", "message": "What date or time range should I check for availability?"},
            )
        return self._show_slot_choices(cmd, state, persist_pending=False, user_text=user_text)

    def _handle_cancel(self, cmd: ParsedCommand, state: AgentState, user_text: str) -> str:
        cmd.booking_uid, hint = self._resolve_booking_reference(cmd, state)
        if cmd.booking_uid:
            active_uid, inactive_msg = self._ensure_active_booking_uid(cmd.booking_uid, state)
            if inactive_msg:
                return self._present(user_text, {"action": "info", "message": inactive_msg})
            cmd = cmd.model_copy(update={"booking_uid": active_uid})
        if not cmd.booking_uid:
            if hint:
                state.pending_command = cmd
                return self._present(user_text, {"action": "info", "message": hint})
            state.pending_command = None
            return self._present(
                user_text,
                {
                    "action": "info",
                    "message": (
                        "I couldn't match that to an upcoming meeting. "
                        "Try `show my meetings` or cancel by time, e.g. "
                        "`cancel my meeting at 10am on June 12`."
                    ),
                },
            )
        state.pending_command = None
        state.awaiting_confirmation = ConfirmationAction(
            action="cancel",
            booking_uid=cmd.booking_uid,
            reason=cmd.reason,
        )
        booking = self._get_booking_summary(cmd.booking_uid, state)
        local_time = format_local_time(booking.start, self.cfg.default_timezone) if booking.start else "that time"
        return self._present(
            user_text,
            {"action": "confirm_cancel", "uid": cmd.booking_uid, "when": local_time},
        )

    def _handle_reschedule(self, cmd: ParsedCommand, state: AgentState, user_text: str) -> str:
        if not cmd.booking_uid:
            lookup = ParsedCommand(start=cmd.after_start or cmd.start)
            resolved_uid, hint = self._resolve_booking_reference(lookup, state)
            if resolved_uid:
                cmd = cmd.model_copy(update={"booking_uid": resolved_uid})
            elif hint:
                return self._present(user_text, {"action": "info", "message": hint})

        if cmd.booking_uid:
            active_uid, inactive_msg = self._ensure_active_booking_uid(cmd.booking_uid, state)
            if inactive_msg:
                return self._present(user_text, {"action": "info", "message": inactive_msg})
            cmd = cmd.model_copy(update={"booking_uid": active_uid})

        missing = []
        if not cmd.booking_uid:
            missing.append("which meeting to move (time or description)")
        if not cmd.start and not (cmd.after_start and cmd.before_end):
            missing.append("new time or time range")
        if missing:
            state.pending_command = cmd
            return self._present(
                user_text,
                {
                    "action": "info",
                    "message": "I need " + self._human_join(missing) + " before I can reschedule this.",
                },
            )

        if not cmd.start and cmd.after_start and cmd.before_end:
            return self._show_slot_choices(
                cmd, state, booking_uid_to_reschedule=cmd.booking_uid, user_text=user_text
            )

        resolved_start = self._resolve_available_slot_start(
            cmd.start or "",
            cmd.duration_minutes,
            booking_uid_to_reschedule=cmd.booking_uid,
        )
        if not resolved_start:
            day_start, day_end = self._slot_day_range(cmd.start or "")
            slot_cmd = cmd.model_copy(
                update={"after_start": day_start, "before_end": day_end, "start": None}
            )
            state.pending_command = slot_cmd
            return self._show_slot_choices(
                slot_cmd,
                state,
                booking_uid_to_reschedule=cmd.booking_uid,
                prefix=(
                    f"That exact time isn't available on {self._format_local_day(cmd.start or '')} for rescheduling. "
                    "Pick one of these open slots instead:"
                ),
                user_text=user_text,
            )

        state.pending_command = None
        state.awaiting_confirmation = ConfirmationAction(
            action="reschedule",
            booking_uid=cmd.booking_uid or "",
            start=resolved_start,
            reason=cmd.reason,
        )
        local_time = format_local_time(resolved_start, self.cfg.default_timezone)
        return self._present(
            user_text,
            {"action": "confirm_reschedule", "uid": cmd.booking_uid, "when": local_time},
        )

    def _execute_confirmed_action(
        self,
        action: ConfirmationAction,
        user_text: str,
        state: AgentState,
    ) -> str:
        if action.action == "cancel":
            booking = self.client.cancel_booking(action.booking_uid, action.reason)
            self._finalize_success(state, booking)
            return self._present(user_text, {"action": "cancel_success", "booking": booking})
        if action.action == "reschedule":
            booking = self.client.reschedule_booking(
                action.booking_uid,
                action.start or "",
                reason=action.reason,
                rescheduled_by=self.cfg.default_attendee_email,
            )
            self._finalize_success(state, booking)
            return self._present(user_text, {"action": "reschedule_success", "booking": booking})
        return self._present(user_text, {"action": "info", "message": "Unknown confirmation action."})

    def _ensure_active_booking_uid(
        self,
        booking_uid: str,
        state: AgentState,
    ) -> tuple[str | None, str | None]:
        booking = self._get_booking_summary(booking_uid, state)
        if booking.status in {"cancelled", "rejected"}:
            when = format_local_time(booking.start, self.cfg.default_timezone) if booking.start else None
            return None, self.formatter._template_error(
                {"action": "error", "error_code": "cancelled_booking", "when": when}
            )
        return booking_uid, None

    def _format_calcom_error(
        self,
        exc: CalcomAPIError,
        user_text: str,
        booking_uid: str,
        state: AgentState,
    ) -> str:
        message = str(exc).lower()
        if "cancelled" in message and "reschedule" in message:
            booking = self._get_booking_summary(booking_uid, state)
            when = format_local_time(booking.start, self.cfg.default_timezone) if booking.start else None
            return self._present(
                user_text,
                {"action": "error", "error_code": "cancelled_booking", "when": when},
            )
        return self._present(
            user_text,
            {
                "action": "error",
                "error_code": "calcom_api",
                "message": "Cal.com rejected that request. Double-check the meeting is still upcoming and the new time is available.",
            },
        )

    def _finalize_success(self, state: AgentState, booking: BookingSummary | None = None) -> None:
        state.pending_command = None
        state.candidate_slots = []
        state.awaiting_confirmation = None
        try:
            state.recent_bookings = self.client.list_bookings(status="upcoming")
        except CalcomAPIError:
            if booking:
                state.recent_bookings = [booking]

    def _show_slot_choices(
        self,
        cmd: ParsedCommand,
        state: AgentState,
        persist_pending: bool = True,
        booking_uid_to_reschedule: str | None = None,
        prefix: str | None = None,
        user_text: str = "",
    ) -> str:
        raw_slots = self.client.get_slots(
            cmd.after_start or "",
            cmd.before_end or "",
            duration_minutes=cmd.duration_minutes,
            booking_uid_to_reschedule=booking_uid_to_reschedule,
        )
        choices = self._extract_slot_choices(raw_slots)
        if not choices:
            day_label = self._format_day_from_range(cmd.after_start, cmd.before_end)
            return self._present(
                user_text,
                {
                    "action": "info",
                    "message": (
                        f"I don’t see available slots on {day_label}. "
                        "That day may be outside your Cal.com working hours. Try another date."
                    ),
                },
            )

        labeled: list[SlotChoice] = []
        for choice in choices[:5]:
            label = self._label_slot(choice.start, choice.end)
            labeled.append(choice.model_copy(update={"label": label}))
        state.candidate_slots = labeled
        if persist_pending:
            state.pending_command = cmd
        return self._present(
            user_text,
            {
                "action": "slots",
                "slot_choices": state.candidate_slots,
                "message": prefix or "I found these available slots:",
            },
        )

    def _label_slot(self, start: str, end: str | None) -> str:
        label = format_local_time(start, self.cfg.default_timezone)
        if end:
            end_local = format_local_time(end, self.cfg.default_timezone).split(" at ", 1)[-1]
            label = f"{label} → {end_local}"
        return label

    def _resolve_available_slot_start(
        self,
        requested_start: str,
        duration_minutes: int | None = None,
        booking_uid_to_reschedule: str | None = None,
    ) -> str | None:
        utc_start = to_utc_z(requested_start)
        day_start, day_end = self._slot_day_range(utc_start)
        raw_slots = self.client.get_slots(
            day_start,
            day_end,
            duration_minutes=duration_minutes,
            booking_uid_to_reschedule=booking_uid_to_reschedule,
        )
        choices = self._extract_slot_choices(raw_slots)
        if not choices:
            return None

        target_key = normalize_start_key(utc_start)
        for choice in choices:
            slot_utc = to_utc_z(choice.start)
            if normalize_start_key(slot_utc) == target_key:
                return slot_utc

        target_hour = self.bookings.local_hour_key(utc_start)
        hour_matches = [
            to_utc_z(choice.start)
            for choice in choices
            if self.bookings.local_hour_key(to_utc_z(choice.start)) == target_hour
        ]
        if len(hour_matches) == 1:
            return hour_matches[0]
        return None

    @staticmethod
    def _extract_slot_choices(raw_slots: Any) -> list[SlotChoice]:
        found: list[tuple[str, str | None]] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                start = value.get("start") or value.get("time") or value.get("slotStart")
                end = value.get("end") or value.get("slotEnd")
                if isinstance(start, str) and "T" in start:
                    found.append((start, end if isinstance(end, str) else None))
                for v in value.values():
                    visit(v)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, str) and "T" in value:
                found.append((value, None))

        visit(raw_slots)
        deduped: list[SlotChoice] = []
        seen = set()
        for start, end in found:
            if start in seen:
                continue
            seen.add(start)
            deduped.append(SlotChoice(index=len(deduped) + 1, start=start, end=end))
        return deduped

    def _match_slot_choice(self, text: str, choices: list[SlotChoice]) -> SlotChoice | None:
        match = re.search(r"^\s*(\d+)\s*\.?\s*$", text.strip())
        if match:
            idx = int(match.group(1))
            for choice in choices:
                if choice.index == idx:
                    return choice

        normalized = text.strip().lower()
        for choice in choices:
            label = (choice.label or self._label_slot(choice.start, choice.end)).lower()
            if normalized == label or normalized in label or label in normalized:
                return choice
            time_part = label.split(" at ", 1)[-1].split(" →", 1)[0].strip()
            if time_part and time_part in normalized:
                return choice

        parsed_start = extract_datetime_as_utc(text, self.cfg.default_timezone)
        if parsed_start:
            target_key = normalize_start_key(parsed_start)
            for choice in choices:
                if normalize_start_key(to_utc_z(choice.start)) == target_key:
                    return choice
                if self.bookings.local_hour_key(to_utc_z(choice.start)) == self.bookings.local_hour_key(
                    parsed_start
                ):
                    return choice
        return None

    @staticmethod
    def _parse_error_message(parsed: ParsedCommand) -> str | None:
        if parsed.summary == MISSING_OPENAI_KEY:
            return "Missing `OPENAI_API_KEY`. Add it to `.env` and restart the app."
        if parsed.summary and parsed.summary.startswith(PARSE_ERROR_PREFIX):
            return (
                "I couldn't understand that request right now. "
                "Please try again, or rephrase as book, list, cancel, or reschedule."
            )
        if parsed.summary == "invalid_llm_response":
            return "I couldn't understand that request. Try rephrasing it."
        return None

    def _coerce_list_intent(self, parsed: ParsedCommand, user_text: str) -> ParsedCommand:
        if parsed.intent == Intent.UNKNOWN and looks_like_list_intent(user_text):
            return ParsedCommand(intent=Intent.LIST, time_phrase=user_text)
        return parsed

    def _coerce_book_intent(self, parsed: ParsedCommand, user_text: str) -> ParsedCommand:
        if parsed.intent != Intent.UNKNOWN:
            return parsed
        book_fields = {"start", "attendee_name", "attendee_email", "after_start", "before_end"}
        parser_hints_book = bool(set(parsed.missing_fields) & book_fields)
        if looks_like_book_intent(user_text) or parser_hints_book:
            return ParsedCommand(
                intent=Intent.BOOK,
                missing_fields=parsed.missing_fields,
                time_phrase=user_text,
            )
        return parsed

    def _coerce_datetime_follow_up(
        self,
        parsed: ParsedCommand,
        user_text: str,
        state: AgentState,
    ) -> ParsedCommand:
        if parsed.intent != Intent.UNKNOWN:
            return parsed
        iso_start = extract_datetime_as_utc(user_text, self.cfg.default_timezone)
        if not iso_start:
            return parsed
        if state.pending_command and state.pending_command.intent in {Intent.BOOK, Intent.RESCHEDULE}:
            return state.pending_command.merge(
                parsed.model_copy(update={"start": iso_start, "intent": state.pending_command.intent})
            )
        if looks_like_booking_time(user_text, iso_start):
            return ParsedCommand(
                intent=Intent.BOOK,
                start=iso_start,
                attendee_name=self.cfg.default_attendee_name,
                attendee_email=self.cfg.default_attendee_email,
            )
        return parsed

    @staticmethod
    def _is_distinct_intent(parsed: ParsedCommand, user_text: str) -> bool:
        if parsed.intent not in {Intent.CANCEL, Intent.RESCHEDULE, Intent.BOOK, Intent.LIST, Intent.SLOTS}:
            return False
        lower = user_text.lower()
        if parsed.intent == Intent.CANCEL and is_reschedule_intent(lower):
            return True
        return True

    def _upcoming_bookings(self, state: AgentState) -> list[BookingSummary]:
        if state.recent_bookings:
            active = [b for b in state.recent_bookings if b.status not in {"cancelled", "rejected"}]
            if active:
                return active
        return self.client.list_bookings(status="upcoming")

    def _resolve_booking_reference(
        self,
        cmd: ParsedCommand,
        state: AgentState,
    ) -> tuple[str | None, str | None]:
        return self.bookings.resolve_reference(cmd, self._upcoming_bookings(state))

    def _get_booking_summary(self, booking_uid: str, state: AgentState) -> BookingSummary:
        for booking in self._upcoming_bookings(state):
            if booking.uid == booking_uid:
                return booking
        for status in ("upcoming", "past", "cancelled"):
            for booking in self.client.list_bookings(status=status):
                if booking.uid == booking_uid:
                    return booking
        return BookingSummary(uid=booking_uid)

    def _with_booking_defaults(self, cmd: ParsedCommand) -> ParsedCommand:
        updates: dict[str, str] = {}
        if not cmd.attendee_name:
            updates["attendee_name"] = self.cfg.default_attendee_name
        effective_name = updates.get("attendee_name", cmd.attendee_name)
        if not cmd.attendee_email and (
            not cmd.attendee_name or effective_name == self.cfg.default_attendee_name
        ):
            updates["attendee_email"] = self.cfg.default_attendee_email
        if updates:
            return cmd.model_copy(update=updates)
        return cmd

    @staticmethod
    def _missing_booking_fields(cmd: ParsedCommand) -> list[str]:
        missing = []
        if not cmd.start and not (cmd.after_start and cmd.before_end):
            missing.append("a date/time or time range")
        if not cmd.attendee_name:
            missing.append("the attendee name")
        if not cmd.attendee_email:
            missing.append("the attendee email")
        return missing

    @staticmethod
    def _is_yes(text: str) -> bool:
        return text in {"yes", "y", "confirm", "confirmed", "ok", "okay", "do it"}

    @staticmethod
    def _is_no(text: str) -> bool:
        return text in {"no", "n", "cancel", "stop", "never mind", "nevermind"}

    def _apply_context_hints(
        self,
        parsed: ParsedCommand,
        user_text: str,
        state: AgentState,
        history: list[dict],
    ) -> ParsedCommand:
        uid = extract_uid(user_text)
        if uid and not parsed.booking_uid:
            parsed = parsed.model_copy(update={"booking_uid": uid})

        if state.pending_command and not parsed.booking_uid:
            history_uid = self._find_uid_in_history(history)
            if history_uid:
                parsed = parsed.model_copy(update={"booking_uid": history_uid})

        if (
            parsed.intent == Intent.UNKNOWN
            and parsed.booking_uid
            and state.pending_command
            and state.pending_command.intent in {Intent.CANCEL, Intent.RESCHEDULE}
        ):
            parsed = state.pending_command.merge(parsed)

        return parsed

    @staticmethod
    def _find_uid_in_history(history: list[dict]) -> str | None:
        for message in reversed(history):
            if message.get("role") != "assistant":
                continue
            match = re.search(r"UID:\s*`([^`]+)`", message.get("content", ""))
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _is_schedule_conflict(exc: CalcomAPIError) -> bool:
        message = str(exc).lower()
        return "already has booking" in message or "not available" in message

    def _render_booking_by_uid(self, booking_uid: str, status: str = "upcoming") -> str:
        bookings = self.client.list_bookings(status=status)
        for booking in bookings:
            if booking.uid == booking_uid:
                return self.render_booking(booking)
        return f"I found UID `{booking_uid}`, but couldn't load its details."

    def _explain_booking_conflict(self, cmd: ParsedCommand) -> str:
        start = cmd.start or ""
        upcoming = self.client.list_bookings(status="upcoming")
        upcoming_uid = self.bookings.match_uid_from_list(upcoming, start) if start else None
        if upcoming_uid:
            return (
                "You already have an upcoming booking at that time.\n\n"
                + self._render_booking_by_uid(upcoming_uid)
            )

        lines = [
            "I couldn't book that time.",
            "You have **no upcoming meetings** on your calendar right now.",
        ]

        cancelled = self.client.list_bookings(status="cancelled")
        cancelled_uid = self.bookings.match_uid_from_list(cancelled, start) if start else None
        if cancelled_uid:
            lines.append(
                f"You do have a **cancelled** meeting at that time (UID: `{cancelled_uid}`), "
                "but that does not count as an upcoming booking."
            )

        if start:
            day_start, day_end = self._slot_day_range(start)
            slots = self.client.get_slots(
                day_start,
                day_end,
                duration_minutes=cmd.duration_minutes,
            )
            if not self._extract_slot_choices(slots):
                local_day = self._format_local_day(start)
                lines.append(
                    f"Cal.com also shows **no open slots on {local_day}** in your availability schedule. "
                    "That usually means the day or time is outside your working hours."
                )
            else:
                local_day = self._format_local_day(start)
                lines.append(
                    f"Other times may be open on {local_day}. "
                    f"Try: `what slots are available on {local_day}`?"
                )
        else:
            lines.append("Try another time, or ask me what slots are available.")

        return "\n\n".join(lines)

    def _render_empty_booking_list(self) -> str:
        cancelled = self.client.list_bookings(status="cancelled")
        if cancelled:
            return (
                "No upcoming bookings.\n\n"
                f"You have {len(cancelled)} cancelled meeting(s) on record. "
                "Cancelled meetings won't appear in this list, and those times may still be "
                "unavailable for rebooking.\n\n"
                "Try asking: `what slots are available on June 12?`"
            )
        return "I don't see any matching bookings."

    def _slot_day_range(self, start_utc: str) -> tuple[str, str]:
        dt = datetime.fromisoformat(to_utc_z(start_utc).replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo(self.cfg.default_timezone))
        start_local = datetime.combine(local.date(), datetime.min.time(), local.tzinfo)
        end_local = start_local + timedelta(days=1)
        return (to_utc_z(start_local.isoformat()), to_utc_z(end_local.isoformat()))

    def _format_day_from_range(self, after_start: str | None, before_end: str | None) -> str:
        if after_start:
            return self._format_local_day(after_start)
        return "that day"

    def _format_local_day(self, start_utc: str) -> str:
        dt = datetime.fromisoformat(to_utc_z(start_utc).replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo(self.cfg.default_timezone))
        return f"{local.strftime('%B')} {local.day}, {local.year}"

    @staticmethod
    def _human_join(items: list[str]) -> str:
        if len(items) <= 1:
            return items[0] if items else ""
        return ", ".join(items[:-1]) + ", and " + items[-1]

    def render_booking(self, booking: BookingSummary) -> str:
        return self.formatter._booking_block(booking)

    def render_bookings(self, bookings: list[BookingSummary]) -> str:
        return self.formatter._template_list(bookings)

    @staticmethod
    def help_text() -> str:
        return """
I can help with:

- **Book**: “Book a 30-min intro tomorrow at 2pm with Jane, jane@example.com”
- **Find slots**: “What slots are available Thursday afternoon?”
- **List**: “What’s on my calendar tomorrow?”
- **Cancel**: “Cancel booking abc123 because the candidate withdrew”
- **Reschedule**: “Move booking abc123 to Friday 3pm”

I will ask for missing details before booking and ask for confirmation before canceling or rescheduling.
""".strip()
