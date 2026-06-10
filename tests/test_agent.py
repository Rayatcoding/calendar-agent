from __future__ import annotations

from agent import SchedulingAgent
from calcom_client import CalcomAPIError
from config import Settings
from models import AgentState, BookingSummary, Intent, ParsedCommand, SlotChoice


class FakeParser:
    def __init__(self, command: ParsedCommand):
        self.command = command

    def parse(self, text, history=None, state=None):
        return self.command


class FakeClient:
    def __init__(self):
        self.cancelled = False

    def list_bookings(self, **kwargs):
        return [BookingSummary(uid="abc123", title="Intro", status="accepted", start="2026-06-11T20:00:00Z")]

    def get_slots(self, *args, **kwargs):
        return {"slots": [{"start": "2026-06-11T20:00:00Z", "end": "2026-06-11T20:30:00Z"}]}

    def create_booking(self, request):
        return BookingSummary(uid="new123", title="Intro", status="accepted", start=request.start, attendee=request.attendee_name)

    def cancel_booking(self, booking_uid, reason=None):
        self.cancelled = True
        return BookingSummary(uid=booking_uid, status="cancelled")

    def reschedule_booking(self, booking_uid, new_start, reason=None, rescheduled_by=None):
        return BookingSummary(uid=booking_uid, status="accepted", start=new_start)


def settings():
    return Settings(calcom_api_key="cal_test", calcom_event_type_id=123, default_attendee_email="demo@example.com")


def test_agent_asks_for_details_on_book_a_meeting():
    class UnknownBookParser:
        def parse(self, text, history=None, state=None):
            return ParsedCommand(
                intent=Intent.UNKNOWN,
                missing_fields=["attendee_name", "attendee_email"],
            )

    agent = SchedulingAgent(settings(), FakeClient(), UnknownBookParser())
    answer, state = agent.handle_message("book a meeting", AgentState())
    assert "day and time" in answer.lower() or "who is the meeting" in answer.lower()
    assert state.pending_command is not None
    assert state.pending_command.intent == Intent.BOOK


def test_agent_asks_for_missing_booking_fields():
    cmd = ParsedCommand(intent=Intent.BOOK, attendee_name="Jane")
    agent = SchedulingAgent(settings(), FakeClient(), FakeParser(cmd))
    answer, state = agent.handle_message("book Jane", AgentState())
    assert "email" in answer.lower()
    assert state.pending_command is not None


def test_agent_shows_slot_choices_for_time_window():
    cmd = ParsedCommand(
        intent=Intent.BOOK,
        attendee_name="Jane",
        attendee_email="jane@example.com",
        after_start="2026-06-11T19:00:00Z",
        before_end="2026-06-12T00:00:00Z",
    )
    agent = SchedulingAgent(settings(), FakeClient(), FakeParser(cmd))
    answer, state = agent.handle_message("book Jane Thursday afternoon", AgentState())
    assert "available slots" in answer
    assert state.candidate_slots[0].start == "2026-06-11T20:00:00Z"


def test_agent_accepts_slot_label_follow_up():
    slot_cmd = ParsedCommand(
        intent=Intent.BOOK,
        attendee_name="Demo User",
        attendee_email="demo@example.com",
        after_start="2026-06-12T07:00:00Z",
        before_end="2026-06-13T07:00:00Z",
    )

    class SlotParser:
        def parse(self, text, history=None, state=None):
            return ParsedCommand(intent=Intent.BOOK, start="2026-06-12T17:00:00Z")

    class SlotClient(FakeClient):
        def get_slots(self, *args, **kwargs):
            return {
                "2026-06-12": [
                    {"start": "2026-06-12T09:00:00.000-07:00", "end": "2026-06-12T09:30:00.000-07:00"},
                    {"start": "2026-06-12T10:00:00.000-07:00", "end": "2026-06-12T10:30:00.000-07:00"},
                ]
            }

        def create_booking(self, request):
            return BookingSummary(uid="new123", title="Intro", status="accepted", start=request.start)

    agent = SchedulingAgent(settings(), SlotClient(), SlotParser())
    state = AgentState(
        pending_command=slot_cmd,
        candidate_slots=[
            SlotChoice(index=1, start="2026-06-12T09:00:00.000-07:00", label="Fri Jun 12, 2026 at 9:00 AM"),
            SlotChoice(index=2, start="2026-06-12T10:00:00.000-07:00", label="Fri Jun 12, 2026 at 10:00 AM"),
        ],
    )
    answer, state = agent.handle_message("Fri Jun 12, 2026 at 10:00 AM", state)
    assert "booked successfully" in answer.lower()


def test_agent_blocks_reschedule_of_cancelled_booking():
    cancelled = BookingSummary(
        uid="dead123",
        title="Intro",
        status="cancelled",
        start="2026-06-12T20:00:00Z",
    )

    class CancelledClient(FakeClient):
        def list_bookings(self, **kwargs):
            status = kwargs.get("status", "upcoming")
            if status == "cancelled":
                return [cancelled]
            return []

    cmd = ParsedCommand(intent=Intent.RESCHEDULE, booking_uid="dead123", start="2026-06-12T21:00:00Z")
    agent = SchedulingAgent(settings(), CancelledClient(), FakeParser(cmd))
    answer, _ = agent.handle_message("reschedule my 1pm meeting to 2pm", AgentState())
    assert "cancelled" in answer.lower()
    assert "400" not in answer


