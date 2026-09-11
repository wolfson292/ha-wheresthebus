"""Data coordinators for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WheresTheBusApi, WheresTheBusAuthError, WheresTheBusError
from .backfill import async_distance_history, distance_entity_id
from .const import (
    ANCHOR_LADDER_KM,
    ANCHOR_LADDER_MILES,
    APPROACH_LEAD_MINUTES,
    ARRIVAL_HISTORY_LIMIT,
    ARRIVAL_SCHEMA,
    ARRIVAL_STORAGE_KEY,
    ARRIVAL_STORAGE_VERSION,
    ARRIVAL_THRESHOLD_KM,
    ARRIVAL_THRESHOLD_MILES,
    BASIS_APPROACH,
    BASIS_HISTORICAL,
    DOMAIN,
    OUTLIER_FLOOR_MINUTES,
    OUTLIER_MAD_MULTIPLIER,
    RECEDE_HYSTERESIS,
    RUN_AM,
    RUN_PM,
    RUN_WINDOW_MINUTES,
    SCAN_DROPOFF,
    SCAN_HISTORY_LIMIT,
    SCAN_PICKUP,
    SOURCE_LEARNED,
    SOURCE_SCHEDULED,
    STATUS_CURRENT,
    STATUS_INACTIVE,
    STATUS_STALE,
    STORAGE_KEY,
    STORAGE_VERSION,
    TRACK_SAMPLE_LIMIT,
)

_LOGGER = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Local hour that separates the morning run from the afternoon run.
_NOON = 12
_MAX_MINUTE = 59

# "7:56 A.M." / "5:48 PM" — the punctuation varies between districts.
_STOP_TIME_PATTERN = re.compile(
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})\s*(?P<meridiem>[ap])", re.IGNORECASE
)


def parse_stop_time(value: str | None) -> time | None:
    """Parse a scheduled stop time such as ``7:56 A.M.`` into a local time."""
    if not value or not (match := _STOP_TIME_PATTERN.search(value)):
        return None

    hour = int(match.group("hour")) % 12
    if match.group("meridiem").lower() == "p":
        hour += 12
    minute = int(match.group("minute"))
    if minute > _MAX_MINUTE:
        return None
    return time(hour=hour, minute=minute)


@dataclass(slots=True)
class ArrivalPrediction:
    """A predicted arrival of the bus at a rider's stop."""

    run: str
    arrival: datetime
    source: str
    basis: str
    samples: int
    spread: int | None
    outliers: int
    scheduled: time
    # Set only while the estimate hangs on a live rung crossing: which rung,
    # and how many past journeys its typical leg was taken from. A clock
    # median leaves both None, which is what tells the two apart from outside.
    anchored_at: float | None = None
    anchor_samples: int | None = None
    # Where the run's window was centred — the learned arrival once there is
    # one, otherwise the timetable. Exposed because a window centred on a
    # timetable that is twenty minutes out is the failure it hides behind.
    centre: time | None = None


@dataclass(slots=True)
class RunArrival:
    """One observed arrival of the bus at a rider's stop."""

    run: str
    arrival: datetime
    closest: float
    # A replacement vehicle keeps its own time. Recorded so the arrival stays
    # visible, but held out of what the estimate learns from.
    substitute: bool = False
    # Seconds from crossing each anchor rung to reaching the stop, keyed by
    # rung index. Empty for arrivals recorded before this was tracked, or
    # where the bus was already inside every rung when the window opened.
    legs: dict[int, int] = field(default_factory=dict)
    # The shape of the approach: (seconds before arrival, distance) samples,
    # oldest first. The ladder says when the bus passed four points; this says
    # what it did in between, which is what a better model will need.
    track: list[tuple[int, float]] = field(default_factory=list)
    # How often the bus crossed a rung and then fell back outside it. A run
    # with several of these was weaving through nearby stops, and its legs are
    # correspondingly less representative.
    recedes: int = 0
    # Samples during the approach where the GPS fix was not current, so the
    # distances behind them were extrapolated rather than observed.
    stale: int = 0


def _run_centre(
    scheduled: time | None, reference: datetime, learned: time | None = None
) -> datetime | None:
    """Return the local instant a run is expected to arrive.

    ``learned`` is the median of arrivals actually observed, and it wins over
    the timetable whenever there is one: the published time can be twenty
    minutes out, and everything downstream is positioned relative to this.
    """
    centre = learned or scheduled
    if centre is None:
        return None
    return dt_util.as_local(reference).replace(
        hour=centre.hour, minute=centre.minute, second=0, microsecond=0
    )


def run_window(
    scheduled: time | None, reference: datetime, learned: time | None = None
) -> tuple[datetime, datetime] | None:
    """Return the local window in which a run's arrival is believed genuine."""
    centre = _run_centre(scheduled, reference, learned)
    if centre is None:
        return None
    span = timedelta(minutes=RUN_WINDOW_MINUTES)
    return centre - span, centre + span


def approach_window(
    scheduled: time | None, reference: datetime, learned: time | None = None
) -> tuple[datetime, datetime] | None:
    """Return the window in which the run's approach is worth watching.

    Opens earlier than the arrival window so the outer rungs are seen at all,
    and closes with it: a bus still out at three miles half an hour after it
    should have arrived is on some other errand.
    """
    centre = _run_centre(scheduled, reference, learned)
    if centre is None:
        return None
    return (
        centre - timedelta(minutes=APPROACH_LEAD_MINUTES),
        centre + timedelta(minutes=RUN_WINDOW_MINUTES),
    )


