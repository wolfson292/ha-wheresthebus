"""Sensors for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import WheresTheBusConfigEntry
from .const import (
    ATTR_BUS_NUMBER,
    ATTR_OUTLIERS_EXCLUDED,
    ATTR_PREDICTION_BASIS,
    ATTR_PREDICTION_SOURCE,
    ATTR_RAW_STATUS,
    ATTR_RIDE_MINUTES,
    ATTR_RUN,
    ATTR_SAMPLES,
    ATTR_SCAN_LOCATION,
    ATTR_SCAN_METHOD,
    ATTR_SCHEDULED,
    ATTR_SCHOOL_NAME,
    ATTR_SPREAD_MINUTES,
    ATTR_STOP_ADDRESS,
    ATTR_STUDENT_ID,
    ATTR_SUBSTITUTE_BUS,
    BUS_STATUS_OPTIONS,
    SCAN_DROPOFF,
    SCAN_PICKUP,
)
from .coordinator import (
    ScanEvent,
    Student,
    WheresTheBusBusCoordinator,
    WheresTheBusStudentCoordinator,
    parse_bus_status,
    predict_school_arrival,
)
from .entity import WheresTheBusEntity


@dataclass(frozen=True, kw_only=True)
class WheresTheBusBusSensorDescription(SensorEntityDescription):
    """Describes a sensor fed by the live-position coordinator."""

    value_fn: Callable[[dict[str, Any]], Any]
    unit_fn: Callable[[bool], str | None] | None = None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class WheresTheBusStudentSensorDescription(SensorEntityDescription):
    """Describes a sensor fed by the roster coordinator."""

    value_fn: Callable[[Student], Any]
    scan_fn: Callable[[Student], ScanEvent | None] | None = None


def _blank_to_none(value: Any) -> Any:
    """Return None for the API's empty-string placeholders."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


BUS_SENSORS: tuple[WheresTheBusBusSensorDescription, ...] = (
    WheresTheBusBusSensorDescription(
        key="distance_to_stop",
        translation_key="distance_to_stop",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:map-marker-distance",
        value_fn=lambda info: info.get("dist"),
        unit_fn=lambda in_km: UnitOfLength.KILOMETERS if in_km else UnitOfLength.MILES,
    ),
    WheresTheBusBusSensorDescription(
        key="bus_status",
        translation_key="bus_status",
        device_class=SensorDeviceClass.ENUM,
        options=BUS_STATUS_OPTIONS,
        icon="mdi:bus-alert",
        value_fn=lambda info: parse_bus_status(info.get("stsMsg"))[0],
        # The unabridged string is kept so nothing is lost when the API uses
        # wording the parser does not recognise.
        attrs_fn=lambda info: {ATTR_RAW_STATUS: _blank_to_none(info.get("stsMsg"))},
    ),
    WheresTheBusBusSensorDescription(
        key="gps_age",
        translation_key="gps_age",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:crosshairs-gps",
        value_fn=lambda info: parse_bus_status(info.get("stsMsg"))[1],
    ),
    WheresTheBusBusSensorDescription(
        key="eta",
        translation_key="eta",
        icon="mdi:clock-fast",
        value_fn=lambda info: _blank_to_none(info.get("etaMsg")),
    ),
)

STUDENT_SENSORS: tuple[WheresTheBusStudentSensorDescription, ...] = (
    WheresTheBusStudentSensorDescription(
        key="last_scan",
        translation_key="last_scan",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:card-account-details-outline",
        value_fn=lambda student: _scan_time(student.last_scan),
        scan_fn=lambda student: student.last_scan,
    ),
    WheresTheBusStudentSensorDescription(
        key="last_pickup",
        translation_key="last_pickup",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:bus-marker",
        value_fn=lambda student: _scan_time(student.last_scan_of(SCAN_PICKUP)),
        scan_fn=lambda student: student.last_scan_of(SCAN_PICKUP),
    ),
    WheresTheBusStudentSensorDescription(
        key="last_dropoff",
        translation_key="last_dropoff",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:bus-stop",
        value_fn=lambda student: _scan_time(student.last_scan_of(SCAN_DROPOFF)),
        scan_fn=lambda student: student.last_scan_of(SCAN_DROPOFF),
    ),
    WheresTheBusStudentSensorDescription(
        key="am_stop_time",
        translation_key="am_stop_time",
        icon="mdi:weather-sunset-up",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda student: _blank_to_none(student.am_stop_time),
    ),
    WheresTheBusStudentSensorDescription(
        key="pm_stop_time",
        translation_key="pm_stop_time",
        icon="mdi:weather-sunset-down",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda student: _blank_to_none(student.pm_stop_time),
    ),
    WheresTheBusStudentSensorDescription(
        key="bus_number",
        translation_key="bus_number",
        icon="mdi:bus",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda student: _blank_to_none(student.bus_number),
    ),
)


