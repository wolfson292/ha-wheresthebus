"""Tests for setting up the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wheresthebus.api import (
    WheresTheBusAuthError,
    WheresTheBusError,
)
from custom_components.wheresthebus.const import DOMAIN

from .fixtures import RIDER_INFO, STUDENT_SCANS


def freeze_time_local(*parts: int):
    """Freeze the clock at a local wall-clock time in Home Assistant's zone."""
    return freeze_time(datetime(*parts, tzinfo=dt_util.get_default_time_zone()))


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    """Add and set up the config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """The entry loads and produces a device with the expected entities."""
    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "12345678")})
    assert device is not None
    assert device.name == "Robin Alex Rivera"

    tracker = hass.states.get("device_tracker.robin_alex_rivera_bus")
    assert tracker is not None
    assert tracker.attributes["latitude"] == 40.73100
    assert tracker.attributes["longitude"] == -73.99500
    assert tracker.attributes["source_type"] == "gps"
    assert tracker.attributes["bus_number"] == "1234"
    assert tracker.attributes["stop_address"] == "MAPLE RD & 3RD ST"


async def test_bus_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Live-position sensors report distance, status and ETA."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_entry(hass, mock_config_entry)

    distance = hass.states.get("sensor.robin_alex_rivera_distance_to_stop")
    assert distance is not None
    # isDistKm is 0 for this account, so the API reports miles, and the
    # distance device class lets Home Assistant present them in the user's
    # own unit system.
    assert distance.state == "3.2"
    assert distance.attributes["unit_of_measurement"] == "mi"

    status = hass.states.get("sensor.robin_alex_rivera_bus_status")
    assert status is not None
    assert status.state == "current"

    # etaMsg is an empty string in the payload and must not become "".
    eta = hass.states.get("sensor.robin_alex_rivera_eta")
    assert eta is not None
    assert eta.state == "unknown"


async def test_distance_converts_for_a_metric_household(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A metric user sees the same miles payload converted to kilometres."""
    await setup_entry(hass, mock_config_entry)

    distance = hass.states.get("sensor.robin_alex_rivera_distance_to_stop")
    assert distance is not None
    assert distance.attributes["unit_of_measurement"] == "km"
    assert float(distance.state) == pytest.approx(5.149, abs=0.01)


async def test_scan_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Scan sensors expose the last scan, pickup and drop-off."""
    await setup_entry(hass, mock_config_entry)

    last_scan = hass.states.get("sensor.robin_alex_rivera_last_scan")
    assert last_scan is not None
    assert last_scan.state == "2026-08-26T20:19:59+00:00"
    assert last_scan.attributes["scan_location"] == "Riverside Middle School"
    assert last_scan.attributes["scan_method"] == "Keypad"

    pickup = hass.states.get("sensor.robin_alex_rivera_last_pickup")
    assert pickup is not None
    assert pickup.state == "2026-08-26T20:19:59+00:00"

    dropoff = hass.states.get("sensor.robin_alex_rivera_last_drop_off")
    assert dropoff is not None
    assert dropoff.state == "2026-08-26T13:30:03+00:00"
    assert dropoff.attributes["scan_location"] == "Riverside Middle School"


async def test_diagnostic_sensors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Scheduled stop times and the bus number are exposed as diagnostics."""
    await setup_entry(hass, mock_config_entry)

    assert hass.states.get("sensor.robin_alex_rivera_morning_stop_time").state == (
        "7:56 A.M."
    )
    assert hass.states.get("sensor.robin_alex_rivera_afternoon_stop_time").state == (
        "5:48 P.M."
    )
    assert hass.states.get("sensor.robin_alex_rivera_bus_number").state == "1234"


async def test_setup_retries_on_connection_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A network failure at login leaves the entry in a retrying state."""
    mock_api.async_login.side_effect = WheresTheBusError("boom")

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_starts_reauth_on_bad_credentials(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Rejected credentials put the entry into the reauth state."""
    mock_api.async_login.side_effect = WheresTheBusAuthError("nope")

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """The entry unloads cleanly."""
    await setup_entry(hass, mock_config_entry)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_gps_age_sensor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """The GPS age is exposed as minutes rather than as prose."""
    await setup_entry(hass, mock_config_entry)

    status = hass.states.get("sensor.robin_alex_rivera_bus_status")
    assert status is not None
    assert status.state == "current"
    assert status.attributes["raw_status"] == "current"
    assert status.attributes["options"] == ["current", "stale", "inactive"]

    age = hass.states.get("sensor.robin_alex_rivera_gps_age")
    assert age is not None
    assert age.state == "0"
    assert age.attributes["unit_of_measurement"] == "min"


