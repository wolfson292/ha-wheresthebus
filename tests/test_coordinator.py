"""Tests for the roster/scan parsing helpers."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import degrees

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.wheresthebus.const import (
    ARRIVAL_HISTORY_LIMIT,
    COORD_PRECISION,
    OUTLIER_FLOOR_MINUTES,
    SCAN_DROPOFF,
    SCAN_PICKUP,
    TRACK_SAMPLE_LIMIT,
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
    _merge_arrivals,
    _pair_riders,
    _reject_outliers,
    _trim_per_run,
    approach_window,
    classify_scans,
    parse_bus_status,
    parse_stop_time,
    predict_school_arrival,
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


LADDER = (3.0, 2.0, 1.0, 0.5)


# The stop these fixtures are built around, and the earth radius the haversine
# uses, so a wanted distance can be turned into a position exactly. Invented,
# like every other coordinate in the tests — see tests/fixtures.py.
_STOP = (40.71550, -74.00200)
_EARTH_RADIUS_MILES = 3958.7613


def _reading(when: datetime, value: str) -> State:
    """Build a recorded bus position the given distance from the stop.

    The replay reads positions now rather than distances, because a route
    match needs to know where the bus was. Tests still speak in distances, so
    this places the bus due north of the stop at exactly that range — due
    north because a haversine along a meridian is simply the radius times the
    angle, which makes the round trip exact rather than approximate.
    """
    try:
        miles = float(value)
    except ValueError:
        # "unknown" / "unavailable": a gap in the record, with no position.
        return State("device_tracker.x_bus", value, last_updated=when)

    return State(
        "device_tracker.x_bus",
        "not_home",
        {
            # A hair inside the asked-for range. The round trip through
            # degrees and back can land a few femtometres the wrong side of a
            # rung, and a test that says "3.0" means "at the three mile rung",
            # not "fractionally outside it". The real feed is rounded to a
            # tenth of a mile, so the question never arises in production.
            "latitude": _STOP[0] + degrees(miles * (1 - 1e-12) / _EARTH_RADIUS_MILES),
            "longitude": _STOP[1],
            "stop_latitude": _STOP[0],
            "stop_longitude": _STOP[1],
        },
        last_updated=when,
    )


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

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    assert len(arrivals) == 1
    assert arrivals[0].run == "am"
    assert dt_util.as_local(arrivals[0].arrival).strftime("%H:%M:%S") == "07:56:47"
    # 07:52:47 to 07:56:47 — the final leg, which is the point of the exercise.
    # Rung 2 is the 1.0 mile anchor: 07:52:47 to 07:56:47.
    assert arrivals[0].legs[2] == 240


def test_reconstruct_arrivals_ignores_the_early_decoy_pass() -> None:
    """A pass at 06:13 is a different route and must not become an arrival."""
    states = [
        _reading(_local(6, 13), "0.0"),
        _reading(_local(6, 20), "3.0"),
    ]

    assert reconstruct_arrivals(states, _rider(), 0.3, LADDER) == []


def test_reconstruct_arrivals_ignores_a_run_that_never_reached_the_stop() -> None:
    """Half a mile out is not an arrival, however close it looks."""
    states = [
        _reading(_local(7, 50), "1.4"),
        _reading(_local(7, 56), "0.5"),
        _reading(_local(8, 2), "2.0"),
    ]

    assert reconstruct_arrivals(states, _rider(), 0.3, LADDER) == []


def test_reconstruct_arrivals_skips_unparseable_readings() -> None:
    """Unavailable and unknown rows are gaps, not distances."""
    states = [
        _reading(_local(7, 49), "2.5"),
        _reading(_local(7, 50), "unavailable"),
        _reading(_local(7, 52, 47), "0.9"),
        _reading(_local(7, 54), "unknown"),
        _reading(_local(7, 56, 47), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    assert len(arrivals) == 1
    # Rung 2 is the 1.0 mile anchor: 07:52:47 to 07:56:47.
    assert arrivals[0].legs[2] == 240


def test_reconstruct_arrivals_separates_the_two_daily_runs() -> None:
    """Morning and afternoon are learned independently."""
    states = [
        _reading(_local(7, 52), "0.9"),
        _reading(_local(7, 56), "0.0"),
        _reading(_local(17, 25), "0.8"),
        _reading(_local(17, 30), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    assert [item.run for item in arrivals] == ["am", "pm"]


def test_reconstruct_arrivals_records_every_rung_crossed() -> None:
    """Each anchor the bus passes gets its own final-leg duration.

    One anchor at a mile left everything further out running on the clock
    median: on 3 Sep the bus was 2.2 miles out and six minutes away while the
    estimate still said fifteen.
    """
    states = [
        # Starts outside the widest rung: a rung is crossed by moving from
        # outside it to inside, so the first reading can only be a baseline.
        _reading(_local(7, 45), "3.6"),
        _reading(_local(7, 46, 47), "3.0"),
        _reading(_local(7, 50, 47), "2.0"),
        _reading(_local(7, 52, 47), "1.0"),
        _reading(_local(7, 55, 47), "0.5"),
        _reading(_local(7, 56, 47), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    assert len(arrivals) == 1
    # Ten minutes out at three miles, one minute out at half a mile.
    assert arrivals[0].legs == {0: 600, 1: 360, 2: 240, 3: 60}


def test_reconstruct_arrivals_records_only_rungs_actually_crossed() -> None:
    """A bus first seen close by has no timing for the rungs it was inside.

    Being inside a rung is not the same as having been watched crossing it.
    On 14 Sep the bus sat parked at exactly 3.0 miles from 06:58, and the
    first reading once watching began at 07:16 was taken as "just crossed
    three miles". The typical nine-and-a-half minute leg was measured from
    there, predicting 07:25 for a bus that arrived at 08:01, and the five
    minute warning duly went out at 07:20.
    """
    states = [
        _reading(_local(7, 55, 47), "0.5"),
        _reading(_local(7, 56, 47), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    # It was already inside every rung when first seen, so not one of them can
    # honestly be timed. The estimate falls through to the clock median rather
    # than inventing a crossing.
    assert arrivals[0].legs == {}


def _scan(when: datetime, kind: str) -> ScanEvent:
    """Build a classified scan."""
    return ScanEvent(
        timestamp=dt_util.as_utc(when), location="", method="", bus="", kind=kind
    )


def test_predict_school_arrival_learns_from_the_drop_off_scans() -> None:
    """The school records its own arrivals; the ride does not need timing."""
    student = _rider()
    student.scans = [
        _scan(_local(8, 1, day=1), SCAN_PICKUP),
        _scan(_local(9, 29, day=1), SCAN_DROPOFF),
        _scan(_local(8, 3, day=2), SCAN_PICKUP),
        _scan(_local(9, 31, day=2), SCAN_DROPOFF),
        _scan(_local(7, 58, day=3), SCAN_PICKUP),
        _scan(_local(9, 30, day=3), SCAN_DROPOFF),
    ]

    prediction = predict_school_arrival(student, _local(7, 45, day=4))

    assert prediction is not None
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "09:30"
    assert prediction.samples == 3
    # Pickup to drop-off on the same morning, which is what fills the bar.
    assert prediction.ride_minutes == 88


def test_predict_school_arrival_ignores_the_afternoon() -> None:
    """Afternoon scans belong to the ride home, not the ride to school."""
    student = _rider()
    student.scans = [
        _scan(_local(16, 19, day=1), SCAN_PICKUP),
        _scan(_local(17, 30, day=1), SCAN_DROPOFF),
    ]

    assert predict_school_arrival(student, _local(7, 45, day=2)) is None


def test_predict_school_arrival_rejects_a_badly_late_morning() -> None:
    """One morning stuck in traffic must not drag the estimate."""
    student = _rider()
    student.scans = [
        _scan(_local(9, 29, day=1), SCAN_DROPOFF),
        _scan(_local(9, 30, day=2), SCAN_DROPOFF),
        _scan(_local(9, 31, day=3), SCAN_DROPOFF),
        _scan(_local(10, 40, day=4), SCAN_DROPOFF),
    ]

    prediction = predict_school_arrival(student, _local(7, 45, day=5))

    assert prediction is not None
    assert prediction.samples == 3
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "09:30"


def _arrival(day: int, run: str, legs: dict[int, int], micro: int = 0) -> RunArrival:
    """Build an arrival on a given September morning."""
    return RunArrival(
        run=run,
        arrival=dt_util.as_utc(_local(8, 1, day=day).replace(microsecond=micro)),
        closest=0.0,
        legs=legs,
    )


def test_merge_arrivals_prefers_the_record_that_knows_more() -> None:
    """A replay carrying the full ladder must beat an older single rung.

    The two differ by microseconds — the live path stamps the clock when the
    poll landed, a replay uses the recorder's stamp for the same reading — so
    matching on timestamp discarded the fuller record as already known, and
    the ladder never populated.
    """
    existing = [_arrival(1, "am", {2: 360}, micro=116985)]
    fresh = [_arrival(1, "am", {0: 480, 1: 330, 2: 240, 3: 120}, micro=116702)]

    merged = _merge_arrivals(existing, fresh)

    assert len(merged) == 1
    assert merged[0].legs == {0: 480, 1: 330, 2: 240, 3: 120}


def test_merge_arrivals_collapses_duplicates_already_stored() -> None:
    """Duplicates written by earlier versions inflated the sample count."""
    existing = [
        _arrival(1, "am", {}, micro=116702),
        _arrival(1, "am", {2: 360}, micro=116985),
    ]

    merged = _merge_arrivals(existing, [])

    assert len(merged) == 1
    assert merged[0].legs == {2: 360}


def test_merge_arrivals_keeps_the_two_runs_of_a_day_apart() -> None:
    """Morning and afternoon are separate arrivals, not duplicates."""
    existing = [_arrival(1, "am", {2: 360}), _arrival(1, "pm", {2: 269})]

    merged = _merge_arrivals(existing, [])

    assert len(merged) == 2
    assert {item.run for item in merged} == {"am", "pm"}


def test_merge_arrivals_keeps_the_same_run_on_different_days() -> None:
    """Consecutive mornings are distinct arrivals."""
    merged = _merge_arrivals(
        [_arrival(1, "am", {2: 360})], [_arrival(2, "am", {2: 270})]
    )

    assert len(merged) == 2


def test_pair_riders_reads_the_substitute_bus() -> None:
    """The API names a replacement vehicle in the sub field."""
    buses = [{"childId": 1, "busNo": "2563", "routeNo": "2563", "sub": "1962"}]

    students = _pair_riders(buses, [])

    assert students[1].bus_number == "2563"
    assert students[1].substitute_bus == "1962"


def test_pair_riders_treats_an_empty_substitute_as_none() -> None:
    """An empty string is the normal case, not a bus called ''."""
    buses = [{"childId": 1, "busNo": "2563", "routeNo": "2563", "sub": ""}]

    assert _pair_riders(buses, [])[1].substitute_bus is None


def test_run_window_prefers_the_learned_arrival_over_the_timetable() -> None:
    """The published time can be far enough out to make the window useless.

    The afternoon timetable here reads 17:48 against a real arrival near
    17:20, so a timetable-centred window opened at 17:18 — after the bus had
    already crossed the 3, 2 and 1 mile rungs.
    """
    reference = _local(17, 0, day=11)
    timetable = run_window(parse_stop_time("5:48 P.M."), reference)
    learned = run_window(
        parse_stop_time("5:48 P.M."), reference, parse_stop_time("5:20 P.M.")
    )

    assert timetable is not None
    assert learned is not None
    assert timetable[0].strftime("%H:%M") == "17:18"
    assert learned[0].strftime("%H:%M") == "16:50"


def test_approach_window_opens_before_the_arrival_window() -> None:
    """The outer rungs are crossed well before an arrival window would open."""
    reference = _local(17, 0, day=11)
    arriving = run_window(None, reference, parse_stop_time("5:20 P.M."))
    watching = approach_window(None, reference, parse_stop_time("5:20 P.M."))

    assert arriving is not None
    assert watching is not None
    assert watching[0] < arriving[0]
    assert watching[0].strftime("%H:%M") == "16:35"
    # Both close together: a bus still far out long after it was due is on
    # some other errand, not a late approach.
    assert watching[1] == arriving[1]


def test_reconstruct_recovers_an_arrival_a_timetable_window_would_miss() -> None:
    """A bus that beats its timetable by more than the window is invisible.

    The afternoon timetable reads 17:48, so the arrival window runs 17:18 to
    18:18. An arrival at 17:12 — ordinary here, where the bus really comes
    around 17:20 — falls outside it and is never learned, which is how the
    afternoon stayed stuck on five samples.
    """
    states = [
        _reading(_local(17, 4, day=11), "3.1"),
        _reading(_local(17, 6, day=11), "2.1"),
        _reading(_local(17, 9, day=11), "0.9"),
        _reading(_local(17, 12, day=11), "0.1"),
    ]

    on_timetable = reconstruct_arrivals(states, _rider(), 0.3, LADDER)
    on_learned = reconstruct_arrivals(
        states, _rider(), 0.3, LADDER, {"pm": parse_stop_time("5:20 P.M.")}
    )

    assert on_timetable == []
    assert len(on_learned) == 1
    assert sorted(on_learned[0].legs) == [0, 1, 2, 3]
    # 3.1 is still outside the 3 mile rung; it is crossed at 17:06.
    assert on_learned[0].legs[0] == 6 * 60


def test_a_crossing_is_discarded_when_the_bus_turns_back_out() -> None:
    """On 11 Sep the bus touched 2.7 miles, drifted to 3.8, then came in.

    Anchoring on the first touch would have put the arrival eight minutes
    early, so a crossing only counts while the bus keeps closing.
    """
    states = [
        # Outside the rung first, so the 17:05 touch is a genuine crossing and
        # the recede has something real to discard.
        _reading(_local(17, 3, day=11), "4.0"),
        _reading(_local(17, 5, day=11), "2.7"),
        _reading(_local(17, 11, day=11), "3.8"),
        _reading(_local(17, 13, day=11), "2.8"),
        _reading(_local(17, 20, day=11), "0.1"),
    ]

    arrivals = reconstruct_arrivals(
        states, _rider(), 0.3, LADDER, {"pm": parse_stop_time("5:20 P.M.")}
    )

    assert arrivals[0].recedes == 1
    # Seven minutes from the real crossing at 17:13, not fifteen from 17:05.
    assert arrivals[0].legs[0] == 7 * 60


def test_the_bus_leaving_again_does_not_undo_its_approach() -> None:
    """Readings after the stop are the bus departing, not the approach failing."""
    states = [
        _reading(_local(7, 50), "2.5"),
        _reading(_local(7, 52, 47), "0.9"),
        _reading(_local(7, 56, 47), "0.0"),
        _reading(_local(8, 4), "1.3"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    assert arrivals[0].recedes == 0
    assert arrivals[0].legs[2] == 240


def test_the_approach_track_is_kept_oldest_first_relative_to_arrival() -> None:
    """The ladder records four instants; the track records the shape between."""
    states = [
        _reading(_local(7, 50), "2.5"),
        _reading(_local(7, 54, 47), "0.9"),
        _reading(_local(7, 56, 47), "0.0"),
    ]

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    # (seconds before arrival, latitude, longitude), oldest first. These
    # fixtures place the bus due north of the stop, so only the latitude moves.
    track = arrivals[0].track
    assert [age for age, _, _ in track] == [407, 120, 0]
    assert [lon for _, _, lon in track] == [round(_STOP[1], COORD_PRECISION)] * 3
    # Closing on the stop: each latitude nearer than the last.
    assert [lat for _, lat, _ in track] == sorted(
        (lat for _, lat, _ in track), reverse=True
    )


def test_the_track_keeps_the_samples_nearest_the_arrival() -> None:
    """When the cap bites it is the far end that goes.

    The estimate hangs on the last few miles, so those are the samples worth
    keeping when a long approach will not fit.
    """
    # Inside the approach window, which opens 45 minutes before the 07:56
    # centre, and dense enough that the cap has to bite.
    start = _local(7, 15, day=3)
    states = [
        _reading(start + timedelta(seconds=10 * step), "1.0")
        for step in range(TRACK_SAMPLE_LIMIT + 100)
    ]
    states.append(_reading(_local(7, 56, 47), "0.0"))

    arrivals = reconstruct_arrivals(states, _rider(), 0.3, LADDER)

    track = arrivals[0].track
    assert len(track) == TRACK_SAMPLE_LIMIT
    assert track[-1][0] == 0  # the arrival itself, at the stop
    # Oldest first, and every retained sample is nearer the arrival than the
    # ones the cap dropped.
    assert [point[0] for point in track] == sorted(
        (point[0] for point in track), reverse=True
    )


def test_a_bus_parked_inside_a_rung_is_not_treated_as_crossing_it() -> None:
    """Replays 14 Sep, when a parked bus produced a five-minute warning at 07:20.

    The decoy pass came and went before watching began, leaving the bus parked
    at exactly 3.0 miles. The first reading once the window opened was taken as
    "just crossed three miles"; the typical nine-and-a-half minute leg was
    measured from there, predicting 07:25 for a bus that arrived at 08:01.

    Being inside a rung is not the same as having been seen cross it.
    """
    states = [
        # Parked at the rung for the whole of the watched period, then a real
        # approach starting at 07:50.
        _reading(_local(7, 16, 17, day=14), "3.0"),
        _reading(_local(7, 30, day=14), "3.0"),
        _reading(_local(7, 50, 48, day=14), "2.9"),
        _reading(_local(7, 58, day=14), "1.4"),
        _reading(_local(8, 1, day=14), "0.1"),
    ]

    arrivals = reconstruct_arrivals(
        states, _rider(), 0.3, LADDER, {"am": parse_stop_time("8:01 A.M.")}
    )

    assert len(arrivals) == 1
    legs = arrivals[0].legs
    # Never watched crossing 3 miles — it was already there — so that rung
    # carries no timing at all rather than a bogus one.
    assert 0 not in legs
    # The rungs it was genuinely seen crossing are timed as usual.
    assert legs[1] == 3 * 60  # 2 miles at 07:58 -> 08:01
    # It was still at 1.4 at 07:58 and inside 0.3 by 08:01, so the inner rungs
    # were crossed somewhere in that gap and can only be timed to the reading
    # that found it inside. Coarse sampling, honestly recorded.
    assert legs[2] == 0
    assert legs[3] == 0


def test_the_band_describes_an_ordinary_journey_not_the_worst_one() -> None:
    """A range wide enough to always be right is worth nothing.

    One bus stuck behind a freight train would drag the worst case out for
    weeks. The band is the range of ORDINARY journeys, so an exceptional day
    can and does fall outside it — which is the right way round.
    """
    # Four tidy journeys and one that took an extra half hour.
    remainders = sorted([600, 640, 660, 700, 2400])

    usual, dropped = _reject_outliers(remainders, floor=OUTLIER_FLOOR_MINUTES * 60)

    assert dropped == 1
    assert usual == [600, 640, 660, 700]
    # Under two minutes wide, rather than the thirty the bad day would give.
    assert usual[-1] - usual[0] == 100
    # And the bad day is genuinely outside the band, not clamped onto its edge.
    assert usual[-1] < 2400


def test_too_few_journeys_to_judge_keeps_them_all() -> None:
    """Two journeys cannot tell you which of them is the anomaly."""
    assert _reject_outliers([600, 2400], floor=OUTLIER_FLOOR_MINUTES * 60) == (
        [600, 2400],
        0,
    )


def test_a_record_with_the_route_beats_one_with_only_the_rungs() -> None:
    """Upgrading to positions must not lose to the record it replaces.

    A stored arrival written before positions were kept has its four rungs and
    an empty track. The replay that rebuilds it has the same four rungs AND
    the route. Ranking on rungs alone made that a tie, the incumbent won, and
    the positions were discarded the moment they arrived — leaving the route
    match with nothing to work from, on that restart and every one after.
    """
    when = dt_util.as_utc(_local(8, 1))
    rungs_only = RunArrival(
        run="am", arrival=when, closest=0.1, legs={0: 600, 1: 360, 2: 240, 3: 60}
    )
    with_route = RunArrival(
        run="am",
        arrival=when,
        closest=0.1,
        legs={0: 600, 1: 360, 2: 240, 3: 60},
        track=[(600, 26.49, -81.86), (0, 26.45, -81.83)],
    )

    merged = _merge_arrivals([rungs_only], [with_route])

    assert len(merged) == 1
    assert merged[0].track == with_route.track


def test_more_rungs_still_wins_over_a_longer_track() -> None:
    """Rungs first, route second — a fuller ladder is the better record."""
    when = dt_util.as_utc(_local(8, 1))
    thin_ladder = RunArrival(
        run="am",
        arrival=when,
        closest=0.1,
        legs={3: 60},
        track=[(0, 26.45, -81.83)] * 9,
    )
    full_ladder = RunArrival(
        run="am", arrival=when, closest=0.1, legs={0: 600, 1: 360, 2: 240, 3: 60}
    )

    merged = _merge_arrivals([thin_ladder], [full_ladder])

    assert sorted(merged[0].legs) == [0, 1, 2, 3]
