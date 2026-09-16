"""Decide whether the rider is on the bus when they did not scan on.

Boarding is normally a fact: the rider scans a badge, and that scan is what the
afternoon ride is measured from. But the scan is missed sometimes — three times
in the first nine school days observed — and a missed scan used to cost the
whole afternoon's notification.

The phone answers the same question independently. Two positions are enough:
where it was while the bus was loading at school, and where it is once the bus
has left. If it has travelled a meaningful distance AND is with the bus, the
rider is on the bus. If it has not moved, they are still at school and this is
not their ride.

Both halves are needed. Distance travelled alone cannot tell a bus from a lift
home in a car. Proximity alone cannot tell riding from standing in a school car
park while the bus loads twenty metres away — which is exactly the moment the
question is being asked.

This deliberately says nothing about when boarding happened, only whether it
did. A phone fix arrives when it arrives; the bus leaving school is what is
being detected, not the step onto it.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from .route import haversine_miles

type Fix = tuple[float, float] | None


def travelled_with_the_bus(
    origin: Fix,
    phone: Fix,
    bus: Fix,
    *,
    moved: float,
    together: float,
) -> bool:
    """Return whether the phone has left with the bus.

    ``origin`` is where the phone was when the run's window opened, ``phone``
    where it is now, and ``bus`` where the bus is now. ``moved`` is how far the
    phone must have gone to count as having left, and ``together`` how close it
    must be to the bus to count as being on it — both in whatever unit the
    caller is working in.

    Any missing fix means no answer, which is not the same as "no". A phone
    that cannot be located has not been shown to be at school either.
    """
    if origin is None or phone is None or bus is None:
        return False

    gone = haversine_miles(origin[0], origin[1], phone[0], phone[1])
    apart = haversine_miles(phone[0], phone[1], bus[0], bus[1])
    return gone >= moved and apart <= together


__all__ = ["travelled_with_the_bus"]
