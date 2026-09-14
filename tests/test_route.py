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

from custom_components.wheresthebus.route import haversine_miles, nearest_remaining

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
