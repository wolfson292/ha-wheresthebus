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
    APPROACH_ANCHOR_KM,
    APPROACH_ANCHOR_MILES,
    ARRIVAL_HISTORY_LIMIT,
    ARRIVAL_STORAGE_KEY,
    ARRIVAL_STORAGE_VERSION,
    ARRIVAL_THRESHOLD_KM,
    ARRIVAL_THRESHOLD_MILES,
    BASIS_APPROACH,
    BASIS_HISTORICAL,
    DOMAIN,
    OUTLIER_FLOOR_MINUTES,
    OUTLIER_MAD_MULTIPLIER,
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


@dataclass(slots=True)
class RunArrival:
    """One observed arrival of the bus at a rider's stop."""

    run: str
    arrival: datetime
    closest: float
    # Seconds from crossing the approach anchor to reaching the stop. None for
    # arrivals recorded before this was tracked, or where the bus was already
    # inside the anchor when the window opened.
    approach_seconds: int | None = None


def run_window(
    scheduled: time | None, reference: datetime
) -> tuple[datetime, datetime] | None:
    """Return the local window in which a run's arrival is believed genuine."""
    if scheduled is None:
        return None
    centre = dt_util.as_local(reference).replace(
        hour=scheduled.hour,
        minute=scheduled.minute,
        second=0,
        microsecond=0,
    )
    span = timedelta(minutes=RUN_WINDOW_MINUTES)
    return centre - span, centre + span


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