# "3 min. ago", "12 mins ago" — the number is the age of the GPS fix.
_AGE_PATTERN = re.compile(r"(\d+)\s*min")


def parse_bus_status(sts_msg: str | None) -> tuple[str | None, int | None]:
    """Split the API's status string into a bounded state and a GPS age.

    ``stsMsg`` is written for humans and changes every minute while a bus is
    running ("current" -> "1 min. ago" -> ... -> "inactive"), which makes it
    useless as an entity state.  It is split into a status with three possible
    values and the age of the GPS fix in minutes.

    Returns ``(None, None)`` for anything unrecognised so a vocabulary the API
    adds later shows as unknown rather than breaking the enum sensor; the raw
    string is kept as an attribute either way.
    """
    if not sts_msg or not (text := sts_msg.strip()):
        return None, None

    lowered = text.lower()
    if lowered.startswith(STATUS_CURRENT):
        return STATUS_CURRENT, 0
    if lowered.startswith(STATUS_INACTIVE):
        return STATUS_INACTIVE, None
    if match := _AGE_PATTERN.search(lowered):
        return STATUS_STALE, int(match.group(1))
    return None, None


def _normalise(value: str | None) -> str:
    """Reduce a place name to comparable lowercase alphanumerics."""
    if not value:
        return ""
    return _NON_ALNUM.sub("", value.lower())


@dataclass(slots=True)
class ScanEvent:
    """A single student ID scan."""

    timestamp: datetime
    location: str
    method: str
    bus: str
    kind: str | None = None


@dataclass(slots=True)
class Student:
    """Everything known about one rider, refreshed on the slow coordinator."""

    child_id: int
    name: str
    student_id: str | None = None
    bus_number: str | None = None
    route_number: str | None = None
    # The API names a replacement vehicle here when one is covering the route.
    substitute_bus: str | None = None
    school_name: str | None = None
    am_stop_time: str | None = None
    pm_stop_time: str | None = None
    am_scheduled: time | None = None
    pm_scheduled: time | None = None
    stop_address: str | None = None
    stop_latitude: float | None = None
    stop_longitude: float | None = None
    scans: list[ScanEvent] = field(default_factory=list)

    @property
    def last_scan(self) -> ScanEvent | None:
        """Return the most recent scan of any kind."""
        return self.scans[-1] if self.scans else None

    def last_scan_of(self, kind: str) -> ScanEvent | None:
        """Return the most recent scan classified as ``kind``."""
        for scan in reversed(self.scans):
            if scan.kind == kind:
                return scan
        return None


def classify_scans(scans: list[ScanEvent], school_name: str | None) -> list[ScanEvent]:
    """Label each scan as a pickup or a drop-off.

    The API reports scans as bare "ID received" events with a location, so the
    direction has to be inferred.  A scan whose location matches the school is
    a drop-off in the morning and a pickup in the afternoon; a scan anywhere
    else (i.e. at the neighbourhood stop) is the reverse.  When the school name
    is unknown or does not match, the scan's position within its own day is
    used instead, since a normal day alternates pickup, drop-off, pickup,
    drop-off.
    """
    school = _normalise(school_name)
    day_counts: dict[Any, int] = {}

    for scan in sorted(scans, key=lambda item: item.timestamp):
        local = dt_util.as_local(scan.timestamp)
        index = day_counts.get(local.date(), 0)
        day_counts[local.date()] = index + 1

        location = _normalise(scan.location)
        at_school = bool(school) and (school in location or location in school)

        if at_school:
            scan.kind = SCAN_DROPOFF if local.hour < _NOON else SCAN_PICKUP
        elif location:
            scan.kind = SCAN_PICKUP if local.hour < _NOON else SCAN_DROPOFF
        else:
            scan.kind = SCAN_PICKUP if index % 2 == 0 else SCAN_DROPOFF

    return sorted(scans, key=lambda item: item.timestamp)


@dataclass(slots=True)
class ApproachRecorder:
    """Accumulates one run's approach as the distance readings come in.

    Shared by the live path and the recorder replay so that a journey learned
    from history and one watched as it happened produce the same record. They
    drifted apart once before, and the replay quietly learned less.
    """

    crossings: dict[int, datetime] = field(default_factory=dict)
    track: list[tuple[datetime, float]] = field(default_factory=list)
    recedes: int = 0
    stale: int = 0
    # Set once the bus has actually reached the stop. Everything after that is
    # the bus leaving again, and must not be read as the approach coming apart.
    arrived: bool = False

    def sample(
        self,
        ladder: tuple[float, ...],
        when: datetime,
        distance: float,
        arrival_threshold: float,
        *,
        fresh: bool = True,
    ) -> None:
        """Fold one distance reading into the approach."""
        if self.arrived:
            # The journey is over; the bus pulling away is not new information.
            return

        self.track.append((when, distance))
        if not fresh:
            self.stale += 1
        if distance <= arrival_threshold:
            self.arrived = True

        for rung, threshold in enumerate(ladder):
            if distance <= threshold:
                self.crossings.setdefault(rung, when)
            elif (
                not self.arrived
                and distance > threshold * RECEDE_HYSTERESIS
                and rung in self.crossings
            ):
                # It crossed, then went back out without ever reaching the
                # stop: that was not the final approach, so the crossing must
                # not anchor anything.
                del self.crossings[rung]
                self.recedes += 1

    def finish(self, arrival: datetime) -> dict[str, Any]:
        """Return the fields describing this approach, given when it ended."""
        return {
            "legs": {
                rung: int((arrival - crossed).total_seconds())
                for rung, crossed in self.crossings.items()
                if crossed <= arrival
            },
            "track": [
                (int((arrival - when).total_seconds()), distance)
                for when, distance in self.track[-TRACK_SAMPLE_LIMIT:]
                if when <= arrival
            ],
            "recedes": self.recedes,
            "stale": self.stale,
        }


