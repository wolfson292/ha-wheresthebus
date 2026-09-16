"""Tests for setting up the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import date, datetime
from math import degrees
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wheresthebus.api import (
    WheresTheBusAuthError,
    WheresTheBusError,
)
from custom_components.wheresthebus.const import ARRIVAL_SCHEMA, DOMAIN

from .fixtures import RIDER_INFO, STUDENT_SCANS, USER_INFO


def freeze_time_local(*parts: int):
    """Freeze the clock at a local wall-clock time in Home Assistant's zone."""
    return freeze_time(datetime(*parts, tzinfo=dt_util.get_default_time_zone()))


def _at_distance(miles: float) -> dict:
    """Return a rider payload with the bus that far from the stop.

    The estimate is built on WHERE the bus is, not how far, so a fixture that
    moved `dist` while leaving the coordinates fixed would describe a bus
    teleporting in place. This puts it due north of the stop at the asked-for
    range, which is what the route match reads.
    """
    return {
        **RIDER_INFO,
        "dist": miles,
        "busLat": RIDER_INFO["stpLat"] + degrees(miles / 3958.7613),
        "busLon": RIDER_INFO["stpLon"],
    }


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
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "12345678"), mock_config_entry.entry_id
    )
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
    """Live-position sensors report distance and status."""
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

    # etaMsg came back empty on every response ever observed, so the sensor
    # that mapped it is gone rather than permanently unknown.
    assert hass.states.get("sensor.robin_alex_rivera_eta") is None


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


async def test_gps_fix_sensor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """The GPS freshness is a status plus the instant of the fix."""
    await setup_entry(hass, mock_config_entry)

    status = hass.states.get("sensor.robin_alex_rivera_bus_status")
    assert status is not None
    assert status.state == "current"
    assert status.attributes["raw_status"] == "current"
    assert status.attributes["options"] == ["current", "stale", "inactive"]

    # The age in minutes wrote a recorder row a minute; the instant replaced it.
    assert hass.states.get("sensor.robin_alex_rivera_gps_age") is None
    fix = hass.states.get("sensor.robin_alex_rivera_last_gps_fix")
    assert fix is not None
    assert fix.attributes["device_class"] == "timestamp"


