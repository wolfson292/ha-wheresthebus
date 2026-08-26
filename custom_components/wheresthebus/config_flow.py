"""Config flow for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import WheresTheBusApi, WheresTheBusAuthError, WheresTheBusError
from .const import (
    CONF_BUS_SCAN_INTERVAL,
    CONF_DEVICE_ID,
    CONF_STUDENT_SCAN_INTERVAL,
    DEFAULT_BUS_SCAN_INTERVAL,
    DEFAULT_STUDENT_SCAN_INTERVAL,
    DOMAIN,
    MAX_BUS_SCAN_INTERVAL,
    MAX_STUDENT_SCAN_INTERVAL,
    MIN_BUS_SCAN_INTERVAL,
    MIN_STUDENT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class WheresTheBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the WheresTheBus config flow."""

    VERSION = 1

    async def _async_validate(self, email: str, password: str, device_id: str) -> str:
        """Log in and return the account's display name."""
        api = WheresTheBusApi(
            async_get_clientsession(self.hass), email, password, device_id
        )
        payload = await api.async_login()
        name = " ".join(
            part for part in (payload.get("firstName"), payload.get("lastName")) if part
        )
        return name or email

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials for a new account."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            # The API ties a session to a device id; a stable per-entry value
            # keeps Home Assistant from looking like a new device each restart.
            device_id = str(uuid.uuid4())
            try:
                title = await self._async_validate(
                    email, user_input[CONF_PASSWORD], device_id
                )
            except WheresTheBusAuthError:
                errors["base"] = "invalid_auth"
            except WheresTheBusError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating WheresTheBus login")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_ID: device_id,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the stored password stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password for the existing account."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            device_id = entry.data.get(CONF_DEVICE_ID) or str(uuid.uuid4())
            try:
                await self._async_validate(
                    entry.data[CONF_EMAIL], user_input[CONF_PASSWORD], device_id
                )
            except WheresTheBusAuthError:
                errors["base"] = "invalid_auth"
            except WheresTheBusError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating WheresTheBus login")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_ID: device_id,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            description_placeholders={CONF_EMAIL: entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return WheresTheBusOptionsFlow()


class WheresTheBusOptionsFlow(OptionsFlow):
    """Let the user tune how often each endpoint is polled."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_BUS_SCAN_INTERVAL: int(user_input[CONF_BUS_SCAN_INTERVAL]),
                    CONF_STUDENT_SCAN_INTERVAL: int(
                        user_input[CONF_STUDENT_SCAN_INTERVAL]
                    ),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BUS_SCAN_INTERVAL,
                        default=options.get(
                            CONF_BUS_SCAN_INTERVAL, DEFAULT_BUS_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_BUS_SCAN_INTERVAL,
                            max=MAX_BUS_SCAN_INTERVAL,
                            step=5,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_STUDENT_SCAN_INTERVAL,
                        default=options.get(
                            CONF_STUDENT_SCAN_INTERVAL, DEFAULT_STUDENT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_STUDENT_SCAN_INTERVAL,
                            max=MAX_STUDENT_SCAN_INTERVAL,
                            step=30,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
