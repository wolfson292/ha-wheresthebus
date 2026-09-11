"""Tests for setting up the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import date, datetime
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
    for moment, dist in (
        (crossed, 0.9),
        (arrived, 0.0),
        ((9, 0), 5.0),
    ):
        with freeze_time_local(2026, 9, day, *moment):
            mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": dist}
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
    with freeze_time_local(2026, 9, 3, 7, 52):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.9}
        await buses.async_refresh()
        await hass.async_block_till_done()

        prediction = buses.predict_next_arrival(12345678)

    assert prediction is not None
    assert prediction.basis == "approach"
    # 07:52 crossing + the five minute median leg, not the 08:01 clock median.
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
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 4.0}
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
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
        await hass.async_block_till_done()
    with freeze_time_local(2026, 9, 7, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
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
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
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
    with freeze_time_local(2026, 8, 31, 7, 58):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.9}
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    # Day two: the bus reaches the same rung ten minutes early.
    with freeze_time_local(2026, 9, 1, 7, 48):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.9}
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
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.9}
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 8, 2):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
    with freeze_time_local(2026, 8, 31, 9, 0):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
        await buses.async_refresh()
        await hass.async_block_till_done()

    with freeze_time_local(2026, 9, 1, 7, 40):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.9}
        await buses.async_refresh()
    # It drifted back out well past the rung without ever reaching the stop.
    with freeze_time_local(2026, 9, 1, 7, 44):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 2.5}
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
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 0.0}
        await buses.async_refresh()
    with freeze_time_local(2026, 9, 12, 9, 30):
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 5.0}
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
        ("17:05:00", "2.7"),
        ("17:13:00", "2.8"),
        ("17:16:00", "0.9"),
        ("17:20:00", "0.1"),
    ]
    history = [
        State(
            "sensor.robin_alex_rivera_distance_to_stop",
            value,
            last_updated=datetime.fromisoformat(f"2026-09-11T{clock}").replace(
                tzinfo=dt_util.get_default_time_zone()
            ),
        )
        for clock, value in readings
    ]

    # The distance sensor is looked up in the registry, which on a real
    # restart is already populated from the previous run.
    mock_config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        "12345678_distance_to_stop",
        config_entry=mock_config_entry,
        suggested_object_id="robin_alex_rivera_distance_to_stop",
    )

    with (
        freeze_time_local(2026, 9, 11, 17, 44),
        patch(
            "custom_components.wheresthebus.coordinator.async_distance_history",
            AsyncMock(return_value=history),
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        buses = mock_config_entry.runtime_data.buses

        # The bus wanders back past on its way elsewhere.
        mock_api.async_get_rider_info.return_value = {**RIDER_INFO, "dist": 4.0}
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
