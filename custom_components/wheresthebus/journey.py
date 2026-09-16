"""Work out which stage of the school run a rider is currently in.

This exists because the same question — "is a journey happening, and how far
through it" — was being answered independently in ten branches of a
Home Assistant automation, from whatever signals were nearest to hand. Four
separate faults came out of that, each one a gap in a single branch that the
other nine could not see:

* the morning progress bar filled for two hours after the rider was already
  in class, because that branch's only stop was a school scan that fails about
  two days in three;
* the bar lurched between 78% and 7% every few minutes, because two branches
  measured progress on different scales;
* the activity's title and colour flickered, because they were chosen in two
  places that disagreed;
* it counted down 61 hours to Monday morning, because one branch never
  checked that the arrival it was counting towards was still today.

The integration already holds everything needed to answer it once, properly:
the scans, the live distance, the learned run windows and the predictions.
So it answers it here, and the automation reads the answer.

The function is deliberately pure — no coordinator, no clock of its own — so
every stage transition can be tested directly at any instant of a school day.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import (
    ARRIVED_DWELL_MINUTES,
    NOON_HOUR,
    RUN_AM,
    RUN_PM,
    SOURCE_LEARNED,
    STAGE_AT_SCHOOL,
    STAGE_AT_STOP,
    STAGE_FROM_SCHOOL,
    STAGE_HOME,
    STAGE_IDLE,
    STAGE_TO_HOME,
    STAGE_TO_SCHOOL,
    STAGE_TO_STOP,
)


@dataclass(slots=True)
class Journey:
    """Which stage of the run a rider is in, and how far through it."""

    stage: str
    # 0-100 through whatever the current stage is measuring, or None when
    # there is nothing meaningful to measure.
    progress: int | None = None
    # The instant being counted towards: school on the way there, the home
    # stop on the way back. None when nothing is pending.
    target: datetime | None = None
    # When this journey began, for a bar that fills against elapsed time.
    boarded: datetime | None = None
    # Identifies this journey, so a notification carrying it never updates
    # yesterday's. Date plus run, e.g. "20260914-am".
    journey_id: str | None = None

    @property
    def active(self) -> bool:
        """Return whether anything is happening worth showing."""
        return self.stage != STAGE_IDLE


# Half a minute, the point a target rounds up to the next displayed minute.
_HALF_MINUTE = 30


def _same_day(moment: datetime | None, now: datetime) -> bool:
    """Return whether a local instant falls on the same day as ``now``."""
    return moment is not None and dt_util.as_local(moment).date() == now.date()


def _fraction(start: datetime, end: datetime, now: datetime) -> int:
    """Return how far ``now`` is between two instants, as a percentage."""
    span = max((end - start).total_seconds(), 60.0)
    done = (now - start).total_seconds() / span
    return max(0, min(100, round(done * 100)))


def _closing(distance: float | None, outer: float) -> int | None:
    """Return how far the bus has closed towards the stop, as a percentage."""
    if distance is None:
        return None
    return max(0, min(100, round(((outer - min(distance, outer)) / outer) * 100)))


def _to_the_minute(moment: datetime) -> datetime:
    """Round a target to the minute it will be displayed as.

    The estimate carries microseconds, so the raw target moves on every poll
    even when nothing has really changed. Anything watching it for a reason to
    act — a notification deciding whether to re-push — then fires every thirty
    seconds and says the same thing each time.

    A card shows minutes. Rounding here means the target changes exactly when
    the reader would see it change, which makes "has the estimate moved" a
    question the attribute can answer on its own.
    """
    rounded = moment.replace(second=0, microsecond=0)
    return rounded + timedelta(minutes=1) if moment.second >= _HALF_MINUTE else rounded


def _run_of(moment: datetime) -> str:
    """Return which of the day's two runs a local instant belongs to."""
    return RUN_AM if dt_util.as_local(moment).hour < NOON_HOUR else RUN_PM