def reconstruct_arrivals(
    states: list[State],
    student: Student,
    arrival_threshold: float,
    ladder: tuple[float, ...],
    learned: dict[str, time] | None = None,
) -> list[RunArrival]:
    """Find each run's arrival in a stretch of recorded distance readings.

    Applies exactly the filters the live path applies: the closest approach
    inside a run's window is that run's arrival, provided it actually reached
    the stop, and the approach either side of it gives the rung timings.
    """
    learned = learned or {}
    best: dict[tuple[date, str], tuple[float, datetime]] = {}
    approaches: dict[tuple[date, str], ApproachRecorder] = {}

    for state in states:
        try:
            distance = float(state.state)
        except (TypeError, ValueError):
            continue

        when = state.last_updated
        local = dt_util.as_local(when)
        for run, scheduled in (
            (RUN_AM, student.am_scheduled),
            (RUN_PM, student.pm_scheduled),
        ):
            centre = learned.get(run)
            watching = approach_window(scheduled, when, centre)
            if watching is None or not watching[0] <= local <= watching[1]:
                continue
            key = (local.date(), run)
            approaches.setdefault(key, ApproachRecorder()).sample(
                ladder, when, distance, arrival_threshold
            )

            arriving = run_window(scheduled, when, centre)
            if arriving is None or not arriving[0] <= local <= arriving[1]:
                continue
            current = best.get(key)
            if current is None or distance < current[0]:
                best[key] = (distance, when)

    arrivals: list[RunArrival] = []
    for (day, run), (closest, when) in best.items():
        if closest > arrival_threshold:
            continue
        approach = approaches.get((day, run), ApproachRecorder())
        arrivals.append(
            RunArrival(run=run, arrival=when, closest=closest, **approach.finish(when))
        )
    return sorted(arrivals, key=lambda item: item.arrival)


@dataclass(slots=True)
class SchoolArrival:
    """A predicted arrival at school on the morning run."""

    arrival: datetime
    samples: int
    ride_minutes: int | None


def predict_school_arrival(
    student: Student, local_now: datetime
) -> SchoolArrival | None:
    """Predict when the morning ride reaches school.

    Learned from the drop-off scans the school itself records, which are
    already in the scan history — the same median with the same outlier
    rejection used for stop arrivals, so a morning stuck in traffic does not
    drag the estimate.

    Also reports the typical ride length, measured pickup to drop-off on the
    same day, which is what lets a progress bar fill across the journey rather
    than only counting elapsed time.
    """
    arrivals: list[int] = []
    rides: list[int] = []
    pickups: dict[date, datetime] = {}

    for scan in student.scans:
        local = dt_util.as_local(scan.timestamp)
        if local.hour >= _NOON:
            continue
        if scan.kind == SCAN_PICKUP:
            pickups.setdefault(local.date(), local)
        elif scan.kind == SCAN_DROPOFF:
            arrivals.append(local.hour * 60 + local.minute)
            if (boarded := pickups.get(local.date())) is not None:
                rides.append(int((local - boarded).total_seconds() // 60))

    if not arrivals:
        return None

    kept, _ = _reject_outliers(sorted(arrivals))
    middle = _median(kept)
    moment = local_now.replace(
        hour=middle // 60, minute=middle % 60, second=0, microsecond=0
    )
    if moment <= local_now:
        moment += timedelta(days=1)

    ride = _median(sorted(rides)) if rides else None
    return SchoolArrival(
        arrival=dt_util.as_utc(moment), samples=len(kept), ride_minutes=ride
    )


class WheresTheBusStudentCoordinator(DataUpdateCoordinator[dict[int, Student]]):
    """Refresh the roster, stop details and ID scan history."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: WheresTheBusApi,
        interval: int,
    ) -> None:
        """Initialise the roster coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} students",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self._store: Store[dict[str, list[dict[str, Any]]]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{config_entry.entry_id}"
        )
        self._history: dict[int, list[ScanEvent]] = {}

    async def async_load_history(self) -> None:
        """Restore accumulated scans saved by a previous run."""
        stored = await self._store.async_load() or {}
        for raw_child_id, scans in stored.items():
            try:
                child_id = int(raw_child_id)
            except (TypeError, ValueError):
                continue
            events = []
            for scan in scans:
                if (timestamp := dt_util.parse_datetime(scan.get("t", ""))) is None:
                    continue
                events.append(
                    ScanEvent(
                        timestamp=timestamp,
                        location=scan.get("l") or "",
                        method=scan.get("m") or "",
                        bus=scan.get("b") or "",
                    )
                )
            if events:
                self._history[child_id] = events
        _LOGGER.debug("Restored scans for %d rider(s)", len(self._history))

    async def _async_save_history(self) -> None:
        """Persist accumulated scans."""
        await self._store.async_save(
            {
                str(child_id): [
                    {
                        "t": scan.timestamp.isoformat(),
                        "l": scan.location,
                        "m": scan.method,
                        "b": scan.bus,
                    }
                    for scan in scans
                ]
                for child_id, scans in self._history.items()
            }
        )

    def _merge_scans(self, child_id: int, fresh: list[ScanEvent]) -> list[ScanEvent]:
        """Fold today's scans into the rider's accumulated history.

        ``getStudentScan`` only ever returns the current day, so replacing the
        list outright would blank every scan sensor at midnight and again on
        each restart.  Scans are merged by timestamp and trimmed to a rolling
        window instead, which keeps "last pickup" meaningful overnight and
        keeps the afternoon pickup visible through the following morning.
        """
        known = {scan.timestamp: scan for scan in self._history.get(child_id, [])}
        for scan in fresh:
            known[scan.timestamp] = scan

        merged = sorted(known.values(), key=lambda item: item.timestamp)
        merged = merged[-SCAN_HISTORY_LIMIT:]
        self._history[child_id] = merged
        return merged

    async def _async_update_data(self) -> dict[int, Student]:
        """Fetch user info, roster and scans, and merge them per student."""
        try:
            user_info = await self.api.async_get_user_info()
            riders = await self.api.async_get_all_riders()
            scan_payload = await self.api.async_get_student_scans()
        except WheresTheBusAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WheresTheBusError as err:
            raise UpdateFailed(str(err)) from err

        # ``childBuses`` is the only place the numeric child id appears, and
        # that id is what ``getRiderInfoEx`` needs, so it drives the roster.
        students = _pair_riders(user_info.get("childBuses") or [], riders)
        _attach_scans(students, scan_payload)

        before = {child_id: len(scans) for child_id, scans in self._history.items()}
        for child_id, student in students.items():
            merged = self._merge_scans(child_id, student.scans)
            student.scans = classify_scans(merged, student.school_name)
        if before != {
            child_id: len(scans) for child_id, scans in self._history.items()
        }:
            await self._async_save_history()

        return students


