"""Tests for the roster/scan parsing helpers."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.util import dt as dt_util

from custom_components.wheresthebus.const import SCAN_DROPOFF, SCAN_PICKUP
from custom_components.wheresthebus.coordinator import (
    ScanEvent,
    _attach_scans,
    _pair_riders,
    classify_scans,
    parse_bus_status,
    parse_stop_time,
    run_window,
)

from .fixtures import ALL_RIDERS, STUDENT_SCANS, USER_INFO


def test_pair_riders_joins_bus_to_roster() -> None:
    """A single rider is joined to its childBuses entry."""
    students = _pair_riders(USER_INFO["childBuses"], ALL_RIDERS)

    assert list(students) == [12345678]
    student = students[12345678]
    assert student.name == "Robin Alex Rivera"
    assert student.student_id == "10000001"
    assert student.bus_number == "1234"
    assert student.route_number == "1234"
    assert student.school_name == "Riverside Middle School"
    assert student.am_stop_time == "7:56 A.M."
    assert student.pm_stop_time == "5:48 P.M."
    assert student.stop_address == "MAPLE RD & 3RD ST"
    assert student.stop_latitude == pytest.approx(40.71550)


def test_pair_riders_matches_two_children_on_the_same_bus() -> None:
    """Riders sharing a bus number are separated by their stop time."""
    child_buses = [
        {"childId": 1, "busNo": "12", "routeNo": "12", "busTime": "3:10"},
        {"childId": 2, "busNo": "12", "routeNo": "12", "busTime": "4:25"},
    ]
    riders = [
        {"riderName": "Second Child", "pmBusNo": "12", "pmStopTime": "4:25 P.M."},
        {"riderName": "First Child", "pmBusNo": "12", "pmStopTime": "3:10 P.M."},
    ]

    students = _pair_riders(child_buses, riders)

    assert students[1].name == "First Child"
    assert students[2].name == "Second Child"


def test_pair_riders_survives_a_missing_roster_entry() -> None:
    """A child with no allRiders record still produces a student."""
    students = _pair_riders(USER_INFO["childBuses"], [])

    assert students[12345678].name == "Rider 12345678"
    assert students[12345678].bus_number == "1234"


def test_classify_scans_uses_location_and_time_of_day() -> None:
    """Stop scans and school scans map to pickup/drop-off by time of day."""
    scans = [
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787746325),
            location="Maple Rd, Springfield",
            method="Keypad",
            bus="1234",
        ),
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787751003),
            location="Riverside Middle School",
            method="Tablet",
            bus="1234",
        ),
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787775599),
            location="Riverside Middle School",
            method="Keypad",
            bus="1234",
        ),
    ]

    classified = classify_scans(scans, "Riverside Middle School")

    assert [scan.kind for scan in classified] == [
        SCAN_PICKUP,
        SCAN_DROPOFF,
        SCAN_PICKUP,
    ]


def test_classify_scans_falls_back_to_daily_alternation() -> None:
    """With no usable location, scans alternate pickup then drop-off."""
    scans = [
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787746325),
            location="",
            method="Keypad",
            bus="1",
        ),
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787751003),
            location="",
            method="Keypad",
            bus="1",
        ),
    ]

    classified = classify_scans(scans, None)

    assert [scan.kind for scan in classified] == [SCAN_PICKUP, SCAN_DROPOFF]


def test_classify_scans_sorts_out_of_order_input() -> None:
    """Scans arriving newest-first are still ordered oldest-first."""
    scans = [
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787775599),
            location="Riverside Middle School",
            method="Keypad",
            bus="1234",
        ),
        ScanEvent(
            timestamp=dt_util.utc_from_timestamp(1787746325),
            location="Maple Rd, Springfield",
            method="Keypad",
            bus="1234",
        ),
    ]

    classified = classify_scans(scans, "Riverside Middle School")

    assert classified[0].timestamp < classified[1].timestamp
    assert classified[0].kind == SCAN_PICKUP


def test_attach_scans_matches_a_shortened_name() -> None:
    """A shortened scan name matches the full roster name."""
    students = _pair_riders(USER_INFO["childBuses"], ALL_RIDERS)

    _attach_scans(students, STUDENT_SCANS)

    student = students[12345678]
    assert len(student.scans) == 3
    assert student.last_scan is not None
    assert student.last_scan.timestamp == dt_util.utc_from_timestamp(1787775599)
    # _attach_scans leaves events unclassified; the coordinator classifies the
    # whole accumulated window after merging.
    assert all(scan.kind is None for scan in student.scans)

    student.scans = classify_scans(student.scans, student.school_name)

    assert student.last_scan_of(SCAN_PICKUP).timestamp == dt_util.utc_from_timestamp(
        1787775599
    )
    assert student.last_scan_of(SCAN_DROPOFF).timestamp == dt_util.utc_from_timestamp(
        1787751003
    )


def test_attach_scans_ignores_unknown_students() -> None:
    """A scan feed naming somebody else leaves the roster untouched."""
    students = _pair_riders(USER_INFO["childBuses"], ALL_RIDERS)
    payload = {
        "studentDetails": [
            {"studentName": "Someone Else", "studentScans": []},
            {"studentName": "Another Kid", "studentScans": []},
        ]
    }

    _attach_scans(students, payload)

    assert students[12345678].scans == []


@pytest.mark.parametrize(
    ("sts_msg", "expected"),
    [
        ("current", ("current", 0)),
        ("Current", ("current", 0)),
        ("inactive", ("inactive", None)),
        ("1 min. ago", ("stale", 1)),
        ("14 min. ago", ("stale", 14)),
        ("3 mins ago", ("stale", 3)),
        ("", (None, None)),
        (None, (None, None)),
        ("something new", (None, None)),
    ],
)
def test_parse_bus_status(
    sts_msg: str | None, expected: tuple[str | None, int | None]
) -> None:
    """The API's human-readable status splits into a state and a GPS age."""
    assert parse_bus_status(sts_msg) == expected


