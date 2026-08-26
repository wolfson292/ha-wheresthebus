"""Tests for the WheresTheBus config flow."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wheresthebus.api import (
    WheresTheBusAuthError,
    WheresTheBusError,
)
from custom_components.wheresthebus.const import (
    CONF_BUS_SCAN_INTERVAL,
    CONF_DEVICE_ID,
    CONF_STUDENT_SCAN_INTERVAL,
    DOMAIN,
)

USER_INPUT = {CONF_EMAIL: "parent@example.com", CONF_PASSWORD: "hunter2"}


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_api: AsyncMock
) -> None:
    """A valid login creates an entry titled with the parent's name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alex Rivera"
    assert result["data"][CONF_EMAIL] == "parent@example.com"
    assert result["data"][CONF_PASSWORD] == "hunter2"
    # A device id is generated so the API sees a stable client.
    assert result["data"][CONF_DEVICE_ID]
    assert result["result"].unique_id == "parent@example.com"


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (WheresTheBusAuthError("nope"), "invalid_auth"),
        (WheresTheBusError("boom"), "cannot_connect"),
        (RuntimeError("surprise"), "unknown"),
    ],
)
async def test_user_flow_errors_recover(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    side_effect: Exception,
    expected: str,
) -> None:
    """Login failures are shown on the form and the flow can be retried."""
    mock_api.async_login.side_effect = side_effect

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    mock_api.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_account_is_rejected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """The same email cannot be configured twice."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_email_is_matched_case_insensitively(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A differently cased email is still the same account."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "  Parent@Example.com  ", CONF_PASSWORD: "hunter2"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Reauth stores the new password and keeps the device id."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-password"
    assert (
        mock_config_entry.data[CONF_DEVICE_ID] == "11111111-2222-3333-4444-555555555555"
    )


async def test_reauth_reports_a_bad_password(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """A still-wrong password keeps the reauth form open."""
    mock_config_entry.add_to_hass(hass)
    mock_api.async_login.side_effect = WheresTheBusAuthError("nope")

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_flow_sets_intervals(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: AsyncMock
) -> None:
    """Poll intervals are stored as integers."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_BUS_SCAN_INTERVAL: 60, CONF_STUDENT_SCAN_INTERVAL: 600},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_BUS_SCAN_INTERVAL: 60,
        CONF_STUDENT_SCAN_INTERVAL: 600,
    }
