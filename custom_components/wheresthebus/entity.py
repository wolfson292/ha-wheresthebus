"""Shared entity base for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    Student,
    WheresTheBusBusCoordinator,
    WheresTheBusStudentCoordinator,
)


class WheresTheBusEntity(CoordinatorEntity):
    """Base entity tied to one rider's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WheresTheBusStudentCoordinator | WheresTheBusBusCoordinator,
        students: WheresTheBusStudentCoordinator,
        child_id: int,
        key: str,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._students = students
        self._child_id = child_id
        self._attr_unique_id = f"{child_id}_{key}"

        student = students.data[child_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(child_id))},
            manufacturer="WheresTheBus",
            name=student.name,
            model=f"Bus {student.bus_number}" if student.bus_number else "Rider",
            configuration_url="https://wheresthebus.com/",
        )

    @property
    def student(self) -> Student | None:
        """Return the current roster record for this rider."""
        return (self._students.data or {}).get(self._child_id)

    @property
    def available(self) -> bool:
        """Return whether the rider is still present on the account."""
        return super().available and self.student is not None
