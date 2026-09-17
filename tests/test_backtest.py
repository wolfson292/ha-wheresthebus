"""Replay real recorded journeys and score what the estimate would have said.

Every prediction fault in this integration was discoverable offline. The
recorder holds the positions, the estimate is a pure function of them, and the
arrival time is known — so "what would we have told the user, minute by
minute, and how wrong was it" can be answered at a desk instead of at a bus
stop. It repeatedly was not, and the cost landed on somebody standing outside
waiting for a school bus.

Two faults this would have caught before shipping, in seconds:

* The distance-indexed estimate slid forward with the clock whenever the bus
  stopped closing. A backtest shows it flat-lining at "about twenty minutes
  away" for a quarter of an hour, never converging.
* The over-confident 17:10 reading that I checked against a single instant,
  called validated, and was wrong about within the hour. A backtest scores
  every instant, so one agreeable point cannot pass for a result.

These tests need recorded journeys, which are NOT in the repository and must
never be. Moving the coordinates does not anonymise a route: a few miles of
turns is a fingerprint that can be matched against the road network and put
straight back on the map, and this is a child's daily route to and from
school.

Point WTB_JOURNEYS at a directory outside the repository and they are read
from there, which is the safest arrangement — the files never enter the tree.
Failing that they are read from tests/journeys, which is gitignored.

Make your own with scripts/capture_journey.py. Without any, these tests skip.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import pytest

from custom_components.wheresthebus.const import (
    MIN_ROUTE_SAMPLES,
    OUTLIER_FLOOR_MINUTES,
    ROUTE_MATCH_RADIUS_MILES,
    TRACK_SAMPLE_LIMIT,
)
from custom_components.wheresthebus.coordinator import _median, _reject_outliers
from custom_components.wheresthebus.route import haversine_miles, nearest_remaining

JOURNEYS = Path(os.environ.get("WTB_JOURNEYS") or Path(__file__).parent / "journeys")
RECORDED = sorted(JOURNEYS.glob("*.json")) if JOURNEYS.is_dir() else []

# Journeys are local-only, so CI and a fresh clone have none. Skipping is the
# honest outcome there: pretending to have validated against real runs would
# be worse than saying plainly that there were none to replay.
needs_journeys = pytest.mark.skipif(
    not RECORDED, reason="no recorded journeys (see scripts/capture_journey.py)"
)


@dataclass(slots=True)
class Journey:
    """One recorded run: where the bus was, and when it actually arrived."""

    run: str
    stop: tuple[float, float]
    boarded: datetime
    arrived: datetime
    samples: list[tuple[datetime, float, float]]

    @property
    def track(self) -> list[tuple[int, float, float]]:
        """The journey as the integration stores it, newest sample last."""
        return [
            (int((self.arrived - at).total_seconds()), lat, lon)
            for at, lat, lon in self.samples
            if at <= self.arrived
        ][-TRACK_SAMPLE_LIMIT:]

    def distance_at(self, index: int) -> float:
        """Straight-line miles from the stop at one sample."""
        _, lat, lon = self.samples[index]
        return haversine_miles(lat, lon, *self.stop)


def _load(name: str) -> Journey:
    raw = json.loads((JOURNEYS / name).read_text())
    return Journey(
        run=raw["run"],
        stop=(raw["stop"]["lat"], raw["stop"]["lon"]),
        boarded=datetime.fromisoformat(raw["boarded"]),
        arrived=datetime.fromisoformat(raw["arrived"]),
        samples=[
            (datetime.fromisoformat(s["at"]), s["lat"], s["lon"])
            for s in raw["samples"]
        ],
    )


def _route_error_minutes(
    journey: Journey, history: list[Journey]
) -> list[tuple[int, float]]:
    """Score the route estimate at every sample of a journey.

    Returns (minutes still to run, error in minutes) for each point the model
    would answer at, where a positive error means it promised the bus too
    early. ``history`` stands in for what had been learned by then.
    """
    tracks = [past.track for past in history]
    scored: list[tuple[int, float]] = []

    for at, lat, lon in journey.samples:
        if at > journey.arrived:
            break
        elapsed = int((at - journey.samples[0][0]).total_seconds())
        remainders = sorted(
            left
            for track in tracks
            if (
                left := nearest_remaining(
                    track, lat, lon, elapsed, ROUTE_MATCH_RADIUS_MILES
                )
            )
            is not None
        )
        if len(remainders) < MIN_ROUTE_SAMPLES:
            continue
        usual, _ = _reject_outliers(remainders, floor=OUTLIER_FLOOR_MINUTES * 60)
        predicted_left = _median(usual)
        actually_left = (journey.arrived - at).total_seconds()
        scored.append((int(actually_left // 60), (actually_left - predicted_left) / 60))
    return scored


@needs_journeys
def test_the_fixture_is_a_real_journey_with_its_geometry_intact() -> None:
    """Guard the fixture itself: a broken one would quietly pass everything."""
    journey = _load(RECORDED[0].name)

    assert journey.run == "pm"
    assert len(journey.samples) > 100
    # Boarded at school, seven miles out; ended at the stop.
    assert journey.distance_at(0) > 7
    assert journey.distance_at(len(journey.samples) - 1) < 0.3
    ride = (journey.arrived - journey.boarded).total_seconds() / 60
    assert 60 < ride < 80


@needs_journeys
def test_a_journey_predicts_itself_almost_exactly() -> None:
    """The floor: scored against its own record, error should be ~zero.

    Not a useful accuracy claim — it is the harness proving it measures what
    it says it measures. A bug in the scoring would show up here first.
    """
    journey = _load(RECORDED[0].name)

    scored = _route_error_minutes(journey, [journey, journey])

    assert scored, "the model answered at no point in the journey"
    worst = max(abs(error) for _, error in scored)
    assert worst < 1.0


@needs_journeys
def test_the_estimate_converges_rather_than_sliding_with_the_clock() -> None:
    """The fault that this whole exercise exists to catch.

    A distance-indexed estimate recomputed "now + typical remaining" on every
    poll, so while the bus worked stops without closing, it moved later exactly
    as fast as the clock and never converged — reported as "about twenty
    minutes away" for twelve minutes straight.

    Matching a position cannot do that, and the shape of the error proves it:
    whatever it says far out, it must be tighter near the end.
    """
    journey = _load(RECORDED[0].name)
    scored = _route_error_minutes(journey, [journey, journey])

    far = [abs(error) for left, error in scored if left > 30]
    near = [abs(error) for left, error in scored if left <= 5]

    assert far
    assert near
    assert max(near) <= max(far)
    # And the last word is close enough to act on.
    assert max(near) < 2.0


@needs_journeys
@pytest.mark.parametrize("name", [p.name for p in RECORDED] or ["none"])
def test_every_recorded_journey_replays(name: str) -> None:
    """Every fixture must load and be scoreable, so none rots unnoticed."""
    journey = _load(name)

    assert journey.track
    assert journey.arrived > journey.boarded


@needs_journeys
def test_the_estimate_does_not_quantise_to_the_sample_spacing() -> None:
    """Score the model between recorded fixes, not on top of them.

    Replaying a journey against its own track asks only about points the
    recorder already holds, where any model that finds the right sample is
    exactly right. The bus spends almost all of its time between those
    points — thirty seconds apart is a quarter of a mile at road speed — and
    that is where a nearest-sample match has to round, quantising the answer
    to the spacing and holding it still while the bus covers the leg.

    So the queries here are the midpoints of the legs the bus actually drove,
    with the truth taken as the midpoint in time. That assumes the bus held
    its speed along the leg, which is why legs where it barely moved are
    skipped and why the bar below is not zero: a stretch where it accelerated
    genuinely reaches its midpoint off-centre in time.
    """
    journey = _load(RECORDED[0].name)
    track = journey.track

    queries = []
    for (left, lat, lon), (next_left, next_lat, next_lon) in pairwise(track):
        if haversine_miles(lat, lon, next_lat, next_lon) < 0.05:
            continue
        queries.append(
            (
                (lat + next_lat) / 2,
                (lon + next_lon) / 2,
                (left + next_left) / 2,
            )
        )

    assert len(queries) > 20, "too few moving legs to say anything"

    errors = [
        abs(truth - answer) / 60
        for lat, lon, truth in queries
        if (
            answer := nearest_remaining(track, lat, lon, None, ROUTE_MATCH_RADIUS_MILES)
        )
        is not None
    ]

    assert len(errors) > 20, "the radius or the ambiguity guard refused too much"
    # Nearest-sample scored 0.67 mean and 3.77 worst on this journey. A tenth
    # of a minute leaves room for genuine speed changes within a leg and none
    # at all for rounding to the nearest fix.
    assert max(errors) < 0.1
