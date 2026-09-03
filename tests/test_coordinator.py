"""Tests for the roster/scan parsing helpers."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.wheresthebus.const import (
    ARRIVAL_HISTORY_LIMIT,
    SCAN_DROPOFF,
    SCAN_PICKUP,
)
from custom_components.wheresthebus.const import (
    RUN_AM as SCAN_RUN_AM,
)
from custom_components.wheresthebus.const import (
    RUN_PM as SCAN_RUN_PM,
)
from custom_components.wheresthebus.coordinator import (
    RunArrival,
    ScanEvent,
    Student,
    _attach_scans,
    _pair_riders,
    _reject_outliers,
    _trim_per_run,
    classify_scans,
    parse_bus_status,
    parse_stop_time,
    reconstruct_arrivals,
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


def test_trim_per_run_keeps_each_run_independently() -> None:
    """A busy run must not evict the other run's history."""
    base = dt_util.utc_from_timestamp(1787746325)
    history = [
        RunArrival(run=SCAN_RUN_AM, arrival=base, closest=0.0),
        *[
            RunArrival(
                run=SCAN_RUN_PM,
                arrival=base + timedelta(days=day),
                closest=0.0,
            )
            for day in range(1, ARRIVAL_HISTORY_LIMIT + 6)
        ],
    ]

    kept = _trim_per_run(history)

    # The single morning survives a flood of afternoons.
    assert [item for item in kept if item.run == SCAN_RUN_AM]
    assert len([item for item in kept if item.run == SCAN_RUN_PM]) == (
        ARRIVAL_HISTORY_LIMIT
    )


def test_reject_outliers_keeps_a_normal_run_intact() -> None:
    """Ordinary minute-to-minute variation is the pattern, not an anomaly."""
    times = sorted([476, 481, 482, 484, 478])

    kept, excluded = _reject_outliers(times)

    assert excluded == 0
    assert kept == times


def test_reject_outliers_drops_a_badly_late_bus() -> None:
    """A bus 40 minutes late is a bad day and must not skew the estimate."""
    times = sorted([476, 481, 482, 484, 478, 522])

    kept, excluded = _reject_outliers(times)

    assert excluded == 1
    assert 522 not in kept


def test_reject_outliers_tolerates_a_normal_delay_on_a_tight_run() -> None:
    """A tight run must not treat a five minute delay as an outlier.

    Without a floor the scaled deviation of a run clustered inside two minutes
    is small enough to reject an entirely ordinary late morning.
    """
    times = sorted([480, 480, 481, 481, 482, 486])

    kept, excluded = _reject_outliers(times)

    assert excluded == 0
    assert 486 in kept


def test_reject_outliers_needs_enough_samples_to_judge() -> None:
    """Two arrivals cannot tell you which of them is the anomaly."""
    times = sorted([480, 540])

    kept, excluded = _reject_outliers(times)

    assert excluded == 0
    assert kept == times


def test_reject_outliers_never_discards_everything() -> None:
    """However strange the data, some estimate beats no estimate."""
    times = sorted([100, 500, 900])

    kept, excluded = _reject_outliers(times)

    assert kept
    assert excluded < len(times)


def _reading(when: datetime, value: str) -> State:
    """Build a recorded distance reading."""
    return State("sensor.x_distance_to_stop", value, last_updated=when)


def _local(hour: int, minute: int, second: int = 0, day: int = 3) -> datetime:
    """Return a local wall-clock time on a school morning."""
    return datetime(
        2026, 9, day, hour, minute, second, tzinfo=dt_util.get_default_time_zone()
    )


def _rider() -> Student:
    """Return a rider scheduled for a 07:56 pickup and 17:48 drop-off."""
    return Student(
        child_id=1,
        name="Rider",
        am_scheduled=parse_stop_time("7:56 A.M."),
        pm_scheduled=parse_stop_time("5:48 P.M."),
    )


def test_reconstruct_arrivals_recovers_a_morning_from_history() -> None:
    """Replays 3 Sep: a mile out at 07:52, at the stop at 07:56."""
    states = [
        _reading(_local(7, 48), "2.9"),
        _reading(_local(7, 52, 47), "0.9"),
        _reading(_local(7, 54), "0.5"),
        _reading(_local(7, 56, 47), "0.0"),
        _reading(_local(8, 4), "1.3"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, 1.0)

    assert len(arrivals) == 1
    assert arrivals[0].run == "am"
    assert dt_util.as_local(arrivals[0].arrival).strftime("%H:%M:%S") == "07:56:47"
    # 07:52:47 to 07:56:47 — the final leg, which is the point of the exercise.
    assert arrivals[0].approach_seconds == 240


def test_reconstruct_arrivals_ignores_the_early_decoy_pass() -> None:
    """A pass at 06:13 is a different route and must not become an arrival."""
    states = [
        _reading(_local(6, 13), "0.0"),
        _reading(_local(6, 20), "3.0"),
    ]

    assert reconstruct_arrivals(states, _rider(), 0.3, 1.0) == []


def test_reconstruct_arrivals_ignores_a_run_that_never_reached_the_stop() -> None:
    """Half a mile out is not an arrival, however close it looks."""
    states = [
        _reading(_local(7, 50), "1.4"),
        _reading(_local(7, 56), "0.5"),
        _reading(_local(8, 2), "2.0"),
    ]

    assert reconstruct_arrivals(states, _rider(), 0.3, 1.0) == []


def test_reconstruct_arrivals_skips_unparseable_readings() -> None:
    """Unavailable and unknown rows are gaps, not distances."""
    states = [
        _reading(_local(7, 50), "unavailable"),
        _reading(_local(7, 52, 47), "0.9"),
        _reading(_local(7, 54), "unknown"),
        _reading(_local(7, 56, 47), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, 1.0)

    assert len(arrivals) == 1
    assert arrivals[0].approach_seconds == 240


def test_reconstruct_arrivals_separates_the_two_daily_runs() -> None:
    """Morning and afternoon are learned independently."""
    states = [
        _reading(_local(7, 52), "0.9"),
        _reading(_local(7, 56), "0.0"),
        _reading(_local(17, 25), "0.8"),
        _reading(_local(17, 30), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, 1.0)

    assert [item.run for item in arrivals] == ["am", "pm"]
