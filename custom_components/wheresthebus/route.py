"""Work out how far through its route a bus is, by matching where it has been.

Distance to the rider's stop is a lossy projection of a school run. The bus is
not driving towards the stop: it is driving a route, serving other children,
turning around in cul-de-sacs, and at times moving directly away from the stop
while making perfect progress. Measured as straight-line distance, a U-turn
looks like a setback and a loitering bus looks like one that will never
arrive — an estimate built on it slides forward with the clock and never
converges.

The route, though, is very nearly the same every day. So the useful question
is not "how far is the bus from the stop" but "where on the route is it, and
how long did that take last time". Matching today's position against the
positions past journeys passed through answers that directly: a U-turn matches
a U-turn, a pause outside a school matches the same pause, and a bus genuinely
running ahead matches a point that was late in previous journeys.

Observed on 11 Sep, four samples thirty seconds apart: west, west, stationary,
then back east. Nothing about that is legible as a distance. As a position on
a route it is unmistakable, and it happened thirteen minutes before the bus
reached the stop.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Sequence
from math import asin, atan2, cos, degrees, radians, sin, sqrt

# Mean radius of the earth. Journeys here are a few miles, where treating the
# earth as a sphere is wrong by centimetres.
_EARTH_RADIUS_MILES = 3958.7613

# How far apart two fixes must be before the line between them is taken as a
# heading. A bus idling at a stop still jitters by a few metres, and the
# bearing of that jitter is noise pointing in a random direction.
_HEADING_MIN_MILES = 0.02

# How far two headings may differ and still count as the same way along the
# road. Ninety degrees splits "onward" from "back the way it came" while
# leaving room for a bend taken between fixes; the passes either side of a
# U-turn are near enough 180 apart.
_HEADING_TOLERANCE_DEGREES = 90.0

# How far apart the answers from one place may be before that place is
# admitted to not have an answer. A bus parked at the depot matches the whole
# of a past journey's parked block, and the two ends of that block are half an
# hour apart: the position is real, and it says nothing about progress.
#
# Measured on the 15 Sep morning run against 14 Sep. While the bus was moving,
# the matching samples spanned 0.0 to 2.5 minutes. While it was parked, 34.5
# to 35.5. There is no borderline case in between — position either pins the
# journey down or misses by an order of magnitude — so five minutes sits clear
# of a genuine wait at a stop and nowhere near the depot.
_AMBIGUOUS_SPREAD_SECONDS = 300

# A half turn, and the fewest fixes a direction can be drawn from.
_STRAIGHT_ANGLE = 180
_PAIR = 2


def haversine_miles(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float
) -> float:
    """Return the great-circle distance between two points, in miles."""
    lat1, lon1, lat2, lon2 = map(radians, (from_lat, from_lon, to_lat, to_lon))
    inner = (
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_MILES * asin(sqrt(inner))


def bearing_degrees(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float
) -> float:
    """Return the initial compass bearing from one point to another."""
    lat1, lat2 = radians(from_lat), radians(to_lat)
    delta_lon = radians(to_lon - from_lon)
    y = sin(delta_lon) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta_lon)
    return degrees(atan2(y, x)) % 360


def _turn_between(first: float, second: float) -> float:
    """Return the smaller angle between two bearings, 0 to 180."""
    difference = abs(first - second) % 360
    return difference if difference <= _STRAIGHT_ANGLE else 360 - difference


def heading_of(points: Sequence[tuple[float, float]]) -> float | None:
    """Return which way something at the end of ``points`` is travelling.

    ``points`` is (latitude, longitude), oldest first. The bearing is taken
    from the most recent earlier fix far enough away to mean something, so a
    bus that has been sitting still reports None rather than the direction of
    its GPS jitter.
    """
    if len(points) < _PAIR:
        return None
    lat, lon = points[-1]
    for previous_lat, previous_lon in reversed(points[:-1]):
        if haversine_miles(previous_lat, previous_lon, lat, lon) >= _HEADING_MIN_MILES:
            return bearing_degrees(previous_lat, previous_lon, lat, lon)
    return None


def _sample_heading(track: list[tuple[int, float, float]], index: int) -> float | None:
    """Return which way a past journey was travelling at one of its samples."""
    return heading_of([(lat, lon) for _, lat, lon in track[: index + 1]])


def _along_segment(
    lat: float,
    lon: float,
    start: tuple[int, float, float],
    end: tuple[int, float, float],
) -> tuple[float, float]:
    """Return how far a point lies from a segment, and the age where it meets.

    A stored track is a handful of fixes, not a road. At thirty miles an hour
    a thirty-second poll leaves them a quarter of a mile apart — the same as
    the match radius — so matching a point against SAMPLES means a journey
    drops in and out of the answer as the bus moves between them, and the
    median jumps by whole minutes as the set changes underneath it. That churn
    is most of the noise the published arrival then has to be held still
    against.

    Between two consecutive fixes the bus was somewhere on the line joining
    them, and its age somewhere between theirs. Projecting onto that line and
    interpolating gives a continuous answer instead of a quantised one, and
    removes the half-poll of rounding that came with picking a sample.

    Locally flat geometry: these are segments of a few hundred yards, where
    treating latitude and longitude as a plane is wrong by inches.
    """
    start_age, start_lat, start_lon = start
    end_age, end_lat, end_lon = end

    scale = cos(radians((start_lat + end_lat) / 2))
    run_x = (end_lon - start_lon) * scale
    run_y = end_lat - start_lat
    length = run_x * run_x + run_y * run_y
    if length == 0:
        return haversine_miles(lat, lon, start_lat, start_lon), float(start_age)

    along = (((lon - start_lon) * scale) * run_x + (lat - start_lat) * run_y) / length
    along = max(0.0, min(1.0, along))
    met_lat = start_lat + along * run_y
    met_lon = start_lon + along * (end_lon - start_lon)
    age = start_age + along * (end_age - start_age)
    return haversine_miles(lat, lon, met_lat, met_lon), age


def nearest_remaining(
    track: list[tuple[int, float, float]],
    lat: float,
    lon: float,
    elapsed: int | None,
    radius: float,
    *,
    heading: float | None = None,
) -> int | None:
    """Return how long was left, when a past journey was where the bus is now.

    ``track`` is (seconds before arrival, latitude, longitude), oldest first.
    ``elapsed`` is how long today's journey has been running, used only to
    break ties. ``radius`` is how close a past position has to be, in miles,
    to count as the same place.

    Returns None when a journey never came near this spot, which simply means
    it has nothing to say about where the bus is now — a detour, a substitute
    bus on another route, or a stretch that journey did not record.

    A route crosses itself: the same junction can be passed on the way out and
    again on the way back, and the two passes want different answers.

    ``heading`` is which way the bus is travelling now, in degrees, and it is
    the strongest thing available for telling those passes apart. At a U-turn
    the two passes are metres apart and roughly 180 degrees opposed, so a
    sample heading the other way is not where the bus is, however close it
    sits. Past samples pointing the wrong way are dropped outright.

    Direction beats elapsed time at this because it does not depend on the
    schedule. ``elapsed`` compares today's running time against a past
    journey's, so it assumes today is going roughly like that journey did —
    which is exactly the assumption the estimate exists to test. A bus ten
    minutes down drifts against every past sample equally, and the tie-break
    stops discriminating at the moment it matters most. Which way the bus is
    pointing is true whatever the clock says.

    So elapsed time stays, demoted to separating same-direction passes, and
    for a journey with no direction yet — the bus stationary, or only one fix
    so far — it is still the only thing there is.
    """
    if not track:
        return None

    # How long the track covers, so a sample's own elapsed time can be read
    # off it: the first sample is the furthest from arrival.
    span = track[0][0]

    # (distance away, disagreement in elapsed time, seconds left)
    #
    # DISTANCE LEADS, elapsed only separates ties. Elapsed is measured from
    # the first sample of each track, and those starts are not comparable
    # between journeys: a track begins when the approach window opens or when
    # the rider scans on, and the window itself moves as the learned centre
    # does. Sorting on it first meant the primary key was the least reliable
    # number available. Direction already tells the two passes of a crossing
    # apart, which is what elapsed was really being asked to do.
    matches: list[tuple[float, float, float]] = []
    for index in range(len(track) - 1):
        gap, age = _along_segment(lat, lon, track[index], track[index + 1])
        if gap > radius:
            continue
        if heading is not None:
            was = _sample_heading(track, index + 1)
            # A sample with no heading of its own is a bus that was standing
            # still there, which is a real place on the route and keeps its
            # claim. Only a sample known to be going the other way is refused.
            if was is not None and _turn_between(heading, was) > (
                _HEADING_TOLERANCE_DEGREES
            ):
                continue
        drift = 0.0 if elapsed is None else abs((span - age) - elapsed)
        matches.append((gap, drift, age))

    if not matches:
        # A single-sample track has no segment to project onto.
        if len(track) == 1:
            age, only_lat, only_lon = track[0]
            if haversine_miles(lat, lon, only_lat, only_lon) <= radius:
                return age
        return None

    # If this place meant wildly different things at different times of a past
    # journey, it does not locate today's. Answering anyway is worse than not
    # answering: the caller has a historical estimate to fall back on that is
    # honestly vague, and replacing it with a confident wrong number is how a
    # parked bus came out 31 minutes adrift on 15 Sep.
    ages = [age for _, _, age in matches]
    if max(ages) - min(ages) > _AMBIGUOUS_SPREAD_SECONDS:
        return None

    return round(min(matches)[2])


__all__ = ["bearing_degrees", "haversine_miles", "heading_of", "nearest_remaining"]
