"""Optional live Cal.com smoke tests. Skipped unless CALCOM_RUN_LIVE_TESTS=1."""

from __future__ import annotations

import os

import pytest

from agent import SchedulingAgent
from calcom_client import CalcomClient
from config import settings
from models import AgentState
from time_utils import to_utc_z


pytestmark = pytest.mark.skipif(
    os.getenv("CALCOM_RUN_LIVE_TESTS") != "1",
    reason="Set CALCOM_RUN_LIVE_TESTS=1 to run live Cal.com API smoke tests.",
)


@pytest.fixture(scope="module")
def client() -> CalcomClient:
    if not settings.has_calcom_auth or not settings.has_event_type_config:
        pytest.skip("Cal.com credentials are not configured")
    return CalcomClient(settings)


def test_live_slots_available_on_june_12(client: CalcomClient):
    slots = client.get_slots("2026-06-12T07:00:00Z", "2026-06-13T07:00:00Z")
    assert slots, "Expected open slots on June 12 for this account"


def test_live_book_from_real_slot(client: CalcomClient):
    slots = client.get_slots("2026-06-12T07:00:00Z", "2026-06-13T07:00:00Z")
    first_day = next(iter(slots.values()))
    first_slot = first_day[0]["start"]
    slot_utc = to_utc_z(first_slot)

    agent = SchedulingAgent(settings, client)
    answer, state = agent.handle_message(
        f"book a meeting on {slot_utc}",
        AgentState(),
    )
    assert "booked successfully" in answer.lower() or "already" in answer.lower() or "open slots" in answer.lower()
