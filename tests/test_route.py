"""Tests for matching a bus against the route it drives.

The cases here are the ones straight-line distance gets wrong. A school bus is
not travelling towards the stop: it serves other children, turns round in
cul-de-sacs, and spends long stretches driving directly away. Measured as
distance, every one of those looks like a setback or a stall.

Every coordinate below is invented, and shares the fictional town used by
tests/fixtures.py. Only the shape of the manoeuvre is drawn from life. Real
positions must never appear here: a few miles of turns is a fingerprint that
can be matched against the road network and put back on the map, and the
route this integration follows is a child's daily journey to and from school.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from custom_components.wheresthebus.route import (
    bearing_degrees,
    haversine_miles,
    heading_of,
    nearest_remaining,
)

RADIUS = 0.25

# Six samples thirty seconds apart: south, west, west, halted, then back east.
# A U-turn, a quarter of an hour before the bus reaches the stop, followed by
# the stop itself at zero seconds remaining.
UTURN: list[tuple[int, float, float]] = [
    (900, 40.7350, -74.0200),
    (870, 40.7300, -74.0200),
    (840, 40.7300, -74.0230),
    (810, 40.7300, -74.0260),
    (780, 40.7299, -74.0260),
    (750, 40.7300, -74.0232),
    (0, 40.7155, -74.0020),
]


def test_haversine_matches_a_known_pair() -> None:
    """Two consecutive samples, thirty seconds apart."""
    miles = haversine_miles(40.7300, -74.0230, 40.7300, -74.0260)

    assert round(miles, 2) == 0.16


def test_a_u_turn_is_a_place_on_the_route_not_a_setback() -> None:
    """The apex of the U-turn is a recognisable spot with a known answer.

    Straight-line distance reads this stretch as the bus giving up ground. As
    a position it is unambiguous: thirteen and a half minutes from home.
    """
    assert nearest_remaining(UTURN, 40.7300, -74.0260, None, RADIUS) == 810


def test_elapsed_time_tells_the_outbound_pass_from_the_homeward_one() -> None:
    """A route crosses itself, and the two passes need different answers.

    The same junction is driven twice, ninety seconds apart. Position alone
    cannot separate them; how long today's journey has been running can.
    """
    outbound = nearest_remaining(UTURN, 40.7300, -74.0231, 90, RADIUS)
    homeward = nearest_remaining(UTURN, 40.7300, -74.0231, 150, RADIUS)

    assert outbound == 810
    assert homeward == 750
    # And they really are different answers, which is the whole point.
    assert outbound != homeward


def test_a_bus_off_the_route_says_so_rather_than_guessing() -> None:
    """A detour or a substitute on another route has no honest answer.

    Returning None lets the estimate fall back to something that does not
    pretend to know, instead of confidently reporting a time derived from
    nothing.
    """
    assert nearest_remaining(UTURN, 40.85, -74.20, None, RADIUS) is None


def test_standing_still_does_not_move_the_answer() -> None:
    """The failure that made this necessary.

    On 14 Sep the bus worked stops four miles out for twelve minutes without
    net progress, and a distance-indexed estimate moved twelve minutes later
    over those twelve minutes — it read "about twenty minutes away" forever,
    because it recomputed `now + typical remaining` on every poll.

    Matching a position cannot do that: standing still matches the same place,
    and the same place has the same answer however long the bus sits there.
    """
    parked = (40.7299, -74.0260)

    first = nearest_remaining(UTURN, *parked, 100, RADIUS)
    still_there = nearest_remaining(UTURN, *parked, 100, RADIUS)

    assert first == still_there


def test_an_empty_track_says_nothing() -> None:
    """A journey recorded before positions were kept cannot be matched."""
    assert nearest_remaining([], 40.73, -74.02, None, RADIUS) is None


def test_bearing_reads_the_four_compass_points() -> None:
    """The primitive the direction match is built on."""
    assert round(bearing_degrees(40.73, -74.02, 40.74, -74.02)) == 0
    assert round(bearing_degrees(40.73, -74.02, 40.73, -74.01)) == 90
    assert round(bearing_degrees(40.73, -74.02, 40.72, -74.02)) == 180
    assert round(bearing_degrees(40.73, -74.02, 40.73, -74.03)) == 270


def test_a_bus_standing_still_reports_no_heading() -> None:
    """A parked bus jitters a few metres, and that jitter points nowhere.

    Most of a school run's samples are the bus not moving — 64 of this
    morning's 73. Reading a direction off those would be reading noise, and
    noise that then rejects perfectly good matches.
    """
    parked = [(40.7300, -74.0260), (40.73001, -74.02601), (40.73, -74.026)]

    assert heading_of(parked) is None
    assert heading_of([(40.7300, -74.0260)]) is None


def test_heading_comes_from_the_last_fix_that_actually_moved() -> None:
    """A long pause before the bus pulls away must not blank its direction."""
    crawl = [
        (40.7300, -74.0200),
        (40.7300, -74.0230),  # a real leg west
        (40.73001, -74.02301),  # then idling at a stop
        (40.73, -74.023),
    ]

    heading = heading_of(crawl)

    assert heading is not None
    assert round(heading) == 270


def test_direction_separates_the_two_passes_of_a_u_turn() -> None:
    """The case elapsed time cannot be trusted with.

    Both samples sit within metres of the query, so position cannot choose.
    Elapsed time can, but only while today is running to the same clock as the
    journey being matched — the assumption the estimate exists to test. A bus
    ten minutes down drifts against every sample equally and the tie-break
    stops discriminating exactly when it is needed.

    Which way the bus is pointing owes nothing to the clock. The two samples
    lie 0.0052 miles from the query and from each other, heading 270 and 87 —
    indistinguishable by position, opposed by direction. Westbound is the
    outbound pass with 840 seconds left; eastbound is the homeward one with
    750, a minute and a half nearer home at the very same spot.
    """
    query = (40.7300, -74.0231)

    westbound = nearest_remaining(UTURN, *query, None, RADIUS, heading=270)
    eastbound = nearest_remaining(UTURN, *query, None, RADIUS, heading=90)

    assert westbound == 840
    assert eastbound == 750
    # Ninety seconds apart, from one position and two directions.
    assert westbound - eastbound == 90


def test_direction_decides_even_when_elapsed_time_points_the_other_way() -> None:
    """Direction must beat the clock, not merely break its ties.

    Elapsed time here argues hard for the outbound pass: 90 seconds in matches
    that sample exactly. But the bus is driving east, so it has already turned
    round and is on its way back. The clock is describing a journey that was
    on time; this one is not.
    """
    left = nearest_remaining(UTURN, 40.7300, -74.0231, 90, RADIUS, heading=90)

    assert left == 750


def test_a_halted_sample_keeps_the_direction_it_arrived_travelling() -> None:
    """A parked bus is still somewhere, and it still got there facing a way.

    The apex sample has only jitter behind it, so its heading is read from the
    last fix that genuinely moved: the bus reached this spot going west. That
    is the right answer rather than a fallback. A bus sitting at the apex
    pointing west is mid-turn with 780 seconds to run; one passing the same
    spot going east has already turned and has 750. Same place, different
    moments in the journey.
    """
    apex = (40.7299, -74.0260)

    assert nearest_remaining(UTURN, *apex, None, RADIUS, heading=270) == 780
    assert nearest_remaining(UTURN, *apex, None, RADIUS, heading=90) == 750


def test_a_sample_with_genuinely_no_heading_is_never_filtered_out() -> None:
    """The opening fix of a track has nothing behind it to take a bearing from.

    It must still be matchable. Refusing every sample whose direction is
    unknown would quietly discard the start of every journey — and on a run
    where the bus barely moves, that is most of the record.
    """
    first = (40.7350, -74.0200)

    assert nearest_remaining(UTURN, *first, None, RADIUS, heading=90) == 900
    assert nearest_remaining(UTURN, *first, None, RADIUS, heading=270) == 900


def test_a_bus_with_no_heading_yet_matches_as_it_always_did() -> None:
    """One fix in, or standing still, there is no direction to compare."""
    assert nearest_remaining(UTURN, 40.7300, -74.0260, None, RADIUS) == 810
    assert (
        nearest_remaining(UTURN, 40.7300, -74.0260, None, RADIUS, heading=None) == 810
    )


# A past journey that waited a long time in one place before setting off, then
# paused briefly on the way. Invented, like everything else here.
DEPOT: list[tuple[int, float, float]] = [
    (2400, 40.7400, -74.0300),
    (2100, 40.7400, -74.0300),
    (1800, 40.7400, -74.0300),
    (1500, 40.7400, -74.0300),
    (1200, 40.7400, -74.0300),
    (900, 40.7400, -74.0300),
    (600, 40.7400, -74.0300),
    (300, 40.7300, -74.0200),
    (270, 40.7300, -74.0200),
    (240, 40.7300, -74.0200),
    (150, 40.7250, -74.0150),
    (0, 40.7155, -74.0020),
]


def test_a_place_the_bus_waited_half_an_hour_has_no_answer() -> None:
    """Measured on 15 Sep, and the reason this guard exists.

    The bus sat at the depot from 07:14 to 07:49 with its position frozen,
    then drove the whole route in eight minutes. Matched against the day
    before, that parked position hit seventy samples spanning thirty-five
    minutes — and the estimate confidently reported forty-five minutes to go
    when the true answer was fourteen. Thirty-one minutes wrong, stated
    without hedging, for the entire first half of the window.

    The position is real. What it is not is informative: a bus that has not
    left yet has made no progress to measure, and where it is parked says
    nothing about when it will go. Returning None hands the question back to
    the historical estimate, which is vague but honest.
    """
    assert nearest_remaining(DEPOT, 40.7400, -74.0300, 120, RADIUS) is None
    # Not merely the elapsed time saving it — no elapsed, same refusal.
    assert nearest_remaining(DEPOT, 40.7400, -74.0300, None, RADIUS) is None


def test_a_brief_stop_on_the_route_still_answers() -> None:
    """The guard must not throw away ordinary stops.

    A bus pausing to work a stop is the normal case, and it is still a place
    with a usable answer. On the same 15 Sep replay the moving samples spanned
    0.0 to 2.5 minutes against 34.5 for the depot, so there is a wide gap to
    put the threshold in rather than a line to split hairs over.
    """
    assert nearest_remaining(DEPOT, 40.7300, -74.0200, None, RADIUS) == 240
    assert nearest_remaining(DEPOT, 40.7250, -74.0150, None, RADIUS) == 150


def test_refusing_to_answer_is_not_the_same_as_finding_nothing() -> None:
    """Both return None, and the caller treats them the same, deliberately.

    Off-route and can't-tell are different states, but the useful response to
    each is identical: say nothing and let a vaguer estimate stand. Collapsing
    them keeps one fallback path rather than two.
    """
    off_route = nearest_remaining(DEPOT, 40.9, -74.5, None, RADIUS)
    cannot_tell = nearest_remaining(DEPOT, 40.7400, -74.0300, None, RADIUS)

    assert off_route is None
    assert cannot_tell is None
