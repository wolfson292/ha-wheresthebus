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

from math import asin, cos, radians, sin, sqrt

# Mean radius of the earth. Journeys here are a few miles, where treating the
# earth as a sphere is wrong by centimetres.
_EARTH_RADIUS_MILES = 3958.7613


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


def nearest_remaining(
    track: list[tuple[int, float, float]],
    lat: float,
    lon: float,
    elapsed: int | None,
    radius: float,
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
    again on the way back. Where several past samples are equally close, the
    one whose own elapsed time best matches today's wins, which is what tells
    the outbound pass from the homeward one. Falling back on the latest such
    sample would always claim the homeward pass, and so always under-estimate.
    """
    if not track:
        return None

    # How long the track covers, so a sample's own elapsed time can be read
    # off it: the first sample is the furthest from arrival.
    span = track[0][0]

    # (disagreement in elapsed time, distance away, seconds left)
    matches: list[tuple[float, float, int]] = []
    for age, sample_lat, sample_lon in track:
        gap = haversine_miles(lat, lon, sample_lat, sample_lon)
        if gap > radius:
            continue
        drift = 0.0 if elapsed is None else abs((span - age) - elapsed)
        matches.append((drift, gap, age))

    if not matches:
        return None
    # Elapsed agreement first, distance second. Both candidates are already
    # within the radius, so the nearer one is not necessarily the right one —
    # the outbound and homeward passes through a junction are metres apart.
    return min(matches)[2]


__all__ = ["haversine_miles", "nearest_remaining"]