def test_parse_bus_status_collapses_the_minute_by_minute_churn() -> None:
    """Every "N min. ago" maps to one state, so the sensor stops churning."""
    messages = ["current", *[f"{n} min. ago" for n in range(1, 15)], "inactive"]

    states = {parse_bus_status(message)[0] for message in messages}

    assert states == {"current", "stale", "inactive"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7:56 A.M.", (7, 56)),
        ("5:48 P.M.", (17, 48)),
        ("12:05 A.M.", (0, 5)),
        ("12:30 P.M.", (12, 30)),
        ("7:56 AM", (7, 56)),
        ("11:59 p.m.", (23, 59)),
    ],
)
def test_parse_stop_time(value: str, expected: tuple[int, int]) -> None:
    """Scheduled stop times parse regardless of meridiem punctuation."""
    parsed = parse_stop_time(value)

    assert parsed is not None
    assert (parsed.hour, parsed.minute) == expected


@pytest.mark.parametrize("value", ["", None, "sometime", "7:75 A.M."])
def test_parse_stop_time_rejects_junk(value: str | None) -> None:
    """Unparseable stop times yield None rather than a wrong time."""
    assert parse_stop_time(value) is None


def test_run_window_brackets_the_scheduled_time() -> None:
    """The window is 30 minutes either side of the scheduled stop."""
    reference = dt_util.as_utc(datetime(2026, 8, 31, 12, 0, tzinfo=UTC))

    start, end = run_window(parse_stop_time("7:56 A.M."), reference)

    assert (start.hour, start.minute) == (7, 26)
    assert (end.hour, end.minute) == (8, 26)


def test_run_window_excludes_the_early_decoy_pass() -> None:
    """The 06:13 pass on an unrelated route falls outside the pickup window."""
    reference = dt_util.as_utc(datetime(2026, 8, 31, 12, 0, tzinfo=UTC))
    start, end = run_window(parse_stop_time("7:56 A.M."), reference)

    decoy = dt_util.as_local(reference).replace(hour=6, minute=13)
    real = dt_util.as_local(reference).replace(hour=8, minute=2)

    assert not start <= decoy <= end
    assert start <= real <= end