def _scan_time(scan: ScanEvent | None) -> datetime | None:
    """Return the timestamp of a scan, if there is one."""
    return scan.timestamp if scan else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WheresTheBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the WheresTheBus sensors."""
    data = entry.runtime_data
    entities: list[SensorEntity] = []

    for child_id in data.students.data:
        entities.append(
            WheresTheBusNextArrivalSensor(data.buses, data.students, child_id)
        )
        entities.extend(
            WheresTheBusBusSensor(data.buses, data.students, child_id, description)
            for description in BUS_SENSORS
        )
        entities.append(WheresTheBusSchoolArrivalSensor(data.students, child_id))
        entities.extend(
            WheresTheBusStudentSensor(data.students, child_id, description)
            for description in STUDENT_SENSORS
        )

    async_add_entities(entities)


class WheresTheBusBusSensor(WheresTheBusEntity, SensorEntity):
    """A sensor derived from the live bus position feed."""

    coordinator: WheresTheBusBusCoordinator
    entity_description: WheresTheBusBusSensorDescription

    def __init__(
        self,
        coordinator: WheresTheBusBusCoordinator,
        students: WheresTheBusStudentCoordinator,
        child_id: int,
        description: WheresTheBusBusSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, students, child_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        info = (self.coordinator.data or {}).get(self._child_id) or {}
        return self.entity_description.value_fn(info)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, which follows the account's miles/km preference."""
        if self.entity_description.unit_fn is None:
            return self.entity_description.native_unit_of_measurement
        return self.entity_description.unit_fn(self.coordinator.distance_in_km)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return any extra context this sensor publishes."""
        if self.entity_description.attrs_fn is None:
            return None
        info = (self.coordinator.data or {}).get(self._child_id) or {}
        return self.entity_description.attrs_fn(info)


class WheresTheBusNextArrivalSensor(WheresTheBusEntity, SensorEntity):
    """When the bus is next expected at this rider's stop.

    A timestamp rather than a minutes-remaining number: a countdown would
    rewrite itself on every poll, and Home Assistant renders a timestamp as
    relative time anyway.  It also makes the alerting automations plain time
    triggers with a negative offset, with no templates involved.
    """

    coordinator: WheresTheBusBusCoordinator
    _attr_translation_key = "next_arrival"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"

    def __init__(
        self,
        coordinator: WheresTheBusBusCoordinator,
        students: WheresTheBusStudentCoordinator,
        child_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, students, child_id, "next_arrival")

    @property
    def native_value(self) -> datetime | None:
        """Return the predicted arrival."""
        prediction = self.coordinator.predict_next_arrival(self._child_id)
        return prediction.arrival if prediction else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return which run this is, and how well informed the guess is."""
        prediction = self.coordinator.predict_next_arrival(self._child_id)
        if prediction is None:
            return None
        return {
            ATTR_RUN: prediction.run,
            ATTR_PREDICTION_SOURCE: prediction.source,
            ATTR_PREDICTION_BASIS: prediction.basis,
            ATTR_SAMPLES: prediction.samples,
            ATTR_SPREAD_MINUTES: prediction.spread,
            ATTR_OUTLIERS_EXCLUDED: prediction.outliers,
            ATTR_SCHEDULED: prediction.scheduled.strftime("%H:%M"),
        }


class WheresTheBusSchoolArrivalSensor(WheresTheBusEntity, SensorEntity):
    """When the morning ride is expected to reach school.

    Learned from the drop-off scans the school records, so the ride to school
    gets a real destination time rather than only a stopwatch since boarding.
    """

    coordinator: WheresTheBusStudentCoordinator
    _attr_translation_key = "school_arrival"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:school-outline"

    def __init__(
        self,
        coordinator: WheresTheBusStudentCoordinator,
        child_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, coordinator, child_id, "school_arrival")

    def _predict(self) -> Any:
        """Return the prediction, or None when nothing has been observed."""
        student = self.student
        if student is None:
            return None
        return predict_school_arrival(student, dt_util.as_local(dt_util.utcnow()))

    @property
    def native_value(self) -> datetime | None:
        """Return the predicted arrival at school."""
        prediction = self._predict()
        return prediction.arrival if prediction else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return how well informed the guess is, and the typical ride."""
        prediction = self._predict()
        if prediction is None:
            return None
        return {
            ATTR_SAMPLES: prediction.samples,
            ATTR_RIDE_MINUTES: prediction.ride_minutes,
        }


class WheresTheBusStudentSensor(WheresTheBusEntity, SensorEntity):
    """A sensor derived from the roster and ID scan history."""

    coordinator: WheresTheBusStudentCoordinator
    entity_description: WheresTheBusStudentSensorDescription

    def __init__(
        self,
        coordinator: WheresTheBusStudentCoordinator,
        child_id: int,
        description: WheresTheBusStudentSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, coordinator, child_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        student = self.student
        if student is None:
            return None
        return self.entity_description.value_fn(student)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return context for the scan that produced this value."""
        student = self.student
        if student is None:
            return None

        if self.entity_description.scan_fn is None:
            return {
                ATTR_STUDENT_ID: student.student_id,
                ATTR_SCHOOL_NAME: student.school_name,
                ATTR_SUBSTITUTE_BUS: student.substitute_bus,
            }

        scan = self.entity_description.scan_fn(student)
        if scan is None:
            return None
        return {
            ATTR_SCAN_LOCATION: _blank_to_none(scan.location),
            ATTR_SCAN_METHOD: _blank_to_none(scan.method),
            ATTR_BUS_NUMBER: _blank_to_none(scan.bus),
            ATTR_STOP_ADDRESS: student.stop_address,
            ATTR_SCHOOL_NAME: student.school_name,
        }