def _load_legs(item: dict[str, Any]) -> dict[int, int]:
    """Read stored final-leg durations, accepting the older single-anchor form."""
    raw = item.get("legs")
    if isinstance(raw, dict):
        legs: dict[int, int] = {}
        for key, value in raw.items():
            try:
                legs[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return legs
    # Pre-ladder records held one duration, measured at what is now rung 2.
    single = item.get("approach")
    return {2: int(single)} if isinstance(single, int) else {}


# Each track sample is stored as a bare (age, distance) pair.
_TRACK_POINT_LENGTH = 2


def _load_track(item: dict[str, Any]) -> list[tuple[int, float]]:
    """Read a stored approach track, tolerating records written without one."""
    raw = item.get("track")
    if not isinstance(raw, list):
        return []
    track: list[tuple[int, float]] = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != _TRACK_POINT_LENGTH:
            continue
        try:
            track.append((int(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    return track


def _median(values: list[int]) -> int:
    """Return the middle value of a sorted, non-empty list."""
    return values[len(values) // 2]


def _reject_outliers(times: list[int]) -> tuple[list[int], int]:
    """Drop arrivals far enough from the median to be a bad day, not a pattern.

    ``times`` must be sorted. Returns the arrivals to learn from and how many
    were discarded. With too few samples to judge, everything is kept: two
    arrivals cannot tell you which of them is the anomaly.
    """
    minimum_to_judge = 3
    if len(times) < minimum_to_judge:
        return times, 0

    middle = _median(times)
    deviation = _median(sorted(abs(value - middle) for value in times))
    threshold = max(OUTLIER_MAD_MULTIPLIER * deviation, OUTLIER_FLOOR_MINUTES)

    kept = [value for value in times if abs(value - middle) <= threshold]
    # Never discard everything, however strange the data looks.
    if not kept:
        return times, 0
    return kept, len(times) - len(kept)


def _merge_arrivals(
    existing: list[RunArrival], fresh: list[RunArrival]
) -> list[RunArrival]:
    """Combine two sets of arrivals, keeping whichever record knows more.

    A bus reaches a given stop once per run per day, so that pair identifies
    an arrival — not its timestamp, which differs by microseconds between the
    live path (the clock when the poll landed) and a replay (the recorder's
    own stamp for the same reading).

    Treating those as separate arrivals had two consequences. Duplicates
    accumulated, each counting towards the sample total. Worse, a replay
    carrying a fuller record was discarded as already known, so the four-rung
    ladder kept losing to the single rung an older version had written, and
    the morning estimate never re-anchored.
    """
    by_run_day: dict[tuple[str, date], RunArrival] = {}
    for item in [*existing, *fresh]:
        key = (item.run, dt_util.as_local(item.arrival).date())
        current = by_run_day.get(key)
        if current is None or len(item.legs) > len(current.legs):
            by_run_day[key] = item
    return sorted(by_run_day.values(), key=lambda item: item.arrival)


def _trim_per_run(history: list[RunArrival]) -> list[RunArrival]:
    """Keep the most recent ARRIVAL_HISTORY_LIMIT arrivals of each run."""
    kept: list[RunArrival] = []
    for run in (RUN_AM, RUN_PM):
        matching = [item for item in history if item.run == run]
        kept.extend(matching[-ARRIVAL_HISTORY_LIMIT:])
    kept.sort(key=lambda item: item.arrival)
    return kept


def _pair_riders(
    child_buses: list[dict[str, Any]], riders: list[dict[str, Any]]
) -> dict[int, Student]:
    """Join ``childBuses`` entries to their ``allRiders`` records.

    The two endpoints share no identifier, so riders are claimed by bus number
    plus scheduled stop time first, then by bus number alone, and only then by
    list position.  Each rider can be claimed once, which keeps two children on
    the same bus from collapsing onto one record.
    """
    entries = [bus for bus in child_buses if bus.get("childId") is not None]
    unclaimed = list(range(len(riders)))
    claimed: dict[int, int] = {}

    def bus_numbers(rider: dict[str, Any]) -> set[str]:
        return {
            str(value)
            for key in ("amBusNo", "pmBusNo", "latePmBusNo")
            if (value := rider.get(key))
        }

    def stop_times(rider: dict[str, Any]) -> set[str]:
        return {
            _normalise(value)
            for key in ("amStopTime", "pmStopTime", "latePmStopTime")
            if (value := rider.get(key))
        }

    for strict in (True, False):
        for position, bus in enumerate(entries):
            if position in claimed:
                continue
            bus_no = str(bus.get("busNo") or "")
            bus_time = _normalise(bus.get("busTime"))
            matches = [
                index
                for index in unclaimed
                if bus_no in bus_numbers(riders[index])
                and (
                    not strict
                    or any(
                        time.startswith(bus_time)
                        for time in stop_times(riders[index])
                        if bus_time
                    )
                )
            ]
            if len(matches) == 1:
                claimed[position] = matches[0]
                unclaimed.remove(matches[0])

    for position in range(len(entries)):
        if position not in claimed and unclaimed:
            claimed[position] = unclaimed.pop(0)

    students: dict[int, Student] = {}
    for position, bus in enumerate(entries):
        index = claimed.get(position)
        rider = riders[index] if index is not None else {}
        child_id = bus["childId"]
        students[child_id] = _build_student(child_id, bus, rider)
    return students


def _build_student(
    child_id: int, bus: dict[str, Any], rider: dict[str, Any]
) -> Student:
    """Combine a ``childBuses`` entry with its ``allRiders`` record."""
    bus_number = bus.get("busNo") or rider.get("amBusNo") or rider.get("pmBusNo")

    # The AM and PM stop are the same place for most riders; prefer whichever
    # one actually carries coordinates.
    stop_latitude = rider.get("amStopLat") or rider.get("pmStopLat")
    stop_longitude = rider.get("amStopLon") or rider.get("pmStopLon")

    return Student(
        child_id=child_id,
        name=rider.get("riderName") or f"Rider {child_id}",
        student_id=rider.get("studentId"),
        bus_number=bus_number,
        route_number=bus.get("routeNo"),
        substitute_bus=(bus.get("sub") or "").strip() or None,
        school_name=rider.get("schoolName"),
        am_stop_time=rider.get("amStopTime"),
        pm_stop_time=rider.get("pmStopTime"),
        am_scheduled=parse_stop_time(rider.get("amStopTime")),
        pm_scheduled=parse_stop_time(rider.get("pmStopTime")),
        stop_address=rider.get("amStopAddress") or rider.get("pmStopAddress"),
        stop_latitude=stop_latitude or None,
        stop_longitude=stop_longitude or None,
    )


def _attach_scans(students: dict[int, Student], payload: dict[str, Any]) -> None:
    """Attach ``getStudentScan`` results to the roster, matching by rider name.

    The scan endpoint keys students by name rather than by child id, so names
    are compared with punctuation and case removed.  Middle names in the roster
    ("Robin Alex Rivera" vs "Robin Rivera") mean a first-and-last match is used.
    """
    details = payload.get("studentDetails") or []
    if not details:
        return

    by_key = {_normalise(student.name): student for student in students.values()}

    for detail in details:
        scan_name = _normalise(detail.get("studentName"))
        student = by_key.get(scan_name)
        if student is None:
            student = _match_by_name_parts(scan_name, students)
        if student is None and len(students) == 1 and len(details) == 1:
            # Single-child account: the names must refer to the same rider.
            student = next(iter(students.values()))
        if student is None:
            _LOGGER.debug("No roster match for scanned student %s", scan_name)
            continue

        events = [
            ScanEvent(
                timestamp=dt_util.utc_from_timestamp(scan_time),
                location=scan.get("scanLocation") or "",
                method=scan.get("scanMethod") or "",
                bus=scan.get("bus") or "",
            )
            for scan in detail.get("studentScans") or []
            if (scan_time := scan.get("scanTime"))
        ]
        # Left unclassified: the coordinator merges these into the rider's
        # accumulated history first, then classifies the whole window at once.
        student.scans = events


def _match_by_name_parts(
    scan_name: str, students: dict[int, Student]
) -> Student | None:
    """Fall back to a first-and-last-name match for a scanned student."""
    if not scan_name:
        return None
    for student in students.values():
        parts = student.name.split()
        if not parts:
            continue
        short = _normalise(f"{parts[0]}{parts[-1]}")
        if short and short == scan_name:
            return student
    return None


class WheresTheBusBusCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Poll live bus positions for every rider on the account."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: WheresTheBusApi,
        students: WheresTheBusStudentCoordinator,
        interval: int,
    ) -> None:
        """Initialise the live-position coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} buses",
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.students = students
        self.distance_in_km = False
        self._last_server_time: dict[int, int] = {}
        self._store: Store[dict[str, list[dict[str, Any]]]] = Store(
            hass,
            ARRIVAL_STORAGE_VERSION,
            f"{ARRIVAL_STORAGE_KEY}.{config_entry.entry_id}",
        )
        self._arrivals: dict[int, list[RunArrival]] = {}
        # Which recording scheme the stored arrivals were captured under.
        self._schema = 0
        # (child_id, run, local date) -> closest approach seen so far.
        self._pending: dict[tuple[int, str, date], tuple[float, datetime]] = {}
        # (child_id, run, local date) -> the approach as it is unfolding.
        self._approach: dict[tuple[int, str, date], ApproachRecorder] = {}

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        """Fetch one position update per rider."""
        roster = self.students.data or {}
        results: dict[int, dict[str, Any]] = {}

        for child_id, student in roster.items():
            if not student.bus_number:
                continue
            try:
                info = await self.api.async_get_rider_info(
                    student.bus_number,
                    child_id,
                    self._last_server_time.get(child_id, 0),
                )
            except WheresTheBusAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except WheresTheBusError as err:
                raise UpdateFailed(str(err)) from err

            self._last_server_time[child_id] = info.get("serverTime") or 0
            results[child_id] = info

            # ``isDistKm`` is an account-level flag repeated on every response.
            self.distance_in_km = bool(info.get("isDistKm"))
            self._observe(
                child_id,
                student,
                info.get("dist"),
                parse_bus_status(info.get("stsMsg"))[0],
            )

        if self._promote_pending():
            await self._async_save_arrivals()

        return results

    # ------------------------------------------------------------------
    # Arrival observation
    # ------------------------------------------------------------------

    @property
    def _ladder(self) -> tuple[float, ...]:
        """Return the anchor rungs, loosest first, in the account's units."""
        return ANCHOR_LADDER_KM if self.distance_in_km else ANCHOR_LADDER_MILES

    @property
    def _arrival_threshold(self) -> float:
        """Return how close counts as an arrival, in the account's units."""
        return ARRIVAL_THRESHOLD_KM if self.distance_in_km else ARRIVAL_THRESHOLD_MILES

    def _window_centre(self, child_id: int, run: str) -> time | None:
        """Return the learned arrival for a run, or None while it has none."""
        return self._learned_time(child_id, run)[0]

    def _observe(
        self, child_id: int, student: Student, dist: Any, status: str | None = None
    ) -> None:
        """Fold one position reading into whichever run is currently active.

        The approach is watched from well before the arrival window opens, so
        the outer rungs are recorded rather than missed; only readings inside
        the tighter arrival window can count as the arrival itself.
        """
        if dist is None:
            return
        try:
            distance = float(dist)
        except (TypeError, ValueError):
            return

        now = dt_util.utcnow()
        local_now = dt_util.as_local(now)
        # Anything but a current fix means the distance behind it was
        # extrapolated, which is what the stale count is recording.
        fresh = status == STATUS_CURRENT
        for run, scheduled in (
            (RUN_AM, student.am_scheduled),
            (RUN_PM, student.pm_scheduled),
        ):
            centre = self._window_centre(child_id, run)
            watching = approach_window(scheduled, now, centre)
            if watching is None or not watching[0] <= local_now <= watching[1]:
                continue
            key = (child_id, run, local_now.date())
            self._approach.setdefault(key, ApproachRecorder()).sample(
                self._ladder, now, distance, self._arrival_threshold, fresh=fresh
            )

            arriving = run_window(scheduled, now, centre)
            if arriving is None or not arriving[0] <= local_now <= arriving[1]:
                continue
            best = self._pending.get(key)
            if best is None or distance < best[0]:
                self._pending[key] = (distance, now)

    def _promote_pending(self) -> bool:
        """Turn closed windows into arrivals. Returns True if anything changed."""
        now = dt_util.utcnow()
        local_now = dt_util.as_local(now)
        changed = False

        # The approach is watched over a wider window than the arrival, so a
        # run can have a recorded approach and no pending arrival at all —
        # a day nobody was collected. Both have to be swept.
        for key in list(self._pending | self._approach.keys()):
            child_id, run, day = key
            student = (self.students.data or {}).get(child_id)
            if student is None:
                self._pending.pop(key, None)
                self._approach.pop(key, None)
                continue

            scheduled = student.am_scheduled if run == RUN_AM else student.pm_scheduled
            window = run_window(scheduled, now, self._window_centre(child_id, run))
            still_open = (
                window is not None
                and day == local_now.date()
                and local_now <= window[1]
            )
            if still_open:
                continue

            approach = self._approach.pop(key, ApproachRecorder())
            pending = self._pending.pop(key, None)
            # A run where the bus never really came — nobody to collect, or a
            # cancelled route — must not be learned as an arrival time.
            if pending is None or pending[0] > self._arrival_threshold:
                _LOGGER.debug(
                    "Ignoring %s run on %s: closest approach was %s",
                    run,
                    day,
                    "never inside the window"
                    if pending is None
                    else f"{pending[0]:.1f}",
                )
                continue

            closest, when = pending
            history = self._arrivals.setdefault(child_id, [])
            history.append(
                RunArrival(
                    run=run,
                    arrival=when,
                    closest=closest,
                    substitute=student.substitute_bus is not None,
                    **approach.finish(when),
                )
            )
            history.sort(key=lambda item: item.arrival)
            self._arrivals[child_id] = _trim_per_run(history)
            # Without this the arrival lived only in memory: the caller saves
            # on a True, and this was left at False, so every journey learned
            # while running was lost on the next restart and only came back if
            # the recorder still held it.
            changed = True

        return changed

    def arrival_diagnostics(self) -> dict[str, Any]:
        """Describe what has been learned, for the diagnostics download.

        Which rungs of the ladder actually carry data is the single most
        useful thing to know when an estimate refuses to re-anchor, and it
        was invisible from the outside until this existed.
        """
        return {
            "schema": self._schema,
            "ladder": list(self._ladder),
            "riders": {
                str(child_id): {
                    "arrivals": [
                        {
                            "run": item.run,
                            "at": dt_util.as_local(item.arrival).isoformat(),
                            "closest": item.closest,
                            "legs_seconds": dict(sorted(item.legs.items())),
                            "substitute": item.substitute,
                            "recedes": item.recedes,
                            "stale_samples": item.stale,
                            # (seconds before arrival, distance), oldest
                            # first — the raw material for fitting a better
                            # estimator than the median-of-legs one.
                            "track": [list(point) for point in item.track],
                        }
                        for item in arrivals
                    ],
                    "rungs_with_data": {
                        run: sorted(
                            {
                                rung
                                for item in arrivals
                                if item.run == run
                                for rung in item.legs
                            }
                        )
                        for run in (RUN_AM, RUN_PM)
                    },
                    # How steady each rung actually is. A rung whose legs
                    # vary by eight minutes is not worth anchoring to, and
                    # that was invisible while only the median was reported.
                    "leg_spread_seconds": {
                        run: {
                            rung: max(legs) - min(legs)
                            for rung in range(len(self._ladder))
                            if (
                                legs := [
                                    item.legs[rung]
                                    for item in arrivals
                                    if item.run == run and rung in item.legs
                                ]
                            )
                        }
                        for run in (RUN_AM, RUN_PM)
                    },
                    "window_centre": {
                        run: (
                            centre.isoformat()
                            if (centre := self._window_centre(child_id, run))
                            else None
                        )
                        for run in (RUN_AM, RUN_PM)
                    },
                }
                for child_id, arrivals in self._arrivals.items()
            },
            "in_flight": {
                f"{child_id}/{run}/{day}": {
                    "crossed": sorted(approach.crossings),
                    "samples": len(approach.track),
                    "recedes": approach.recedes,
                    "stale": approach.stale,
                }
                for (child_id, run, day), approach in self._approach.items()
            },
        }

    async def async_backfill_arrivals(self) -> None:
        """Recover past arrivals from the recorder, once.

        Skipped once the stored history was captured under the current
        recording scheme. Checking merely that some final leg exists is not
        enough: 1.7.0 widened one anchor into a four-rung ladder, and history
        holding only the old single rung would have been left as it was and
        behaved exactly as its predecessor did.
        """
        complete = self._schema >= ARRIVAL_SCHEMA and any(
            item.legs for arrivals in self._arrivals.values() for item in arrivals
        )
        if complete:
            return

        students = self.students.data or {}
        if not students:
            return

        recovered: dict[int, list[RunArrival]] = {}
        for child_id, student in students.items():
            entity_id = distance_entity_id(self.hass, child_id)
            if entity_id is None:
                continue
            try:
                rows = await async_distance_history(self.hass, entity_id)
            except Exception:
                _LOGGER.exception(
                    "Could not read %s history from the recorder", entity_id
                )
                continue
            # Replay through the same windows the live path uses. Centring
            # on the timetable here would rediscover exactly the gap this
            # release exists to close.
            arrivals = reconstruct_arrivals(
                rows,
                student,
                self._arrival_threshold,
                self._ladder,
                {
                    run: centre
                    for run in (RUN_AM, RUN_PM)
                    if (centre := self._window_centre(child_id, run)) is not None
                },
            )
            if arrivals:
                recovered[child_id] = arrivals
                _LOGGER.info(
                    "Recovered %d past arrival(s) for %s from the recorder",
                    len(arrivals),
                    entity_id,
                )

        for child_id, arrivals in recovered.items():
            merged = _merge_arrivals(self._arrivals.get(child_id, []), arrivals)
            self._arrivals[child_id] = _trim_per_run(merged)

        # Recorded even when nothing new was found, so an instance whose
        # recorder has no history left does not replay on every restart.
        self._schema = ARRIVAL_SCHEMA
        await self._async_save_arrivals()

    async def async_load_arrivals(self) -> None:
        """Restore observed arrivals saved by a previous run."""
        stored = await self._store.async_load() or {}
        # Older stores were a bare rider mapping, with no schema recorded.
        if "riders" in stored:
            self._schema = int(stored.get("schema") or 0)
            stored = stored.get("riders") or {}
        for raw_child_id, arrivals in stored.items():
            try:
                child_id = int(raw_child_id)
            except (TypeError, ValueError):
                continue
            restored = [
                RunArrival(
                    run=item["run"],
                    arrival=parsed,
                    closest=float(item.get("closest", 0.0)),
                    legs=_load_legs(item),
                    substitute=bool(item.get("sub")),
                    track=_load_track(item),
                    recedes=int(item.get("recedes", 0) or 0),
                    stale=int(item.get("stale", 0) or 0),
                )
                for item in arrivals
                if item.get("run") in (RUN_AM, RUN_PM)
                and (parsed := dt_util.parse_datetime(item.get("at", ""))) is not None
            ]
            if restored:
                # Collapses duplicates written by earlier versions, which each
                # counted towards the sample total and skewed the spread.
                self._arrivals[child_id] = _merge_arrivals(restored, [])

    async def _async_save_arrivals(self) -> None:
        """Persist observed arrivals."""
        await self._store.async_save(
            {
                "schema": self._schema,
                "riders": {
                    str(child_id): [
                        {
                            "run": item.run,
                            "at": item.arrival.isoformat(),
                            "closest": item.closest,
                            "legs": {str(k): v for k, v in item.legs.items()},
                            "sub": item.substitute,
                            # Pairs rather than objects: the track is by far
                            # the biggest thing in this store, and keys
                            # repeated 80 times an arrival add up fast.
                            "track": [[age, dist] for age, dist in item.track],
                            "recedes": item.recedes,
                            "stale": item.stale,
                        }
                        for item in arrivals
                    ]
                    for child_id, arrivals in self._arrivals.items()
                },
            }
        )

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict_next_arrival(self, child_id: int) -> ArrivalPrediction | None:
        """Predict when the bus next reaches this rider's stop."""
        student = (self.students.data or {}).get(child_id)
        if student is None:
            return None

        local_now = dt_util.as_local(dt_util.utcnow())
        candidates: list[ArrivalPrediction] = []

        for run, scheduled in (
            (RUN_AM, student.am_scheduled),
            (RUN_PM, student.pm_scheduled),
        ):
            if scheduled is None:
                continue
            learned, samples, spread, outliers = self._learned_time(child_id, run)

            # Once the bus is inside the approach anchor for this run, when it
            # set off no longer matters: what is left is the final leg, which
            # is far steadier. Anchor to the crossing and stop guessing.
            anchored = self._anchored_arrival(child_id, run, local_now)
            if anchored is not None:
                when, rung_distance, rung_samples = anchored
                candidates.append(
                    ArrivalPrediction(
                        run=run,
                        arrival=dt_util.as_utc(when),
                        source=SOURCE_LEARNED,
                        basis=BASIS_APPROACH,
                        samples=samples,
                        spread=spread,
                        outliers=outliers,
                        scheduled=scheduled,
                        anchored_at=rung_distance,
                        anchor_samples=rung_samples,
                        centre=learned or scheduled,
                    )
                )
                continue

            predicted_time = learned or scheduled
            for day_offset in (0, 1):
                moment = (local_now + timedelta(days=day_offset)).replace(
                    hour=predicted_time.hour,
                    minute=predicted_time.minute,
                    second=0,
                    microsecond=0,
                )
                if moment > local_now:
                    candidates.append(
                        ArrivalPrediction(
                            run=run,
                            arrival=dt_util.as_utc(moment),
                            source=SOURCE_LEARNED if learned else SOURCE_SCHEDULED,
                            basis=BASIS_HISTORICAL if learned else SOURCE_SCHEDULED,
                            samples=samples,
                            spread=spread,
                            outliers=outliers,
                            scheduled=scheduled,
                            centre=learned or scheduled,
                        )
                    )
                    break

        if not candidates:
            return None
        return min(candidates, key=lambda item: item.arrival)

    def _anchored_arrival(
        self, child_id: int, run: str, local_now: datetime
    ) -> tuple[datetime, float, int] | None:
        """Estimate arrival from the live approach, or None if not applicable.

        Applies only while this run's bus has crossed a rung today, is still
        closing, and has not yet arrived.  Returns the crossing time plus the
        typical leg from that rung, alongside the rung's distance and how many
        past journeys back it — so an early bus is reported early instead of
        being averaged back towards the usual clock time.
        """
        approach = self._approach.get((child_id, run, local_now.date()))
        if approach is None or not approach.crossings:
            return None

        # Tightest rung first: the closer the bus was when it crossed, the less
        # of the journey is left to vary.
        for rung in sorted(approach.crossings, reverse=True):
            legs = sorted(
                item.legs[rung]
                for item in self._arrivals.get(child_id, [])
                if item.run == run and rung in item.legs and not item.substitute
            )
            if legs:
                break
        else:
            return None

        crossed = dt_util.as_local(approach.crossings[rung])
        estimate = crossed + timedelta(seconds=_median(legs))
        # A bus already overdue against this estimate has arrived, or is about
        # to; leave it be rather than reporting a time in the past.
        if estimate <= local_now:
            return None
        return estimate, self._ladder[rung], len(legs)

    def _learned_time(
        self, child_id: int, run: str
    ) -> tuple[time | None, int, int | None, int]:
        """Return the typical arrival for a run, its spread, and outliers cut.

        Badly late days are discarded before the median is taken.  The median
        already resists them, but they would still widen the reported spread
        and, if several accumulated, drag the prediction — so they are excluded
        from the calculation while remaining in history.
        """
        times = sorted(
            dt_util.as_local(item.arrival).hour * 60
            + dt_util.as_local(item.arrival).minute
            for item in self._arrivals.get(child_id, [])
            if item.run == run and not item.substitute
        )
        if not times:
            return None, 0, None, 0

        kept, excluded = _reject_outliers(times)
        middle = _median(kept)
        spread = kept[-1] - kept[0]
        return time(hour=middle // 60, minute=middle % 60), len(kept), spread, excluded
