"""Fixtures for the WheresTheBus tests."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wheresthebus.const import CONF_DEVICE_ID, DOMAIN

from .fixtures import (
    ALL_RIDERS,
    LOGIN_PAYLOAD,
    RIDER_INFO,
    STUDENT_SCANS,
    USER_INFO,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of the custom integration in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a configured WheresTheBus entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Alex Rivera",
        unique_id="parent@example.com",
        data={
            CONF_EMAIL: "parent@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_DEVICE_ID: "11111111-2222-3333-4444-555555555555",
        },
    )


@pytest.fixture
def mock_api() -> Generator[AsyncMock]:
    """Patch the API client used by both the integration and the config flow."""
    with (
        patch(
            "custom_components.wheresthebus.WheresTheBusApi", autospec=True
        ) as mock_client,
        patch(
            "custom_components.wheresthebus.config_flow.WheresTheBusApi",
            new=mock_client,
        ),
    ):
        api = mock_client.return_value
        api.shard_id = "sh_05"
        api.async_login.return_value = LOGIN_PAYLOAD
        api.async_get_user_info.return_value = USER_INFO
        api.async_get_all_riders.return_value = ALL_RIDERS
        api.async_get_student_scans.return_value = STUDENT_SCANS
        api.async_get_rider_info.return_value = dict(RIDER_INFO)
        yield api