async def test_stale_gps_does_not_churn_the_status_or_the_fix(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A bus going quiet must not write a new state every minute.

    While the same fix is being reported the age climbs and the clock climbs
    with it, so the instant of the fix does not move. The old age sensor
    changed on every one of these — 720 recorder rows in three days on a live
    install, for a diagnostic nobody reads.
    """
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    seen_status: set[str] = set()
    seen_fix: set[str] = set()
    # The clock advances in step with the age: one fix, reported four times.
    for minute in (1, 2, 7, 14):
        with freeze_time_local(2026, 9, 11, 8, minute):
            mock_api.async_get_rider_info.return_value = {
                **RIDER_INFO,
                "stsMsg": f"{minute} min. ago",
            }
            await buses.async_refresh()
            await hass.async_block_till_done()

        seen_status.add(hass.states.get("sensor.robin_alex_rivera_bus_status").state)
        seen_fix.add(hass.states.get("sensor.robin_alex_rivera_last_gps_fix").state)

    assert seen_status == {"stale"}
    assert len(seen_fix) == 1

    # A genuinely new fix does move it.
    with freeze_time_local(2026, 9, 11, 8, 15):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "stsMsg": "current"}
        await buses.async_refresh()
        await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.robin_alex_rivera_last_gps_fix").state not in seen_fix
    )


async def test_a_dark_bus_reports_no_fix(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Once the bus goes inactive the last known fix stops being a claim."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "stsMsg": "inactive"}
    await buses.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.robin_alex_rivera_bus_status").state == "inactive"
    assert hass.states.get("sensor.robin_alex_rivera_last_gps_fix").state == "unknown"


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
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    # Once the window has closed the observation is promoted to an arrival.
    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
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
        mock_api.async_get_rider_info.return_value = _at_distance(0.5)
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
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
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
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


async def _record_morning_arrival(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    *,
    day: int,
    crossed: tuple[int, int],
    arrived: tuple[int, int],
) -> None:
    """Drive one morning: cross the anchor, reach the stop, close the window."""
    buses = mock_config_entry.runtime_data.buses
    # A reading from outside the rungs first. A rung is crossed by moving from
    # outside it to inside, so a lone close reading establishes a baseline and
    # times nothing — which is the whole point of the 14 Sep fix.
    approaching = (crossed[0], crossed[1] - 2)
    for moment, dist in (
        (approaching, 2.5),
        (crossed, 0.9),
        (arrived, 0.0),
        ((9, 0), 5.0),
    ):
        with freeze_time_local(2026, 9, day, *moment):
            mock_api.async_get_rider_info.return_value = _at_distance(dist)
            await buses.async_refresh()
            await hass.async_block_till_done()


async def test_prediction_re_anchors_to_the_live_approach(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """An early bus is reported early, not averaged back to its usual time.

    Reproduces 3 Sep: two prior mornings both arrived at 08:01, so the clock
    median said 08:01. The bus then crossed a mile out at 07:52 and reached
    the stop at 07:56, and the estimate stayed 08:01 until it was four
    minutes wrong. Anchoring to the crossing plus the typical final leg
    tracks the early bus instead.
    """
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # Two mornings, each a five minute final leg ending at 08:01.
    for day in (1, 2):
        await _record_morning_arrival(
            hass,
            mock_config_entry,
            mock_api,
            day=day,
            crossed=(7, 56),
            arrived=(8, 1),
        )

    # A third morning: the bus is a mile out at 07:52, four minutes early.
    with freeze_time_local(2026, 9, 3, 7, 50):
        mock_api.async_get_rider_info.return_value = _at_distance(2.5)
        await buses.async_refresh()
        await hass.async_block_till_done()
    with freeze_time_local(2026, 9, 3, 7, 52):
        mock_api.async_get_rider_info.return_value = _at_distance(0.9)
        await buses.async_refresh()
        await hass.async_block_till_done()

        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    # Answered from where the bus has got to along the route, which outranks
    # the rung ladder because it reconsiders on every position report.
    assert prediction.basis == "route"
    assert prediction.route_samples == 2
    # Five minutes left from a mile out, as on both previous mornings — not
    # the 08:01 clock median.
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "07:57"


async def test_prediction_uses_clock_history_before_the_bus_is_close(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Far out, there is no approach to anchor to, so history is all there is."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    await _record_morning_arrival(
        hass, mock_config_entry, mock_api, day=1, crossed=(7, 56), arrived=(8, 1)
    )

    with freeze_time_local(2026, 9, 2, 7, 30):
        mock_api.async_get_rider_info.return_value = _at_distance(4.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.basis == "historical"
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "08:01"


async def test_history_is_replayed_again_when_the_scheme_widens(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """Arrivals recorded under a narrower scheme are replayed, not kept as-is.

    1.7.0 widened one anchor into a four-rung ladder. Skipping the replay
    merely because some final leg exists would have left that history holding
    only the old single rung, and the ladder would have behaved exactly as its
    predecessor did — the bug it was written to fix.
    """
    mock_config_entry.add_to_hass(hass)
    key = f"wheresthebus_arrivals.{mock_config_entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        # No "schema", and one rung: exactly what 1.6.0 wrote.
        "data": {
            "12345678": [
                {
                    "run": "am",
                    "at": "2026-09-01T12:01:27+00:00",
                    "closest": 0.0,
                    "legs": {"2": 240},
                }
            ]
        },
    }

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    # The replay ran and stamped the current scheme, so it will not run again.
    # Read from the constant: hard-coding it meant the assertion went stale
    # the moment the scheme was bumped, which is exactly when it matters.
    assert hass_storage[key]["data"]["schema"] == ARRIVAL_SCHEMA
    assert "riders" in hass_storage[key]["data"]


async def test_history_is_not_replayed_twice(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """History already captured under the current scheme is left alone."""
    mock_config_entry.add_to_hass(hass)
    key = f"wheresthebus_arrivals.{mock_config_entry.entry_id}"
    stored = {
        "schema": ARRIVAL_SCHEMA,
        "riders": {
            "12345678": [
                {
                    "run": "am",
                    "at": "2026-09-01T12:01:27+00:00",
                    "closest": 0.0,
                    "legs": {"0": 600, "1": 360, "2": 240, "3": 60},
                }
            ]
        },
    }
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": stored,
    }

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    rider = hass_storage[key]["data"]["riders"]["12345678"]
    assert rider[0]["legs"] == {"0": 600, "1": 360, "2": 240, "3": 60}


async def test_a_substitute_day_is_recorded_but_not_learned_from(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A replacement vehicle keeps its own time, so it must not teach.

    The arrival is still kept — it happened, and hiding it would make the
    history lie — but a different bus and driver run the route differently,
    and averaging that in drags every following day's estimate.
    """
    substitute = {
        **USER_INFO,
        "childBuses": [{**USER_INFO["childBuses"][0], "sub": "1962"}],
    }
    mock_api.async_get_user_info.return_value = substitute
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    with freeze_time_local(2026, 9, 7, 8, 2):
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
        await hass.async_block_till_done()
    with freeze_time_local(2026, 9, 7, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    recorded = buses.arrival_diagnostics()["riders"]["12345678"]["arrivals"]
    assert [item["substitute"] for item in recorded] == [True]

    # Recorded, but the estimate still falls back to the timetable.
    with freeze_time_local(2026, 9, 8, 6, 0):
        prediction = buses.predict_next_arrival(12345678)
    assert prediction is not None
    assert prediction.source == "scheduled"


async def test_a_learned_arrival_survives_a_restart(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """An arrival learned while running must reach storage, not just memory.

    ``_promote_pending`` reported no change no matter what it had learned, so
    the caller never saved: every journey observed live was lost on the next
    restart and only came back if the recorder still held it.
    """
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    stored = hass_storage[f"wheresthebus_arrivals.{mock_config_entry.entry_id}"]
    arrivals = stored["data"]["riders"]["12345678"]
    assert len(arrivals) == 1
    assert arrivals[0]["run"] == "am"
    assert stored["data"]["schema"] == ARRIVAL_SCHEMA


async def test_the_estimate_re_anchors_as_the_bus_closes_in(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Yesterday's arrival time must not decide today's.

    Once a rung has been crossed the estimate is that crossing plus the
    typical leg from it, so a bus running early is reported early.
    """
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # Day one: learn a four-minute leg from the 1 mile rung.
    with freeze_time_local(2026, 8, 31, 7, 56):
        mock_api.async_get_rider_info.return_value = _at_distance(2.5)
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 7, 58):
        mock_api.async_get_rider_info.return_value = _at_distance(0.9)
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    # Day two: the bus reaches the same rung ten minutes early.
    with freeze_time_local(2026, 9, 1, 7, 46):
        mock_api.async_get_rider_info.return_value = _at_distance(2.5)
        await buses.async_refresh()
    with freeze_time_local(2026, 9, 1, 7, 48):
        mock_api.async_get_rider_info.return_value = _at_distance(0.9)
        await buses.async_refresh()
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.basis == "approach"
    assert prediction.anchored_at == 1.0
    assert prediction.anchor_samples == 1
    # 07:48 plus the four-minute leg — not yesterday's 08:02.
    assert dt_util.as_local(prediction.arrival).strftime("%H:%M") == "07:52"


async def test_a_bus_that_turns_back_out_stops_anchoring(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A pass that comes close and leaves again is not the final approach."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    with freeze_time_local(2026, 8, 31, 7, 58):
        mock_api.async_get_rider_info.return_value = _at_distance(0.9)
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 9, 1, 7, 38):
        mock_api.async_get_rider_info.return_value = _at_distance(2.5)
        await buses.async_refresh()
    with freeze_time_local(2026, 9, 1, 7, 40):
        mock_api.async_get_rider_info.return_value = _at_distance(0.9)
        await buses.async_refresh()
    # It drifted back out well past the rung without ever reaching the stop.
    with freeze_time_local(2026, 9, 1, 7, 44):
        mock_api.async_get_rider_info.return_value = _at_distance(2.5)
        await buses.async_refresh()
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    # Back to the clock median rather than anchored to a crossing that lapsed.
    assert prediction.basis == "historical"
    assert prediction.anchored_at is None


async def test_retired_entities_are_removed_from_the_registry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """An entity that is no longer created must not linger as unavailable.

    Dropping a sensor does not forget it: the registry keeps the row and the
    UI shows it as permanently unavailable, which reads as a fault rather
    than a deliberate removal.
    """
    mock_config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in ("gps_age", "eta"):
        registry.async_get_or_create(
            Platform.SENSOR,
            DOMAIN,
            f"12345678_{key}",
            config_entry=mock_config_entry,
            suggested_object_id=f"robin_alex_rivera_{key}",
        )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id(Platform.SENSOR, DOMAIN, "12345678_gps_age")
        is None
    )
    assert registry.async_get_entity_id(Platform.SENSOR, DOMAIN, "12345678_eta") is None
    # The replacement is there in its place.
    assert (
        registry.async_get_entity_id(Platform.SENSOR, DOMAIN, "12345678_last_gps_fix")
        is not None
    )


async def test_a_friday_evening_predicts_monday_not_saturday(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """The search used to look one day ahead and know nothing about weekends."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # 11 Sep 2026 is a Friday; ask after that day's runs are over.
    with freeze_time_local(2026, 9, 11, 20, 0):
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    arrival = dt_util.as_local(prediction.arrival)
    assert arrival.strftime("%a %H:%M") == "Mon 07:56"


async def test_a_weekend_run_is_predicted_once_it_has_been_seen(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Weekdays are the default, not a rule — an observed Saturday counts.

    Inferring service days purely from history would be worse: a rider who
    has not happened to ride on a Wednesday yet must not lose Wednesdays.
    """
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # A Saturday arrival, observed and learned.
    with freeze_time_local(2026, 9, 12, 8, 2):
        mock_api.async_get_rider_info.return_value = _at_distance(0.0)
        await buses.async_refresh()
    with freeze_time_local(2026, 9, 12, 9, 30):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 9, 11, 20, 0):
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert dt_util.as_local(prediction.arrival).strftime("%a") == "Sat"


async def test_a_restart_mid_run_rebuilds_what_it_missed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A bus that has already been and gone must not look like a fresh approach.

    The closest approach and the rung crossings live only in memory. After a
    restart on 11 Sep at 17:44 — minutes after the bus had arrived — a pass
    four miles out re-anchored the estimate and would have been recorded as a
    second arrival for the day.
    """
    readings = [
        ("17:05:00", 2.7),
        ("17:13:00", 2.8),
        ("17:16:00", 0.9),
        ("17:20:00", 0.1),
    ]
    history = [
        State(
            "device_tracker.robin_alex_rivera_bus",
            "not_home",
            {
                "latitude": RIDER_INFO["stpLat"] + degrees(miles / 3958.7613),
                "longitude": RIDER_INFO["stpLon"],
                "stop_latitude": RIDER_INFO["stpLat"],
                "stop_longitude": RIDER_INFO["stpLon"],
            },
            last_updated=datetime.fromisoformat(f"2026-09-11T{clock}").replace(
                tzinfo=dt_util.get_default_time_zone()
            ),
        )
        for clock, miles in readings
    ]

    # The distance sensor is looked up in the registry, which on a real
    # restart is already populated from the previous run.
    mock_config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "device_tracker",
        DOMAIN,
        "12345678_bus",
        config_entry=mock_config_entry,
        suggested_object_id="robin_alex_rivera_bus",
    )

    with (
        freeze_time_local(2026, 9, 11, 17, 44),
        patch(
            "custom_components.wheresthebus.coordinator.async_position_history",
            AsyncMock(return_value=history),
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        buses = mock_config_entry.runtime_data.buses

        # The bus wanders back past on its way elsewhere.
        mock_api.async_get_rider_info.return_value = _at_distance(4.0)
        await buses.async_refresh()
        await hass.async_block_till_done()
        prediction = buses.predict_next_arrival(12345678)

    # The replayed run already reached the stop, so nothing re-anchors and the
    # 0.1 mile arrival stands rather than being replaced by the 4 mile pass.
    assert prediction is not None
    assert prediction.anchored_at is None
    key = (12345678, "pm", date(2026, 9, 11))
    assert buses._approach[key].arrived is True
    assert buses._pending[key][0] == pytest.approx(0.1)


async def test_the_journey_sensor_reports_the_current_stage(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """One sensor answers "what is happening", so nothing else has to guess."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # Nothing doing in the middle of the day.
    with freeze_time_local(2026, 9, 14, 13, 0):
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()

    journey = hass.states.get("sensor.robin_alex_rivera_journey")
    assert journey is not None
    assert journey.state == "idle"
    assert journey.attributes["options"] == [
        "idle",
        "to_stop",
        "at_stop",
        "to_school",
        "at_school",
        "from_school",
        "home",
    ]

    # The numbers a notification needs travel with it, so nothing downstream
    # has to work them out. Stage transitions themselves are covered directly
    # in test_journey.py, at every instant of a school day.
    for key in ("progress", "target", "boarded", "journey_id"):
        assert key in journey.attributes


async def test_the_prediction_reports_an_earliest_and_a_latest(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A single time implies a precision the data does not have."""
    await setup_entry(hass, mock_config_entry)
    buses = mock_config_entry.runtime_data.buses

    # Two mornings that disagree: five minutes from a mile out, then seven.
    await _record_morning_arrival(
        hass, mock_config_entry, mock_api, day=1, crossed=(7, 56), arrived=(8, 1)
    )
    await _record_morning_arrival(
        hass, mock_config_entry, mock_api, day=2, crossed=(7, 54), arrived=(8, 1)
    )

    with freeze_time_local(2026, 9, 3, 7, 50):
        mock_api.async_get_rider_info.return_value = _at_distance(2.5)
        await buses.async_refresh()
    with freeze_time_local(2026, 9, 3, 7, 52):
        mock_api.async_get_rider_info.return_value = _at_distance(0.9)
        await buses.async_refresh()
        await hass.async_block_till_done()
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.earliest is not None
    assert prediction.latest is not None
    # The two mornings bracket it, and the estimate sits inside the bracket.
    assert prediction.earliest <= prediction.arrival <= prediction.latest
    assert dt_util.as_local(prediction.earliest).strftime("%H:%M") == "07:57"
    assert dt_util.as_local(prediction.latest).strftime("%H:%M") == "07:59"

    sensor = hass.states.get("sensor.robin_alex_rivera_next_arrival")
    assert sensor is not None
    assert sensor.attributes["uncertainty_minutes"] == 2


async def test_the_replay_reruns_when_the_store_holds_no_route(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """A schema number claiming the data exists is not the data existing.

    3.0.0's backfill ran, marked the schema current and saved — and a merge
    bug then discarded every route track it had recovered. The store was left
    saying "current schema, has legs" while holding no positions, so the guard
    skipped the replay on every later start and the fix could never land.
    """
    mock_config_entry.add_to_hass(hass)
    hass_storage[f"wheresthebus_arrivals.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "data": {
            "schema": ARRIVAL_SCHEMA,
            "riders": {
                "12345678": [
                    {
                        "run": "am",
                        "at": "2026-08-31T12:02:00+00:00",
                        "closest": 0.0,
                        "legs": {"0": 600, "1": 360, "2": 240, "3": 60},
                        "track": [],
                    }
                ]
            },
        },
    }

    replayed = AsyncMock(return_value=[])
    with patch(
        "custom_components.wheresthebus.coordinator.async_position_history", replayed
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Legs and a current schema are not enough: with no positions stored, the
    # replay has to run rather than assume its work was already done.
    assert replayed.called


async def test_one_live_track_does_not_stand_in_for_history_never_read(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """The third door into the same trap, found on 15 Sep.

    3.0.3 replaced a schema check with "does any arrival hold a track", which
    is the right question asked of the wrong scope. The live path records a
    track for every journey it watches, so the first real journey after the
    upgrade satisfied `any` on its own and the guard declared the history
    complete. Nineteen older arrivals had never been read and now never would
    be; with one morning track stored and MIN_ROUTE_SAMPLES at two, the route
    matcher could not reach its minimum and silently fell back to the ladder.

    The question has to be asked of each arrival: has the recorder been asked
    for THIS one.
    """
    mock_config_entry.add_to_hass(hass)
    hass_storage[f"wheresthebus_arrivals.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "data": {
            "schema": ARRIVAL_SCHEMA,
            "riders": {
                "12345678": [
                    # Older arrivals the recorder was never asked about.
                    {
                        "run": "am",
                        "at": "2026-09-08T12:02:00+00:00",
                        "closest": 0.0,
                        "legs": {"0": 600, "1": 360, "2": 240, "3": 60},
                        "track": [],
                    },
                    {
                        "run": "am",
                        "at": "2026-09-09T12:01:00+00:00",
                        "closest": 0.0,
                        "legs": {"0": 540, "1": 359, "2": 240, "3": 120},
                        "track": [],
                    },
                    # One journey the live path watched, which is what made
                    # `any(track)` true and stranded the other two.
                    {
                        "run": "am",
                        "at": "2026-09-14T12:01:48+00:00",
                        "closest": 0.0,
                        "legs": {"1": 449, "2": 329, "3": 150},
                        "track": [[449, 40.7300, -74.0230], [150, 40.7155, -74.0020]],
                    },
                ]
            },
        },
    }

    replayed = AsyncMock(return_value=[])
    with patch(
        "custom_components.wheresthebus.coordinator.async_position_history", replayed
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert replayed.called


async def test_an_arrival_the_recorder_cannot_reach_is_not_replayed_for_ever(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """The other half of the guard, and the reason it is a flag not a scan.

    An arrival older than the recorder keeps can never gain a track however
    often it is replayed. Asking only "is the track empty" would retry those
    on every start for the rest of the integration's life. Recording that the
    attempt was made is what separates "not read yet" from "read, nothing
    there" — and only the first is work.
    """
    mock_config_entry.add_to_hass(hass)
    hass_storage[f"wheresthebus_arrivals.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "data": {
            "schema": ARRIVAL_SCHEMA,
            "riders": {
                "12345678": [
                    {
                        "run": "am",
                        "at": "2026-08-27T12:04:46+00:00",
                        "closest": 0.0,
                        "legs": {"0": 1739, "1": 419, "2": 299, "3": 119},
                        "track": [],
                        "replayed": True,
                    }
                ]
            },
        },
    }

    replayed = AsyncMock(return_value=[])
    with patch(
        "custom_components.wheresthebus.coordinator.async_position_history", replayed
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert not replayed.called


async def test_a_run_that_has_already_arrived_is_not_offered_as_the_next_one(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """The estimate re-widening after the bus had gone, seen on 15 Sep.

    The bus reached the stop at 07:57. At 07:58 the morning window snapped
    from one minute wide back to fourteen and went on counting towards 08:01 —
    a prediction for an arrival that had already happened, growing vaguer the
    further past it the clock got.

    It needs a LEARNED time later than the timetable to appear at all, which
    is why it went unnoticed: the published 07:56 is already behind you at
    07:58, so the run rolls to tomorrow on its own. The learned 08:01 is not,
    and the historical branch asked only whether that was still in the future.

    What it could not see is that the run was over. An arrival is not written
    to history until its window closes, half an hour later, so nothing in the
    record contradicted it. Reading the pending arrival closes that gap.
    """
    mock_config_entry.add_to_hass(hass)
    # Five mornings that all arrived at 08:01, five minutes after the
    # timetable, which is what makes the learned time outlive the bus.
    hass_storage[f"wheresthebus_arrivals.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "data": {
            "schema": ARRIVAL_SCHEMA,
            "riders": {
                "12345678": [
                    {
                        "run": "am",
                        "at": datetime(
                            2026, 9, day, 8, 1, tzinfo=dt_util.get_default_time_zone()
                        ).isoformat(),
                        "closest": 0.0,
                        "legs": {"0": 600, "1": 360, "2": 240, "3": 60},
                        "track": [],
                        "replayed": True,
                    }
                    for day in (8, 9, 10, 11, 14)
                ]
            },
        },
    }

    with freeze_time_local(2026, 9, 15, 7, 56):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        buses = mock_config_entry.runtime_data.buses

        # The bus pulls up at the stop.
        mock_api.async_get_rider_info.return_value = _at_distance(0.1)
        await buses.async_refresh()
        await hass.async_block_till_done()

    assert buses._pending[(12345678, "am", date(2026, 9, 15))][0] == pytest.approx(0.1)

    # A minute later the morning is over, whatever the learned time still says.
    with freeze_time_local(2026, 9, 15, 7, 58):
        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.run == "pm"
    assert dt_util.as_local(prediction.arrival).date() == date(2026, 9, 15)


async def test_an_overdue_run_stays_the_next_arrival_until_its_window_shuts(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """The afternoon of 15 Sep, which cost the next morning's Live Activity.

    The bus ran its route near the stop without serving it, weaving in and out
    of the two mile rung. Each inward crossing re-anchored the estimate a few
    minutes ahead; the clock caught up with it; the prediction rolled on to
    tomorrow morning; the journey collapsed to idle — and the next crossing
    began the whole thing again. Three journeys in seventy minutes, so three
    Live Activities started and cleared, and by the next morning the iOS
    push-to-start budget was spent and nothing appeared at all.

    A run does not stop being the next arrival because its predicted time went
    by. The bus is late, not cancelled. It stays today's run until its window
    shuts or it actually arrives — one journey, one activity.
    """
    mock_config_entry.add_to_hass(hass)
    hass_storage[f"wheresthebus_arrivals.{mock_config_entry.entry_id}"] = {
        "version": 1,
        "data": {
            "schema": ARRIVAL_SCHEMA,
            "riders": {
                "12345678": [
                    {
                        "run": "pm",
                        "at": datetime(
                            2026, 9, day, 17, 21, tzinfo=dt_util.get_default_time_zone()
                        ).isoformat(),
                        "closest": 0.0,
                        "legs": {"0": 450, "1": 360, "2": 270, "3": 120},
                        "track": [],
                        "replayed": True,
                    }
                    for day in (8, 9, 10, 11, 14)
                ]
            },
        },
    }

    with freeze_time_local(2026, 9, 15, 17, 24):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        buses = mock_config_entry.runtime_data.buses

        # Out beyond the outer rung, so nothing is anchored and nothing on the
        # route matches: the historical branch is the one answering, which is
        # the branch that used to roll on to tomorrow here.
        mock_api.async_get_rider_info.return_value = _at_distance(5.0)
        await buses.async_refresh()
        await hass.async_block_till_done()
        overdue = buses.predict_next_arrival(12345678)

    # 17:21 has gone by and the bus has not come. It is still this afternoon's
    # bus that arrives next, not tomorrow morning's.
    assert overdue is not None
    assert overdue.run == "pm"
    assert dt_util.as_local(overdue.arrival).date() == date(2026, 9, 15)

    # Once the window shuts the afternoon really is over, and only then does
    # the answer become tomorrow.
    with freeze_time_local(2026, 9, 15, 17, 55):
        after = buses.predict_next_arrival(12345678)

    assert after is not None
    assert after.run == "am"
    assert dt_util.as_local(after.arrival).date() == date(2026, 9, 16)
