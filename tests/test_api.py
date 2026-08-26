"""Tests for the WheresTheBus API client."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pytest
from aiohttp import ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.wheresthebus.api import (
    WheresTheBusApi,
    WheresTheBusAuthError,
    WheresTheBusError,
)

from .fixtures import LOGIN_PAYLOAD, RIDER_INFO, USER_INFO

LOGIN_URL = "https://mdt.wheresthebus.com/wtbparentapp/api/v2/login"
SHARD = "https://mdt.wheresthebus.com/sh_05/wtbparentapp/api/v2/"


@pytest.fixture
async def session(hass: HomeAssistant) -> ClientSession:
    """Return an aiohttp session bound to the test loop."""
    return async_get_clientsession(hass)


def make_api(session: ClientSession) -> WheresTheBusApi:
    """Return a client under test."""
    return WheresTheBusApi(session, "parent@example.com", "hunter2", "device-1")


async def test_login_stores_shard_base_path(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """The login payload's basePath becomes the base for later calls."""
    aioclient_mock.post(
        LOGIN_URL, json={"resCode": 0, "mesgStr": "", "payload": LOGIN_PAYLOAD}
    )
    api = make_api(session)

    await api.async_login()

    assert api.session_id == "00000000-1111-2222-3333-444444444444"
    assert api.base_path == "https://mdt.wheresthebus.com/sh_05/"
    assert api.shard_id == "sh_05"

    # The client identifies itself the way the Flutter web app does.
    body = aioclient_mock.mock_calls[0][2]
    assert body["emailId"] == "parent@example.com"
    assert body["imeiNo"] == "device-1"
    assert body["deviceType"] == "FlutterWeb"


async def test_login_rejects_bad_credentials(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """A non-zero resCode on login raises an auth error carrying the message."""
    aioclient_mock.post(
        LOGIN_URL, json={"resCode": 3, "mesgStr": "Invalid Login", "payload": None}
    )
    api = make_api(session)

    with pytest.raises(WheresTheBusAuthError, match="Invalid Login"):
        await api.async_login()


async def test_login_without_a_session_id_is_an_auth_error(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """A success code with no session is still a failed login."""
    aioclient_mock.post(LOGIN_URL, json={"resCode": 0, "payload": {}})
    api = make_api(session)

    with pytest.raises(WheresTheBusAuthError):
        await api.async_login()


async def test_http_error_is_wrapped(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """A 5xx becomes a WheresTheBusError rather than an aiohttp error."""
    aioclient_mock.post(LOGIN_URL, status=502)
    api = make_api(session)

    with pytest.raises(WheresTheBusError):
        await api.async_login()


async def test_call_reauthenticates_once(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """An expired session triggers one silent re-login and a retry."""
    aioclient_mock.post(LOGIN_URL, json={"resCode": 0, "payload": LOGIN_PAYLOAD})
    responses = [
        {"resCode": 9, "mesgStr": "Session Expired", "payload": None},
        {"resCode": 0, "mesgStr": "", "payload": USER_INFO},
    ]

    async def _user_info(method, url, data):
        return AiohttpClientMockResponse(method, url, json=responses.pop(0))

    aioclient_mock.post(f"{SHARD}getUserInfo", side_effect=_user_info)

    api = make_api(session)
    result = await api.async_get_user_info()

    assert result["childBuses"][0]["childId"] == 12345678
    # Two logins: the implicit first one and the refresh after the failure.
    login_calls = [
        call for call in aioclient_mock.mock_calls if str(call[1]) == LOGIN_URL
    ]
    assert len(login_calls) == 2


async def test_call_gives_up_after_one_retry(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """A persistently failing call raises with the server's message."""
    aioclient_mock.post(LOGIN_URL, json={"resCode": 0, "payload": LOGIN_PAYLOAD})
    aioclient_mock.post(
        f"{SHARD}getAllRiders",
        json={"resCode": 9, "mesgStr": "Session Expired", "payload": None},
    )
    api = make_api(session)

    with pytest.raises(WheresTheBusError, match="Session Expired"):
        await api.async_get_all_riders()


async def test_rider_info_carries_server_time(
    aioclient_mock: AiohttpClientMocker, session: ClientSession
) -> None:
    """ServerTime is folded into the payload for the next lastServerTime."""
    aioclient_mock.post(LOGIN_URL, json={"resCode": 0, "payload": LOGIN_PAYLOAD})
    payload = {key: value for key, value in RIDER_INFO.items() if key != "serverTime"}
    aioclient_mock.post(
        f"{SHARD}getRiderInfoEx",
        json={"resCode": 0, "payload": payload, "serverTime": 1787778893},
    )
    api = make_api(session)

    info = await api.async_get_rider_info("1234", 12345678, 0)

    assert info["busLat"] == 40.73100
    assert info["serverTime"] == 1787778893

    body = aioclient_mock.mock_calls[-1][2]
    assert body["bid"] == "1234"
    assert body["chdId"] == 12345678
    assert body["lastServerTime"] == 0
    assert body["sessionId"] == "00000000-1111-2222-3333-444444444444"
