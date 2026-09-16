"""Tests for deciding whether the rider is on the bus without a scan.

Every coordinate here is invented and sits in the fictional town that
tests/fixtures.py uses. The geometry is what matters: a school, a bus that
leaves it, and a phone that either goes along or does not.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from custom_components.wheresthebus.aboard import travelled_with_the_bus

# A school, and a point three miles away down the route.
SCHOOL = (40.7550, -74.0400)
DOWN_THE_ROUTE = (40.7300, -74.0250)

MOVED = 0.5
TOGETHER = 0.25


def test_the_phone_left_with_the_bus() -> None:
    """The case this exists for: no scan, but she is plainly on it."""
    assert travelled_with_the_bus(
        SCHOOL, DOWN_THE_ROUTE, DOWN_THE_ROUTE, moved=MOVED, together=TOGETHER
    )


def test_a_phone_still_at_school_is_not_on_the_bus() -> None:
    """The 15 Sep case. She did not ride; the bus went anyway.

    Reading the clock alone announced a bus coming home that was never
    coming, and the flapping that followed spent the next morning's
    push-to-start budget. The phone settles it: it never left the school.
    """
    assert not travelled_with_the_bus(
        SCHOOL, SCHOOL, DOWN_THE_ROUTE, moved=MOVED, together=TOGETHER
    )


def test_a_lift_home_in_a_car_is_not_the_bus() -> None:
    """Movement alone is not enough, which is why proximity is also required.

    Collected at the gate and driven the other way: the phone has travelled
    well over the threshold, and is nowhere near the bus.
    """
    driven_away = (40.7150, -74.0550)

    assert not travelled_with_the_bus(
        SCHOOL, driven_away, DOWN_THE_ROUTE, moved=MOVED, together=TOGETHER
    )


def test_standing_beside_a_loading_bus_is_not_riding_it() -> None:
    """And proximity alone is not enough either, which is the subtler half.

    While the bus loads at school the phone is metres away from it — that is
    the exact moment the question gets asked. Without the distance-travelled
    test this would read as aboard every single afternoon, scan or no scan.
    """
    on_the_kerb = (40.75505, -74.04005)
    bus_still_loading = (40.7551, -74.0401)

    assert not travelled_with_the_bus(
        SCHOOL, on_the_kerb, bus_still_loading, moved=MOVED, together=TOGETHER
    )


def test_a_phone_that_cannot_be_located_is_not_an_answer() -> None:
    """A missing fix must not read as "not aboard".

    It has not been shown that she is on the bus, but it has not been shown
    that she is at school either. The caller falls back on the scan, which is
    the signal that was always definitive.
    """
    assert not travelled_with_the_bus(
        None, DOWN_THE_ROUTE, DOWN_THE_ROUTE, moved=MOVED, together=TOGETHER
    )
    assert not travelled_with_the_bus(
        SCHOOL, None, DOWN_THE_ROUTE, moved=MOVED, together=TOGETHER
    )
    assert not travelled_with_the_bus(
        SCHOOL, DOWN_THE_ROUTE, None, moved=MOVED, together=TOGETHER
    )


def test_the_thresholds_are_the_caller_s_units() -> None:
    """Miles or kilometres, the same geometry has to answer consistently."""
    assert travelled_with_the_bus(
        SCHOOL, DOWN_THE_ROUTE, DOWN_THE_ROUTE, moved=0.8, together=0.4
    )
    # A threshold wider than the journey itself refuses, as it should.
    assert not travelled_with_the_bus(
        SCHOOL, DOWN_THE_ROUTE, DOWN_THE_ROUTE, moved=99.0, together=0.4
    )
