from __future__ import annotations

import json

from config import Settings
from llm_router import MISSING_OPENAI_KEY, CommandParser
from models import AgentState, BookingSummary, Intent, ParsedCommand, SlotChoice


class ScriptedParser(CommandParser):
    """Parser with a fake OpenAI client for deterministic contract tests."""

    def __init__(self, scripts: dict[str, dict]):
        super().__init__(Settings(openai_api_key="test-key", default_timezone="America/Los_Angeles"))
        self.scripts = scripts

    def _parse_with_llm(self, text: str, history: list[dict], state: AgentState | None) -> ParsedCommand:
        payload = self.scripts[text]
        return ParsedCommand.model_validate(payload)


def test_openai_client_uses_default_base_url_when_env_is_blank(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    parser = CommandParser(Settings(openai_api_key="test-key"))
    assert parser.client is not None
    assert str(parser.client.base_url).startswith("https://")


def test_missing_openai_key_returns_clear_summary():
    parser = CommandParser(Settings(openai_api_key=None))
    cmd = parser.parse("check all my meetings")
    assert cmd.summary == MISSING_OPENAI_KEY


def test_parser_includes_session_context_in_llm_payload(monkeypatch):
    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            payload = {"intent": "list"}
            message = type("Message", (), {"content": json.dumps(payload)})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    parser = CommandParser(Settings(openai_api_key="test-key", default_timezone="America/Los_Angeles"))
    parser.client = FakeClient()
    state = AgentState(
        recent_bookings=[BookingSummary(uid="abc123", title="Intro", status="accepted")],
        candidate_slots=[SlotChoice(index=1, start="2026-06-12T17:00:00Z", label="10:00 AM")],
        pending_command=ParsedCommand(intent=Intent.BOOK, attendee_name="Jane"),
    )

    cmd = parser.parse("check all my meetings", state=state)
    assert cmd.intent == Intent.LIST

    user_payload = json.loads(captured["messages"][1]["content"])
    assert user_payload["pending_command"]["intent"] == "book"
    assert user_payload["recent_bookings"][0]["uid"] == "abc123"
    assert user_payload["candidate_slots"][0]["label"] == "10:00 AM"


def test_check_all_meetings_is_list_intent():
    cmd = ScriptedParser({"check all my meetings": {"intent": "list"}}).parse("check all my meetings")
    assert cmd.intent == Intent.LIST


def test_list_upcoming_without_day_filter():
    cmd = ScriptedParser(
        {"please list the upcoming bookings": {"intent": "list"}}
    ).parse("please list the upcoming bookings")
    assert cmd.intent == Intent.LIST
    assert cmd.after_start is None
    assert cmd.before_end is None


def test_slots_on_named_date_uses_that_day():
    cmd = ScriptedParser(
        {
            "what slots are available on June 12?": {
                "intent": "slots",
                "after_start": "2026-06-12T07:00:00Z",
                "before_end": "2026-06-13T07:00:00Z",
            }
        }
    ).parse("what slots are available on June 12?")
    assert cmd.intent == Intent.SLOTS
    assert cmd.after_start == "2026-06-12T07:00:00Z"
    assert cmd.before_end == "2026-06-13T07:00:00Z"


def test_list_tomorrow_keeps_day_filter():
    cmd = ScriptedParser(
        {
            "what's on my calendar tomorrow?": {
                "intent": "list",
                "after_start": "2026-06-10T07:00:00Z",
                "before_end": "2026-06-11T07:00:00Z",
            }
        }
    ).parse("what's on my calendar tomorrow?")
    assert cmd.intent == Intent.LIST
    assert cmd.after_start is not None
    assert cmd.before_end is not None


def test_reschedule_extracts_uid_and_time():
    cmd = ScriptedParser(
        {
            "Reschedule booking sD2Tp2oagtvSKKiwi6wcX7 to Friday at 4pm": {
                "intent": "reschedule",
                "booking_uid": "sD2Tp2oagtvSKKiwi6wcX7",
                "start": "2026-06-12T23:00:00Z",
            }
        }
    ).parse("Reschedule booking sD2Tp2oagtvSKKiwi6wcX7 to Friday at 4pm")
    assert cmd.intent == Intent.RESCHEDULE
    assert cmd.booking_uid == "sD2Tp2oagtvSKKiwi6wcX7"
    assert cmd.start is not None


def test_cancel_extracts_time_hint():
    cmd = ScriptedParser(
        {
            "cancel my meeting at 3pm": {
                "intent": "cancel",
                "start": "2026-06-12T22:00:00Z",
            }
        }
    ).parse("cancel my meeting at 3pm")
    assert cmd.intent == Intent.CANCEL
    assert cmd.start is not None


def test_complaint_about_booked_is_not_a_book_request():
    cmd = ScriptedParser(
        {
            "then why you say 3pm 6/10/2026 already booked": {
                "intent": "unknown",
            }
        }
    ).parse("then why you say 3pm 6/10/2026 already booked")
    assert cmd.intent == Intent.UNKNOWN


def test_uid_only_follow_up_is_not_parsed_as_datetime():
    cmd = ScriptedParser(
        {
            "k8HFJmDwKq8wiWLPLXSAdd": {
                "intent": "unknown",
                "booking_uid": "k8HFJmDwKq8wiWLPLXSAdd",
            }
        }
    ).parse("k8HFJmDwKq8wiWLPLXSAdd")
    assert cmd.intent == Intent.UNKNOWN
    assert cmd.booking_uid == "k8HFJmDwKq8wiWLPLXSAdd"
    assert cmd.start is None