async def test_stale_gps_does_not_churn_the_status(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Minute-by-minute GPS ageing moves the age, not the status."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    seen_status: set[str] = set()
    for minute in (1, 2, 7, 14):
        mock_api.async_get_rider_info.return_value = {
            **RIDER_INFO,
            "stsMsg": f"{minute} min. ago",
        }
        await buses.async_refresh()
        await hass.async_block_till_done()

        seen_status.add(hass.states.get("sensor.robin_alex_rivera_bus_status").state)
        assert hass.states.get("sensor.robin_alex_rivera_gps_age").state == str(minute)

    # Four different API strings collapsed to a single sensor state.
    assert seen_status == {"stale"}


async def test_unrecognised_status_keeps_the_raw_string(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Wording the parser does not know shows unknown but is not discarded."""
    await setup_entry(hass, mock_config_entry)

    mock_api.async_get_rider_info.return_value = {
        **RIDER_INFO,
        "stsMsg": "awaiting first fix",
    }
    await mock_config_entry.runtime_data.buses.async_refresh()
    await hass.async_block_till_done()

    status = hass.states.get("sensor.robin_alex_rivera_bus_status")
    assert status.state == "unknown"
    assert status.attributes["raw_status"] == "awaiting first fix"


async def test_scans_survive_the_midnight_reset(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """An empty scan feed must not blank yesterday's pickup and drop-off."""
    await setup_entry(hass, mock_config_entry)

    pickup = hass.states.get("sensor.robin_alex_rivera_last_pickup").state
    dropoff = hass.states.get("sensor.robin_alex_rivera_last_drop_off").state
    assert pickup == "2026-08-26T20:19:59+00:00"
    assert dropoff == "2026-08-26T13:30:03+00:00"

    # After midnight the API reports the new day, which has no scans yet.
    mock_api.async_get_student_scans.return_value = {
        "studentDetails": [{"studentName": "Robin Rivera", "studentScans": []}],
        "studentInfo": STUDENT_SCANS["studentInfo"],
    }
    await mock_config_entry.runtime_data.students.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.robin_alex_rivera_last_pickup").state == pickup
    assert hass.states.get("sensor.robin_alex_rivera_last_drop_off").state == dropoff


async def test_new_scans_merge_with_retained_history(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Today's pickup lands without losing yesterday's drop-off."""
    await setup_entry(hass, mock_config_entry)

    # A new day: one morning pickup at the neighbourhood stop, nothing else.
    mock_api.async_get_student_scans.return_value = {
        "studentDetails": [
            {
                "studentName": "Robin Rivera",
                "studentScans": [
                    {
                        "scanTime": 1787832325,
                        "scanLocation": "Maple Rd, Springfield",
                        "scanMethod": "Keypad",
                        "bus": "1234",
                    }
                ],
            }
        ],
        "studentInfo": STUDENT_SCANS["studentInfo"],
    }
    await mock_config_entry.runtime_data.students.async_refresh()
    await hass.async_block_till_done()

    # The new scan becomes the pickup...
    assert (
        hass.states.get("sensor.robin_alex_rivera_last_pickup").state
        == "2026-08-27T12:05:25+00:00"
    )
    # ...while the previous day's drop-off is still there.
    assert (
        hass.states.get("sensor.robin_alex_rivera_last_drop_off").state
        == "2026-08-26T13:30:03+00:00"
    )


async def test_scan_history_is_restored_after_a_restart(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A restart before the first scan of the day keeps the previous values."""
    await setup_entry(hass, mock_config_entry)
    pickup = hass.states.get("sensor.robin_alex_rivera_last_pickup").state

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Come back up on a fresh day whose scan feed is still empty.
    mock_api.async_get_student_scans.return_value = {
        "studentDetails": [{"studentName": "Robin Rivera", "studentScans": []}],
        "studentInfo": STUDENT_SCANS["studentInfo"],
    }
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.robin_alex_rivera_last_pickup").state == pickup


async def test_next_arrival_falls_back_to_the_schedule(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """With no observed arrivals yet, the prediction is the scheduled time."""
    await setup_entry(hass, mock_config_entry)

    arrival = hass.states.get("sensor.robin_alex_rivera_next_arrival")
    assert arrival is not None
    assert arrival.attributes["prediction_source"] == "scheduled"
    assert arrival.attributes["samples"] == 0
    assert arrival.attributes["run"] in ("am", "pm")

    # Whichever run is next, the predicted clock time is its scheduled one.
    predicted = dt_util.as_local(dt_util.parse_datetime(arrival.state))
    assert predicted.strftime("%H:%M") == arrival.attributes["scheduled"]
    assert predicted > dt_util.as_local(dt_util.utcnow())


async def test_arrival_is_learned_from_a_close_pass(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A pass that reaches the stop inside the window becomes a sample."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # 08:02 local, inside the 07:26-08:26 pickup window, right at the stop.
    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    # Once the window has closed the observation is promoted to an arrival.
    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    # Ask before the next morning window, so the AM run is the next arrival.
    with freeze_time_local(2026, 9, 1, 6, 0):
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.run == "am"
    assert prediction.source == "learned"
    assert prediction.samples == 1
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "08:02"


async def test_a_distant_pass_is_not_learned_as_an_arrival(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A run where the bus stayed half a mile out never reached the stop."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.5}
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 9, 1, 6, 0):
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.run == "am"
    assert prediction.source == "scheduled"
    assert prediction.samples == 0
    # Falls back to the timetable, not to the pass that never reached the stop.
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "07:56"


async def test_the_early_decoy_pass_is_not_learned(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Touching the stop at 06:13 on another route must not become a sample."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    with freeze_time_local(2026, 8, 31, 6, 13):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 9, 1, 6, 0):
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.run == "am"
    assert prediction.source == "scheduled"
    assert prediction.samples == 0
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "07:56"


async def test_setup_survives_a_scan_store_written_by_an_older_version(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """An existing scan store must not block setup.

    The scan store's shape has never changed, so it stays at version 1. Home
    Assistant raises NotImplementedError for a version mismatch with no
    migration function, which would take the whole entry down on upgrade.
    """
    mock_config_entry.add_to_hass(hass)
    hass_storage[f"wheresthebus_scans.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"wheresthebus_scans.{mock_config_entry.entry_id}",
        "data": {
            "12345678": [
                {
                    "t": "2026-08-26T20:19:59+00:00",
                    "l": "Maple Rd, Springfield",
                    "m": "Keypad",
                    "b": "1234",
                }
            ]
        },
    }

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    # The stored scan is still there, merged with whatever the API returned.
    assert hass.states.get("sensor.robin_alex_rivera_last_pickup") is not None
