"""Data coordinators for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WheresTheBusApi, WheresTheBusAuthError, WheresTheBusError
from .const import DOMAIN, SCAN_DROPOFF, SCAN_PICKUP

_LOGGER = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Local hour that separates the morning run from the afternoon run.
_NOON = 12


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
        return students


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
        stop_address=rider.get("amStopAddress") or rider.get("pmStopAddress"),
        stop_latitude=stop_latitude or None,
        stop_longitude=stop_longitude or None,
    )


def _attach_scans(students: dict[int, Student], payload: dict[str, Any]) -> None:
    """Match ``getStudentScan`` results onto the roster by rider name.

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
        student.scans = classify_scans(events, student.school_name)


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

        return results
