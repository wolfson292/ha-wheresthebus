"""Device tracker for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WheresTheBusConfigEntry
from .const import (
    ATTR_BUS_NUMBER,
    ATTR_ROUTE_NUMBER,
    ATTR_STATUS_COLOR,
    ATTR_STOP_ADDRESS,
    ATTR_STOP_LATITUDE,
    ATTR_STOP_LONGITUDE,
)
from .coordinator import WheresTheBusBusCoordinator, WheresTheBusStudentCoordinator
from .entity import WheresTheBusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WheresTheBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one bus tracker per rider."""
    data = entry.runtime_data
    async_add_entities(
        WheresTheBusTracker(data.buses, data.students, child_id)
        for child_id in data.students.data
    )


class WheresTheBusTracker(WheresTheBusEntity, TrackerEntity):
    """Reports the live GPS position of the rider's bus."""

    coordinator: WheresTheBusBusCoordinator
    _attr_translation_key = "bus"
    _attr_icon = "mdi:bus-school"

    def __init__(
        self,
        coordinator: WheresTheBusBusCoordinator,
        students: WheresTheBusStudentCoordinator,
        child_id: int,
    ) -> None:
        """Initialise the tracker."""
        super().__init__(coordinator, students, child_id, "bus")

    @property
    def _info(self) -> dict[str, Any]:
        """Return this rider's latest position payload."""
        return (self.coordinator.data or {}).get(self._child_id) or {}

    @property
    def source_type(self) -> SourceType:
        """Return the source of the location."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the bus latitude."""
        return _coordinate(self._info.get("busLat"))

    @property
    def longitude(self) -> float | None:
        """Return the bus longitude."""
        return _coordinate(self._info.get("busLon"))

    @property
    def available(self) -> bool:
        """Return whether a position has been reported."""
        return super().available and self.latitude is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return stop and status context alongside the position."""
        student = self.student
        info = self._info
        return {
            ATTR_BUS_NUMBER: student.bus_number if student else None,
            ATTR_ROUTE_NUMBER: student.route_number if student else None,
            ATTR_STOP_ADDRESS: student.stop_address if student else None,
            ATTR_STOP_LATITUDE: _coordinate(info.get("stpLat")),
            ATTR_STOP_LONGITUDE: _coordinate(info.get("stpLon")),
            ATTR_STATUS_COLOR: info.get("stsClr") or None,
        }


def _coordinate(value: Any) -> float | None:
    """Return a usable coordinate, treating the API's 0.0 as 'unknown'."""
    if value in (None, 0, 0.0):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
