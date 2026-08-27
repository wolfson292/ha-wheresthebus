"""The WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WheresTheBusApi, WheresTheBusAuthError, WheresTheBusError
from .const import (
    CONF_BUS_SCAN_INTERVAL,
    CONF_DEVICE_ID,
    CONF_STUDENT_SCAN_INTERVAL,
    DEFAULT_BUS_SCAN_INTERVAL,
    DEFAULT_STUDENT_SCAN_INTERVAL,
)
from .coordinator import WheresTheBusBusCoordinator, WheresTheBusStudentCoordinator

PLATFORMS: list[Platform] = [Platform.DEVICE_TRACKER, Platform.SENSOR]


@dataclass(slots=True)
class WheresTheBusData:
    """Runtime objects shared by the platforms."""

    api: WheresTheBusApi
    students: WheresTheBusStudentCoordinator
    buses: WheresTheBusBusCoordinator


type WheresTheBusConfigEntry = ConfigEntry[WheresTheBusData]


async def async_setup_entry(
    hass: HomeAssistant, entry: WheresTheBusConfigEntry
) -> bool:
    """Set up WheresTheBus from a config entry."""
    api = WheresTheBusApi(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DEVICE_ID],
    )

    try:
        await api.async_login()
    except WheresTheBusAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except WheresTheBusError as err:
        raise ConfigEntryNotReady(str(err)) from err

    students = WheresTheBusStudentCoordinator(
        hass,
        entry,
        api,
        entry.options.get(CONF_STUDENT_SCAN_INTERVAL, DEFAULT_STUDENT_SCAN_INTERVAL),
    )
    # Accumulated scans are restored before the first poll so the scan sensors
    # come back with yesterday's values instead of blanking until the next scan.
    await students.async_load_history()
    await students.async_config_entry_first_refresh()

    buses = WheresTheBusBusCoordinator(
        hass,
        entry,
        api,
        students,
        entry.options.get(CONF_BUS_SCAN_INTERVAL, DEFAULT_BUS_SCAN_INTERVAL),
    )
    await buses.async_config_entry_first_refresh()

    entry.runtime_data = WheresTheBusData(api=api, students=students, buses=buses)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: WheresTheBusConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: WheresTheBusConfigEntry
) -> None:
    """Reload the entry so new poll intervals take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