def test_agent_lists_meetings_when_llm_fails():
    class FailingParser:
        def parse(self, text, history=None, state=None):
            return ParsedCommand(intent=Intent.UNKNOWN, summary="parse_error: APIConnectionError")

    agent = SchedulingAgent(settings(), FakeClient(), FailingParser())
    answer, _ = agent.handle_message("show my meetings", AgentState())
    assert "Intro" in answer or "matching bookings" in answer.lower()


def test_agent_picks_closest_booking_time():
    bookings = [
        BookingSummary(uid="ten", title="Intro", status="accepted", start="2026-06-12T17:00:00Z"),
        BookingSummary(uid="ten-thirty", title="Intro", status="accepted", start="2026-06-12T17:30:00Z"),
    ]

    class ListingClient(FakeClient):
        def list_bookings(self, **kwargs):
            return bookings

    cmd = ParsedCommand(intent=Intent.CANCEL, start="2026-06-12T17:00:00Z")
    agent = SchedulingAgent(settings(), ListingClient(), FakeParser(cmd))
    answer, _ = agent.handle_message("cancel my meeting at 10am on June 12", AgentState(recent_bookings=bookings))
    assert "ten" in answer
    assert "ten-thirty" not in answer


def test_agent_cancels_by_time_without_uid():
    bookings = [
        BookingSummary(uid="abc123", title="Intro", status="accepted", start="2026-06-12T17:00:00Z"),
    ]

    class ListingClient(FakeClient):
        def list_bookings(self, **kwargs):
            return bookings

    cmd = ParsedCommand(intent=Intent.CANCEL, start="2026-06-12T17:00:00Z")
    agent = SchedulingAgent(settings(), ListingClient(), FakeParser(cmd))
    answer, state = agent.handle_message("cancel my meeting at 10am on June 12", AgentState(recent_bookings=bookings))
    assert "Please confirm" in answer
    assert "abc123" in answer


def test_agent_accepts_uid_follow_up_for_pending_cancel():
    class MultiClient(FakeClient):
        def list_bookings(self, **kwargs):
            return [
                BookingSummary(uid="abc123", title="Intro", status="accepted", start="2026-06-11T20:00:00Z"),
                BookingSummary(uid="def456", title="Intro", status="accepted", start="2026-06-12T20:00:00Z"),
            ]

    cmd = ParsedCommand(intent=Intent.CANCEL)
    agent = SchedulingAgent(settings(), MultiClient(), FakeParser(cmd))

    class FollowUpParser:
        def parse(self, text, history=None, state=None):
            from parse_utils import extract_uid, looks_like_uid_only

            uid = extract_uid(text)
            if uid and looks_like_uid_only(text, uid):
                return ParsedCommand(intent=Intent.UNKNOWN, booking_uid=uid)
            return ParsedCommand(intent=Intent.CANCEL)

    agent.parser = FollowUpParser()
    _, state = agent.handle_message("cancel my meeting", AgentState())
    answer, state = agent.handle_message("k8HFJmDwKq8wiWLPLXSAdd", state)
    assert "Please confirm" in answer
    assert "k8HFJmDwKq8wiWLPLXSAdd" in answer


def test_agent_handles_duplicate_booking_conflict():
    class ConflictClient(FakeClient):
        def create_booking(self, request):
            raise CalcomAPIError(
                "Cal.com API error 400: User either already has booking at this time or is not available"
            )

        def list_bookings(self, **kwargs):
            status = kwargs.get("status", "upcoming")
            if status == "cancelled":
                return [
                    BookingSummary(
                        uid="existing123",
                        title="Intro",
                        status="cancelled",
                        start="2026-06-10T22:00:00Z",
                    )
                ]
            return []

        def get_slots(self, *args, **kwargs):
            return {
                "2026-06-10": [
                    {"start": "2026-06-10T22:00:00Z", "end": "2026-06-10T22:30:00Z"},
                ]
            }

    cmd = ParsedCommand(
        intent=Intent.BOOK,
        start="2026-06-10T22:00:00Z",
        attendee_name="Demo User",
        attendee_email="demo@example.com",
    )
    agent = SchedulingAgent(settings(), ConflictClient(), FakeParser(cmd))
    answer, _ = agent.handle_message("book meeting", AgentState())
    assert "couldn't book" in answer.lower()
    assert "no upcoming" in answer.lower()


def test_agent_resolves_cancel_by_time_hint():
    bookings = [
        BookingSummary(uid="abc123", title="Intro", status="accepted", start="2026-06-10T22:00:00Z"),
    ]

    class ListingClient(FakeClient):
        def list_bookings(self, **kwargs):
            return bookings

    cmd = ParsedCommand(intent=Intent.CANCEL, start="2026-06-10T22:00:00Z")
    agent = SchedulingAgent(settings(), ListingClient(), FakeParser(cmd))
    answer, state = agent.handle_message("cancel my meeting at 3pm", AgentState())
    assert "Please confirm" in answer
    assert "abc123" in answer


def test_agent_requires_confirmation_before_cancel():
    cmd = ParsedCommand(intent=Intent.CANCEL, booking_uid="abc123")
    fake_client = FakeClient()
    agent = SchedulingAgent(settings(), fake_client, FakeParser(cmd))
    answer, state = agent.handle_message("cancel abc123", AgentState())
    assert "Please confirm" in answer
    assert not fake_client.cancelled

    answer, state = agent.handle_message("yes", state)
    assert "cancelled successfully" in answer.lower()
    assert fake_client.cancelled
