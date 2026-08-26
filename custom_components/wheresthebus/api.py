"""Client for the WheresTheBus parent app API."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    API_PATH,
    API_ROOT,
    APP_VERSION,
    DEVICE_OS,
    DEVICE_TYPE,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class WheresTheBusError(Exception):
    """Raised when the API cannot be reached or returns an unusable response."""


class WheresTheBusAuthError(WheresTheBusError):
    """Raised when credentials are rejected."""


class WheresTheBusApi:
    """Minimal async client for the endpoints the parent app uses."""

    def __init__(
        self,
        session: ClientSession,
        email: str,
        password: str,
        device_id: str,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._email = email
        self._password = password
        self._device_id = device_id
        self._login_lock = asyncio.Lock()
        self._timeout = ClientTimeout(total=REQUEST_TIMEOUT.total_seconds())

        self.session_id: str | None = None
        self.base_path: str = API_ROOT
        self.shard_id: str | None = None
        self.first_name: str | None = None
        self.last_name: str | None = None

    @property
    def email(self) -> str:
        """Return the account email."""
        return self._email

    async def _request(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON body and return the decoded payload."""
        try:
            async with self._session.post(
                url, json=body, timeout=self._timeout
            ) as response:
                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise WheresTheBusAuthError("Session rejected by server")
                response.raise_for_status()
                # The API always answers with JSON but does not always set a
                # JSON content type, so decoding is forced.
                data = await response.json(content_type=None)
        except WheresTheBusError:
            raise
        except TimeoutError as err:
            raise WheresTheBusError(f"Timeout talking to {url}") from err
        except ClientError as err:
            raise WheresTheBusError(f"Error talking to {url}: {err}") from err
        except ValueError as err:
            raise WheresTheBusError(f"Malformed response from {url}: {err}") from err

        if not isinstance(data, dict):
            raise WheresTheBusError(f"Unexpected response from {url}: {data!r}")
        return data

    async def async_login(self) -> dict[str, Any]:
        """Authenticate and remember the session id and shard base path.

        The front-door login answers with a 307 to the account's shard; aiohttp
        replays the POST body against the redirect target for us.
        """
        async with self._login_lock:
            data = await self._request(
                f"{API_ROOT}{API_PATH}login",
                {
                    "emailId": self._email,
                    "password": self._password,
                    "imeiNo": self._device_id,
                    "deviceType": DEVICE_TYPE,
                    "sso": 0,
                    "deviceOS": DEVICE_OS,
                },
            )

            if data.get("resCode") != 0:
                raise WheresTheBusAuthError(
                    data.get("mesgStr") or "Invalid email or password"
                )

            payload = data.get("payload") or {}
            session_id = payload.get("sessionId")
            if not session_id:
                raise WheresTheBusAuthError("Login succeeded but returned no session")

            base_path = payload.get("basePath") or API_ROOT
            if not base_path.endswith("/"):
                base_path = f"{base_path}/"

            self.session_id = session_id
            self.base_path = base_path
            self.shard_id = payload.get("shardId")
            self.first_name = payload.get("firstName")
            self.last_name = payload.get("lastName")
            _LOGGER.debug("Logged in to WheresTheBus shard %s", self.shard_id)
            return payload

    async def _call(
        self, endpoint: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call an authenticated endpoint, re-authenticating once if needed.

        Returns the whole response envelope; callers that only need ``payload``
        can index into it, and ``getRiderInfoEx`` also needs ``serverTime``.
        """
        if self.session_id is None:
            await self.async_login()

        for attempt in (1, 2):
            session_id = self.session_id
            request_body = {**(body or {}), "sessionId": session_id}
            url = f"{self.base_path}{API_PATH}{endpoint}?sessionId={session_id}"
            data = await self._request(url, request_body)

            if data.get("resCode") == 0:
                return data

            message = data.get("mesgStr") or f"resCode {data.get('resCode')}"
            if attempt == 1:
                # A non-zero resCode on an authenticated call is almost always a
                # dead session, so re-login once before giving up.
                _LOGGER.debug(
                    "%s returned %s, refreshing session and retrying", endpoint, message
                )
                await self.async_login()
                continue

            raise WheresTheBusError(f"{endpoint} failed: {message}")

        raise WheresTheBusError(f"{endpoint} failed")

    async def async_get_user_info(self) -> dict[str, Any]:
        """Return account settings plus the list of the account's child buses."""
        data = await self._call(
            "getUserInfo",
            {
                "imeiNo": self._device_id,
                "versionInstalled": APP_VERSION,
                # Sent exactly as the web client sends them.  An empty
                # tokenId means this client registers no push target, so the
                # account's real devices keep their own notification settings.
                "tokenId": "",
                "deviceNotif": True,
            },
        )
        return data.get("payload") or {}

    async def async_get_all_riders(self) -> list[dict[str, Any]]:
        """Return the roster with AM/PM stop details for each rider."""
        data = await self._call("getAllRiders")
        return (data.get("payload") or {}).get("allRiders") or []

    async def async_get_rider_info(
        self, bus_no: str, child_id: int, last_server_time: int = 0
    ) -> dict[str, Any]:
        """Return live bus position and stop distance for one child.

        ``serverTime`` is folded into the result so the caller can feed it back
        as ``lastServerTime`` and receive only new breadcrumb points.
        """
        data = await self._call(
            "getRiderInfoEx",
            {
                "bid": bus_no,
                "chdId": child_id,
                "lastServerTime": last_server_time,
            },
        )
        payload = data.get("payload") or {}
        payload["serverTime"] = data.get("serverTime") or 0
        return payload

    async def async_get_student_scans(self) -> dict[str, Any]:
        """Return recent student ID scan events."""
        data = await self._call("getStudentScan")
        return data.get("payload") or {}
