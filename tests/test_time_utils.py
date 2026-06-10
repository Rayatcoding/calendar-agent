from time_utils import format_local_time, normalize_start_key, to_utc_z


def test_to_utc_z_from_offset_slot_time():
    assert to_utc_z("2026-06-12T09:00:00.000-07:00") == "2026-06-12T16:00:00Z"


def test_to_utc_z_from_zulu():
    assert to_utc_z("2026-06-12T16:00:00Z") == "2026-06-12T16:00:00Z"


def test_normalize_start_key_matches_slot_and_request():
    slot = to_utc_z("2026-06-12T09:00:00.000-07:00")
    request = to_utc_z("2026-06-12T16:00:00Z")
    assert normalize_start_key(slot) == normalize_start_key(request)
