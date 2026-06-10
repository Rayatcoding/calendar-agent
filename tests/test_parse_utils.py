from __future__ import annotations

from parse_utils import (
    extract_datetime_as_utc,
    extract_uid,
    looks_like_book_intent,
    looks_like_booking_time,
    looks_like_list_intent,
    looks_like_uid_only,
)


def test_extract_uid_from_plain_follow_up():
    uid = "k8HFJmDwKq8wiWLPLXSAdd"
    assert extract_uid(uid) == uid
    assert looks_like_uid_only(uid, uid)


def test_extract_datetime_from_slot_label():
    start = extract_datetime_as_utc("Fri Jun 12, 2026 at 10:00 AM", "America/Los_Angeles")
    assert start == "2026-06-12T17:00:00Z"


def test_looks_like_book_intent_for_partial_phrases():
    assert looks_like_book_intent("book a meeting")
    assert looks_like_book_intent("book meeting")
    assert looks_like_book_intent("book")


def test_looks_like_list_intent_for_show_meetings():
    assert looks_like_list_intent("show my meetings")
    assert looks_like_list_intent("check my meetings")


def test_looks_like_booking_time_for_explicit_phrase():
    start = extract_datetime_as_utc("cancel my meeting at 3pm", "America/Los_Angeles")
    assert looks_like_booking_time("cancel my meeting at 3pm", start)