def journey_stage(
    *,
    now: datetime,
    distance: float | None,
    arrival_threshold: float,
    outer_rung: float,
    next_arrival: datetime | None,
    next_run: str | None,
    prediction_source: str | None,
    school_arrival: datetime | None,
    last_pickup: datetime | None,
    last_dropoff: datetime | None,
    approach_open: bool,
) -> Journey:
    """Return the rider's current stage.

    ``now`` is local. Ordering matters: having arrived somewhere outranks
    being on the way there, and being aboard outranks the bus merely being
    nearby — otherwise an afternoon approach would describe a rider who is
    already on the bus as though they were still waiting for it.
    """
    at_stop = distance is not None and distance <= arrival_threshold

    # 1. Just scanned off the bus. Holds briefly, then lets the day go quiet.
    #
    #    WHERE they got off depends on which run it was. A drop-off scan in the
    #    morning is the school; in the afternoon it is the home stop. Reading
    #    every drop-off as the school announced "Arrived at school — dropped
    #    off safely" as the rider stepped off the bus outside the house.
    #
    #    That assumption held only because the home leg went unscanned for the
    #    first eight days observed. It is being scanned now, which is better
    #    data and a reminder that "never happens" is a thing to check rather
    #    than build on.
    if _same_day(last_dropoff, now) and last_dropoff is not None:
        since = (now - dt_util.as_local(last_dropoff)).total_seconds()
        if 0 <= since <= ARRIVED_DWELL_MINUTES * 60:
            at_school = _run_of(last_dropoff) == RUN_AM
            return Journey(
                stage=STAGE_AT_SCHOOL if at_school else STAGE_HOME,
                progress=100,
                journey_id=_journey_id(last_dropoff),
            )

    # 2. The bus is at the stop. In the afternoon that is the end of the ride
    #    home; in the morning it is the moment to walk out of the door.
    if at_stop and approach_open and next_run is not None:
        # Which run this IS comes from the clock, not from which run is
        # predicted next. Those differ the moment the bus arrives: the
        # prediction rolls straight on to the afternoon, and reading the stage
        # off it announced "Home" while the rider was still standing at the
        # kerb waiting to be let on for school.
        arrived_stage = STAGE_HOME if _run_of(now) == RUN_PM else STAGE_AT_STOP
        return Journey(stage=arrived_stage, progress=100, journey_id=_journey_id(now))

    # 3. Aboard. The bar fills against elapsed time, because distance to the
    #    home stop says nothing useful while the bus is working its route.
    if _same_day(last_pickup, now) and last_pickup is not None:
        boarded = dt_util.as_local(last_pickup)
        morning = _run_of(boarded) == RUN_AM
        finished = _same_day(last_dropoff, now) and (
            last_dropoff is not None and last_dropoff > last_pickup
        )
        target = school_arrival if morning else next_arrival
        # The target has to be today. Once the journey ends the predictions
        # roll to the next school day, and a bar filling towards a target
        # three days out is never right.
        if not finished and _same_day(target, now) and target is not None:
            local_target = dt_util.as_local(target)
            return Journey(
                stage=STAGE_TO_SCHOOL if morning else STAGE_FROM_SCHOOL,
                progress=_fraction(boarded, local_target, now),
                target=_to_the_minute(local_target),
                boarded=boarded,
                journey_id=_journey_id(boarded),
            )

    # 4. Not aboard, but the bus is making its approach. Only once the run has
    #    actually been learned: anchored to a timetable that was twenty
    #    minutes out, this fired after the bus had already been and gone.
    if (
        approach_open
        and prediction_source == SOURCE_LEARNED
        and _same_day(next_arrival, now)
        and next_arrival is not None
        and next_run is not None
    ):
        return Journey(
            # The clock again, for the same reason: next_run says what is
            # being predicted, and this wants to know what is happening.
            stage=STAGE_TO_HOME if _run_of(now) == RUN_PM else STAGE_TO_STOP,
            progress=_closing(distance, outer_rung),
            target=_to_the_minute(dt_util.as_local(next_arrival)),
            journey_id=_journey_id(now),
        )

    return Journey(stage=STAGE_IDLE)


def _journey_id(moment: datetime) -> str:
    """Return a stable identifier for the journey a local instant belongs to.

    Date plus run, from the clock rather than from the prediction's own run
    attribute — that flips to the afternoon the instant the morning pickup
    passes, which would rename a journey halfway through it.
    """
    local = dt_util.as_local(moment)
    return f"{local.strftime('%Y%m%d')}-{_run_of(local)}"


def approach_is_open(now: datetime, window: tuple[datetime, datetime] | None) -> bool:
    """Return whether a run's approach window is currently open."""
    return window is not None and window[0] <= now <= window[1]


__all__ = ["Journey", "approach_is_open", "journey_stage"]
