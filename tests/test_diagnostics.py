"""Tests for WheresTheBus diagnostics."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wheresthebus.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redact_personal_data(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Diagnostics include the live data but hide identifying details."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["shard_id"] == "sh_05"
    assert result["distance_in_km"] is False

    student = result["students"]["12345678"]
    assert student["bus_number"] == "1234"
    assert student["school_name"] == "Riverside Middle School"
    assert len(student["scans"]) == 3

    # Credentials, names, addresses and home/school coordinates are hidden.
    assert result["entry"]["data"]["password"] == REDACTED
    assert result["entry"]["data"]["email"] == REDACTED
    assert student["name"] == REDACTED
    assert student["student_id"] == REDACTED
    assert student["stop_address"] == REDACTED
    assert student["stop_latitude"] == REDACTED
    assert student["scans"][0]["location"] == REDACTED

    bus = result["buses"]["12345678"]
    assert bus["homLat"] == REDACTED
    assert bus["schLat"] == REDACTED
    assert bus["stpLat"] == REDACTED
    # The bus's own position is the point of the integration, so it stays.
    assert bus["busLat"] == 40.73100


async def test_diagnostics_expose_the_learned_ladder(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Which rungs carry data must be visible without reading storage.

    An estimate that refuses to re-anchor looks identical from the outside to
    one that has no reason to, and telling them apart previously meant waiting
    for the sensor to roll over to the other run.
    """
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    arrivals = result["arrivals"]
    assert arrivals["ladder"] == [3.0, 2.0, 1.0, 0.5]
    assert "schema" in arrivals
    assert "riders" in arrivals
    assert "crossed_today" in arrivals
