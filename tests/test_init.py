"""Tests for setting up the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wheresthebus.api import (
    WheresTheBusAuthError,
    WheresTheBusError,
)
from custom_components.wheresthebus.const import DOMAIN


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
