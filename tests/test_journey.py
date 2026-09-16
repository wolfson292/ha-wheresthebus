"""Tests for the journey stage machine.

Each of these is a moment from a real school day that the old ten-branch
automation got wrong, expressed as a single call at a single instant. That is
the point of moving the logic here: none of them needed a Home Assistant
instance, a notification, or a phone to reproduce.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util

from custom_components.wheresthebus.journey import journey_stage

LADDER_OUTER = 3.0
THRESHOLD = 0.3


def _at(hour: int, minute: int, second: int = 0, day: int = 14) -> datetime:
    """Return a local instant on a school day."""
    return datetime(
        2026, 9, day, hour, minute, second, tzinfo=dt_util.get_default_time_zone()
    )


def _stage(**overrides):
    """Call the stage machine with a sensible idle baseline."""
    args = {
        "now": _at(7, 50),
        "distance": 5.0,
        "arrival_threshold": THRESHOLD,
        "outer_rung": LADDER_OUTER,
        "next_arrival": None,
        "next_run": None,
        "prediction_source": None,
        "school_arrival": None,
        "last_pickup": None,
        "last_dropoff": None,
        "approach_open": False,
        "ride_home_ended": None,
    }
    args.update(overrides)
    return journey_stage(**args)


def test_nothing_happening_is_idle() -> None:
    """Most of the day is not a school run."""
    assert _stage(now=_at(13, 0)).stage == "idle"
    assert _stage(now=_at(13, 0)).active is False


def test_the_morning_approach_fills_by_distance() -> None:
    """Before boarding, how close the bus is IS the progress."""
    journey = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1),
        next_run="am",
        prediction_source="learned",
    )

    assert journey.stage == "to_stop"
    assert journey.progress == 50  # halfway in from the 3 mile rung
    assert journey.target == _at(8, 1)


def test_an_unlearned_run_does_not_announce_an_approach() -> None:
    """Anchored to a timetable 20 minutes out, this fired after the bus had gone."""
    journey = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1),
        next_run="am",
        prediction_source="scheduled",
    )

    assert journey.stage == "idle"


def test_reaching_the_stop_in_the_morning_says_board_now() -> None:
    """At the stop, the morning stage is 'go', not 'arrived'."""
    journey = _stage(
        now=_at(8, 1),
        distance=0.1,
        approach_open=True,
        next_arrival=_at(8, 1),
        next_run="am",
        prediction_source="learned",
    )

    assert journey.stage == "at_stop"
    assert journey.progress == 100


def test_the_ride_to_school_fills_by_elapsed_time() -> None:
    """Distance to the home stop says nothing while the bus works its route."""
    journey = _stage(
        now=_at(8, 43),
        distance=6.0,
        last_pickup=_at(8, 1),
        school_arrival=_at(9, 25),
    )

    assert journey.stage == "to_school"
    assert journey.progress == 50  # 42 minutes into an 84 minute ride
    assert journey.boarded == _at(8, 1)
    assert journey.target == _at(9, 25)


def test_the_ride_to_school_ends_when_the_school_scans_her_in() -> None:
    """The drop-off scan ends the ride and briefly says so."""
    journey = _stage(
        now=_at(9, 24),
        last_pickup=_at(8, 1),
        last_dropoff=_at(9, 23),
        school_arrival=_at(9, 25),
    )

    assert journey.stage == "at_school"
    assert journey.progress == 100


def test_the_ride_to_school_ends_even_when_the_school_never_scans() -> None:
    """Two days in three the school records nothing.

    The old automation's only stop was that scan, so on those days the bar
    filled every five minutes until noon — roughly thirty pointless pushes,
    which is what drained the iOS Live Activity budget.
    """
    journey = _stage(
        now=_at(11, 0),
        last_pickup=_at(8, 1),
        last_dropoff=None,
        # The prediction has rolled to tomorrow, as it does once today's passes.
        school_arrival=_at(9, 25, day=15),
    )

    assert journey.stage == "idle"


def test_the_ride_home_fills_by_elapsed_time() -> None:
    """The afternoon ride is measured the same way as the morning one."""
    journey = _stage(
        now=_at(16, 50),
        distance=4.0,
        last_pickup=_at(16, 14),
        next_arrival=_at(17, 26),
    )

    assert journey.stage == "from_school"
    assert journey.progress == 50
    assert journey.target == _at(17, 26)


def test_being_aboard_outranks_the_bus_merely_being_near() -> None:
    """An approach must not describe someone already on the bus as waiting."""
    journey = _stage(
        now=_at(17, 20),
        distance=1.5,
        approach_open=True,
        last_pickup=_at(16, 14),
        next_arrival=_at(17, 26),
        next_run="pm",
        prediction_source="learned",
    )

    assert journey.stage == "from_school"
    # 66 minutes into a 72 minute ride. Emphatically not the 50% the distance
    # would have given — the two scales disagreeing is what made the bar lurch
    # from 78% back to 7% every few minutes.
    assert journey.progress == 92


def test_the_ride_home_ends_at_the_stop() -> None:
    """Arriving outranks being on the way."""
    journey = _stage(
        now=_at(17, 26),
        distance=0.1,
        approach_open=True,
        last_pickup=_at(16, 14),
        next_arrival=_at(17, 26),
        next_run="pm",
        prediction_source="learned",
    )

    assert journey.stage == "home"
    assert journey.progress == 100


def test_the_ride_home_does_not_count_down_to_the_next_school_day() -> None:
    """Reproduces the 61 hour countdown of 11 Sep.

    An hour after the bus had been and gone, the activity still said "riding
    home" and counted down 3686 minutes, because next_arrival had rolled to
    Monday morning and nothing checked that the target was still today.
    """
    journey = _stage(
        now=_at(18, 35, day=11),
        distance=5.3,
        last_pickup=_at(16, 14, day=11),
        next_arrival=_at(8, 1, day=14),
    )

    assert journey.stage == "idle"


def test_a_journey_is_identified_by_its_day_and_run() -> None:
    """Two journeys a day, each with its own identity, from the clock."""
    morning = _stage(now=_at(8, 30), last_pickup=_at(8, 1), school_arrival=_at(9, 25))
    afternoon = _stage(
        now=_at(16, 50), last_pickup=_at(16, 14), next_arrival=_at(17, 26)
    )

    assert morning.journey_id == "20260914-am"
    assert afternoon.journey_id == "20260914-pm"


def test_the_journey_id_does_not_change_mid_ride() -> None:
    """It comes from the clock, not the prediction's run attribute.

    next_arrival flips to the afternoon the instant the morning pickup passes,
    so an id taken from it would rename a journey halfway through and start a
    second notification rather than updating the first.
    """
    early = _stage(now=_at(8, 5), last_pickup=_at(8, 1), school_arrival=_at(9, 25))
    late = _stage(now=_at(9, 20), last_pickup=_at(8, 1), school_arrival=_at(9, 25))

    assert early.journey_id == late.journey_id == "20260914-am"


def test_an_afternoon_drop_off_scan_means_home_not_school() -> None:
    """A drop-off scan says the rider got off the bus, not where.

    Which it was depends on the run. Reading every drop-off as the school
    announced "Arrived at school — dropped off safely" as the rider stepped off
    the bus outside the house.
    """
    journey = _stage(
        now=_at(17, 22),
        distance=0.0,
        last_pickup=_at(16, 21),
        last_dropoff=_at(17, 20),
        next_arrival=_at(17, 20),
    )

    assert journey.stage == "home"
    assert journey.progress == 100


def test_a_morning_drop_off_scan_still_means_school() -> None:
    """The same scan, earlier in the day, is the school."""
    journey = _stage(
        now=_at(9, 24),
        last_pickup=_at(8, 1),
        last_dropoff=_at(9, 23),
        school_arrival=_at(9, 25),
    )

    assert journey.stage == "at_school"


def test_arriving_in_the_morning_is_not_home_because_the_afternoon_is_next() -> None:
    """The moment the bus arrives, the prediction rolls to the afternoon.

    Reading the stage off which run is predicted NEXT then flips the card to
    "Home" while the rider is standing at the kerb waiting to be let on for
    school. What run this is comes from the clock; next_run says only what is
    being counted towards.
    """
    journey = _stage(
        now=_at(8, 1),
        distance=0.1,
        approach_open=True,
        next_arrival=_at(17, 21),
        next_run="pm",
        prediction_source="learned",
    )

    assert journey.stage == "at_stop"
    assert journey.progress == 100


def test_arriving_home_is_not_a_walk_to_the_stop_because_tomorrow_is_next() -> None:
    """The afternoon mirror, and the one that actually happens.

    When the bus reaches the stop at the end of the ride home, the prediction
    rolls to tomorrow morning. Reading the stage off it then announces "At the
    stop now — time to board" as the rider steps off the bus outside the
    house, at half past five in the evening.
    """
    journey = _stage(
        now=_at(17, 26),
        distance=0.1,
        approach_open=True,
        next_arrival=_at(8, 1, day=15),
        next_run="am",
        prediction_source="learned",
    )

    assert journey.stage == "home"
    assert journey.progress == 100


def test_the_target_only_moves_when_the_displayed_minute_moves() -> None:
    """So "has the estimate changed" is a question the attribute can answer.

    The estimate carries microseconds and is recomputed every thirty seconds,
    so the raw target drifts constantly while saying the same thing. Anything
    watching it for a reason to re-push a notification fired on every poll.

    A card shows minutes, so the target is rounded to the minute it will be
    read as. Two estimates that display identically now compare equal.
    """
    early = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1, 12),
        next_run="am",
        prediction_source="learned",
    )
    later = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1, 29),
        next_run="am",
        prediction_source="learned",
    )

    assert early.target == later.target == _at(8, 1)


def test_a_target_past_the_half_minute_rounds_up() -> None:
    """Rounding, not truncation: 8:01:45 is shown as 8:02, so it targets 8:02."""
    journey = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1, 45),
        next_run="am",
        prediction_source="learned",
    )

    assert journey.target == _at(8, 2)


def test_a_real_change_in_the_estimate_still_moves_the_target() -> None:
    """The rounding must not swallow a change worth telling somebody about."""
    before = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1),
        next_run="am",
        prediction_source="learned",
    )
    after = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(7, 56),
        next_run="am",
        prediction_source="learned",
    )

    assert before.target != after.target
    assert after.target == _at(7, 56)


def test_no_afternoon_journey_without_a_scan_saying_the_rider_is_aboard() -> None:
    """15 Sep, and the reason the next morning had no notification at all.

    The rider did not ride. The bus ran a nearby route anyway, and the
    afternoon window opened at 16:36 on the clock alone — for a bus that was
    never coming to this stop. It then flapped in and out of idle as the
    estimate slid past, starting and clearing three Live Activities in seventy
    minutes and spending the push-to-start budget the next morning needed.

    Whether the rider is on the bus is not a guess. They scan a badge to board
    it. No afternoon scan, no ride home, nothing to show.
    """
    journey = _stage(
        now=_at(16, 40),
        distance=4.0,
        approach_open=True,
        next_arrival=_at(17, 21),
        next_run="pm",
        prediction_source="learned",
    )

    assert journey.stage == "idle"


def test_the_morning_approach_needs_no_scan_because_none_can_exist_yet() -> None:
    """The asymmetry, and why this is not simply "require a scan".

    The scan happens on BOARDING. In the morning the bus is still on its way
    to collect the rider, so there cannot be one — requiring it would mean
    never showing the approach that matters most, the one you read while
    deciding when to walk out of the door.
    """
    journey = _stage(
        now=_at(7, 50),
        distance=1.5,
        approach_open=True,
        next_arrival=_at(8, 1),
        next_run="am",
        prediction_source="learned",
    )

    assert journey.stage == "to_stop"
    assert journey.progress == 50


def test_an_afternoon_scan_gives_the_ride_home_by_elapsed_time() -> None:
    """And with the scan, the afternoon is rule 3 — which knows more.

    Being aboard is measured from the time of boarding against the predicted
    arrival, which is real progress. The approach branch could only ever have
    offered distance to the stop, which says nothing while the bus works its
    route. So nothing was lost by removing it.
    """
    journey = _stage(
        now=_at(16, 50),
        distance=4.0,
        last_pickup=_at(16, 14),
        next_arrival=_at(17, 26),
    )

    assert journey.stage == "from_school"
    assert journey.progress == 50


def test_the_ride_home_does_not_restart_when_the_bus_comes_back_past() -> None:
    """16 Sep, and the notification that said she was riding home indoors.

    The bus reached the stop at 17:20 and she got off. It then worked the
    neighbourhood for seventeen minutes — 0.0, 0.6, 1.3, 0.7, 0.4 — crossing
    back inside the rungs twice. Each re-entry restarted the aboard rule,
    which announced a ride home that had already finished; the last one
    computed a fresh seven-minute estimate from a bus that simply happened to
    be near the house.

    A morning arrival starts a journey. An afternoon arrival ends one.
    """
    journey = _stage(
        now=_at(17, 37),
        distance=0.4,
        approach_open=True,
        last_pickup=_at(16, 15),
        next_arrival=_at(17, 44),
        next_run="pm",
        prediction_source="learned",
        ride_home_ended=_at(17, 20),
    )

    assert journey.stage == "idle"


def test_the_finished_card_is_the_last_thing_said() -> None:
    """Because nothing clears it any more, the last push has to be right.

    The journey-end clear was removed so activities are not destroyed — which
    means whatever was pushed last simply stands on the phone until iOS
    retires it. A journey that ends mid-ride therefore leaves a card saying
    the rider is still on the bus. Holding the finished state for the dwell is
    what makes the final push a finished one.
    """
    journey = _stage(
        now=_at(17, 22),
        distance=0.1,
        approach_open=True,
        last_pickup=_at(16, 15),
        next_arrival=_at(17, 20),
        next_run="pm",
        prediction_source="learned",
        ride_home_ended=_at(17, 20),
    )

    assert journey.stage == "home"
    assert journey.progress == 100


def test_a_morning_arrival_still_starts_the_ride_to_school() -> None:
    """The asymmetry, guarded: only the AFTERNOON arrival is terminal.

    Reaching the stop in the morning is the bus collecting the rider, and the
    ride to school follows it. If arriving were treated as terminal in both
    directions, the morning journey would end at the moment it began.
    """
    journey = _stage(
        now=_at(8, 43),
        distance=6.0,
        last_pickup=_at(8, 1),
        school_arrival=_at(9, 25),
        ride_home_ended=None,
    )

    assert journey.stage == "to_school"
    assert journey.progress == 50
