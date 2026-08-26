"""Diagnostics support for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import WheresTheBusConfigEntry
from .const import CONF_DEVICE_ID

TO_REDACT = {
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    "full_name",
    "homLat",
    "homLon",
    # ``location`` is the ScanEvent field; ``scanLocation`` is the raw API key.
    "location",
    "name",
    "riderName",
    "scanLocation",
    "schLat",
    "schLon",
    "stop_address",
    "stpLat",
    "stpLon",
    "stop_latitude",
    "stop_longitude",
    "student_id",
    "studentId",
    "studentName",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WheresTheBusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    students = {
        str(child_id): asdict(student)
        for child_id, student in (data.students.data or {}).items()
    }
    buses = {str(child_id): info for child_id, info in (data.buses.data or {}).items()}

    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "shard_id": data.api.shard_id,
            "distance_in_km": data.buses.distance_in_km,
            "students": students,
            "buses": buses,
        },
        TO_REDACT,
    )