def reconstruct_arrivals(
    states: list[State],
    student: Student,
    arrival_threshold: float,
    anchor_threshold: float,
) -> list[RunArrival]:
    """Find each run's arrival in a stretch of recorded distance readings.

    Applies exactly the filters the live path applies: the closest approach
    inside a run's window is that run's arrival, provided it actually reached
    the stop. The first reading inside the anchor gives the final leg.
    """
    best: dict[tuple[date, str], tuple[float, datetime]] = {}
    anchor: dict[tuple[date, str], datetime] = {}

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
            window = run_window(scheduled, when)
            if window is None or not window[0] <= local <= window[1]:
                continue
            key = (local.date(), run)
            current = best.get(key)
            if current is None or distance < current[0]:
                best[key] = (distance, when)
            if distance <= anchor_threshold and key not in anchor:
                anchor[key] = when

    arrivals: list[RunArrival] = []
    for (day, run), (closest, when) in best.items():
        if closest > arrival_threshold:
            continue
        crossed = anchor.get((day, run))
        approach = (
            int((when - crossed).total_seconds())
            if crossed is not None and crossed <= when
            else None
        )
        arrivals.append(
            RunArrival(
                run=run, arrival=when, closest=closest, approach_seconds=approach
            )
        )
    return sorted(arrivals, key=lambda item: item.arrival)


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
        # (child_id, run, local date) -> closest approach seen so far.
        self._pending: dict[tuple[int, str, date], tuple[float, datetime]] = {}
        # (child_id, run, local date) -> when the bus first came inside the
        # approach anchor for that run.
        self._anchor: dict[tuple[int, str, date], datetime] = {}

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
            self._observe(child_id, student, info.get("dist"))

        if self._promote_pending():
            await self._async_save_arrivals()

        return results

    # ------------------------------------------------------------------
    # Arrival observation
    # ------------------------------------------------------------------

    @property
    def _anchor_threshold(self) -> float:
        """Return the approach anchor distance in the account's units."""
        return APPROACH_ANCHOR_KM if self.distance_in_km else APPROACH_ANCHOR_MILES

    @property
    def _arrival_threshold(self) -> float:
        """Return how close counts as an arrival, in the account's units."""
        return ARRIVAL_THRESHOLD_KM if self.distance_in_km else ARRIVAL_THRESHOLD_MILES

    def _observe(self, child_id: int, student: Student, dist: Any) -> None:
        """Track the closest approach inside whichever run window is open."""
        if dist is None:
            return
        try:
            distance = float(dist)
        except (TypeError, ValueError):
            return

        now = dt_util.utcnow()
        for run, scheduled in (
            (RUN_AM, student.am_scheduled),
            (RUN_PM, student.pm_scheduled),
        ):
            window = run_window(scheduled, now)
            if window is None or not window[0] <= dt_util.as_local(now) <= window[1]:
                continue
            key = (child_id, run, dt_util.as_local(now).date())
            best = self._pending.get(key)
            if best is None or distance < best[0]:
                self._pending[key] = (distance, now)
            if distance <= self._anchor_threshold and key not in self._anchor:
                self._anchor[key] = now

    def _promote_pending(self) -> bool:
        """Turn closed windows into arrivals. Returns True if anything changed."""
        now = dt_util.utcnow()
        local_now = dt_util.as_local(now)
        changed = False

        for key in list(self._pending):
            child_id, run, day = key
            student = (self.students.data or {}).get(child_id)
            if student is None:
                del self._pending[key]
                continue

            scheduled = student.am_scheduled if run == RUN_AM else student.pm_scheduled
            window = run_window(scheduled, now)
            still_open = (
                window is not None
                and day == local_now.date()
                and local_now <= window[1]
            )
            if still_open:
                continue

            closest, when = self._pending.pop(key)
            if closest > self._arrival_threshold:
                self._anchor.pop(key, None)
            # A run where the bus never really came — nobody to collect, or a
            # cancelled route — must not be learned as an arrival time.
            if closest > self._arrival_threshold:
                _LOGGER.debug(
                    "Ignoring %s run on %s: closest approach was %.1f",
                    run,
                    day,
                    closest,
                )
                continue

            crossed = self._anchor.pop(key, None)
            approach = (
                int((when - crossed).total_seconds())
                if crossed is not None and crossed <= when
                else None
            )
            history = self._arrivals.setdefault(child_id, [])
            history.append(
                RunArrival(
                    run=run, arrival=when, closest=closest, approach_seconds=approach
                )
            )
            history.sort(key=lambda item: item.arrival)
            self._arrivals[child_id] = _trim_per_run(history)
            changed = True

        return changed

    async def async_backfill_arrivals(self) -> None:
        """Recover past arrivals from the recorder, once.

        Skipped as soon as any run has a recorded final leg, so this costs one
        history query on the first start after upgrading and nothing after.
        """
        if any(
            item.approach_seconds is not None
            for arrivals in self._arrivals.values()
            for item in arrivals
        ):
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
            arrivals = reconstruct_arrivals(
                rows, student, self._arrival_threshold, self._anchor_threshold
            )
            if arrivals:
                recovered[child_id] = arrivals
                _LOGGER.info(
                    "Recovered %d past arrival(s) for %s from the recorder",
                    len(arrivals),
                    entity_id,
                )

        changed = False
        for child_id, arrivals in recovered.items():
            known = {item.arrival for item in self._arrivals.get(child_id, [])}
            fresh = [item for item in arrivals if item.arrival not in known]
            if not fresh:
                continue
            history = self._arrivals.setdefault(child_id, [])
            history.extend(fresh)
            history.sort(key=lambda item: item.arrival)
            self._arrivals[child_id] = _trim_per_run(history)
            changed = True

        if changed:
            await self._async_save_arrivals()

    async def async_load_arrivals(self) -> None:
        """Restore observed arrivals saved by a previous run."""
        stored = await self._store.async_load() or {}
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
                    approach_seconds=item.get("approach"),
                )
                for item in arrivals
                if item.get("run") in (RUN_AM, RUN_PM)
                and (parsed := dt_util.parse_datetime(item.get("at", ""))) is not None
            ]
            if restored:
                self._arrivals[child_id] = restored

    async def _async_save_arrivals(self) -> None:
        """Persist observed arrivals."""
        await self._store.async_save(
            {
                str(child_id): [
                    {
                        "run": item.run,
                        "at": item.arrival.isoformat(),
                        "closest": item.closest,
                        "approach": item.approach_seconds,
                    }
                    for item in arrivals
                ]
                for child_id, arrivals in self._arrivals.items()
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
                candidates.append(
                    ArrivalPrediction(
                        run=run,
                        arrival=dt_util.as_utc(anchored),
                        source=SOURCE_LEARNED,
                        basis=BASIS_APPROACH,
                        samples=samples,
                        spread=spread,
                        outliers=outliers,
                        scheduled=scheduled,
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
                        )
                    )
                    break

        if not candidates:
            return None
        return min(candidates, key=lambda item: item.arrival)

    def _anchored_arrival(
        self, child_id: int, run: str, local_now: datetime
    ) -> datetime | None:
        """Estimate arrival from the live approach, or None if not applicable.

        Applies only while this run's bus has already crossed the approach
        anchor today and has not yet arrived.  Returns the crossing time plus
        the typical final leg, so an early bus is reported early instead of
        being averaged back towards the usual clock time.
        """
        crossed = self._anchor.get((child_id, run, local_now.date()))
        if crossed is None:
            return None

        legs = sorted(
            item.approach_seconds
            for item in self._arrivals.get(child_id, [])
            if item.run == run and item.approach_seconds is not None
        )
        if not legs:
            return None

        estimate = dt_util.as_local(crossed) + timedelta(seconds=legs[len(legs) // 2])
        # A bus already overdue against this estimate has arrived, or is about
        # to; leave it be rather than reporting a time in the past.
        return estimate if estimate > local_now else None

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
            if item.run == run
        )
        if not times:
            return None, 0, None, 0

        kept, excluded = _reject_outliers(times)
        middle = _median(kept)
        spread = kept[-1] - kept[0]
        return time(hour=middle // 60, minute=middle % 60), len(kept), spread, excluded
