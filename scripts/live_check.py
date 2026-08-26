#!/usr/bin/env python3
"""Check the WheresTheBus API with real credentials, without Home Assistant.

Reads credentials from the environment so they never land in shell history:

    export WTB_EMAIL='you@example.com'
    read -rs WTB_PASSWORD && export WTB_PASSWORD
    python3 scripts/live_check.py

Prints what the integration would expose.  Only ``aiohttp`` is required.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

import aiohttp

ROOT = "https://mdt.wheresthebus.com/"
PATH = "wtbparentapp/api/v2/"


async def call(session: aiohttp.ClientSession, url: str, body: dict) -> dict:
    """POST a JSON body and return the decoded envelope."""
    async with session.post(url, json=body) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def main() -> int:
    """Log in and dump the live rider data."""
    email = os.environ.get("WTB_EMAIL")
    password = os.environ.get("WTB_PASSWORD")
    if not email or not password:
        print("Set WTB_EMAIL and WTB_PASSWORD first.", file=sys.stderr)
        return 2

    device_id = str(uuid.uuid4())

    async with aiohttp.ClientSession() as session:
        login = await call(
            session,
            f"{ROOT}{PATH}login",
            {
                "emailId": email,
                "password": password,
                "imeiNo": device_id,
                "deviceType": "FlutterWeb",
                "sso": 0,
                "deviceOS": "Web_safari_Flutter",
            },
        )
        if login.get("resCode") != 0:
            print(f"Login failed: {login.get('mesgStr')}", file=sys.stderr)
            return 1

        payload = login["payload"]
        base = payload.get("basePath") or ROOT
        sid = payload["sessionId"]
        print(f"Logged in as {payload.get('firstName')} {payload.get('lastName')}")
        print(f"  shard    {payload.get('shardId')}")
        print(f"  basePath {base}")

        def url(endpoint: str) -> str:
            return f"{base}{PATH}{endpoint}?sessionId={sid}"

        info = await call(
            session,
            url("getUserInfo"),
            {
                "imeiNo": device_id,
                "versionInstalled": "5.2.2",
                "tokenId": "",
                "deviceNotif": True,
                "sessionId": sid,
            },
        )
        buses = (info.get("payload") or {}).get("childBuses") or []
        print(f"\n{len(buses)} rider(s) on this account")

        riders = await call(session, url("getAllRiders"), {"sessionId": sid})
        for rider in (riders.get("payload") or {}).get("allRiders") or []:
            print(f"\n  {rider.get('riderName')}")
            print(f"    school     {rider.get('schoolName')}")
            print(
                f"    AM stop    {rider.get('amStopTime')} @ {rider.get('amStopAddress')}"
            )
            print(
                f"    PM stop    {rider.get('pmStopTime')} @ {rider.get('pmStopAddress')}"
            )

        for bus in buses:
            live = await call(
                session,
                url("getRiderInfoEx"),
                {
                    "bid": bus.get("busNo"),
                    "chdId": bus.get("childId"),
                    "lastServerTime": 0,
                    "sessionId": sid,
                },
            )
            data = live.get("payload") or {}
            print(f"\n  Bus {bus.get('busNo')} (child {bus.get('childId')})")
            print(f"    position   {data.get('busLat')}, {data.get('busLon')}")
            print(
                f"    distance   {data.get('dist')} ({'km' if data.get('isDistKm') else 'mi'})"
            )
            print(f"    status     {data.get('stsMsg')!r}  eta {data.get('etaMsg')!r}")
            print(f"    refresh    {data.get('refreshTime')}s")

        scans = await call(session, url("getStudentScan"), {"sessionId": sid})
        print("\n  Recent ID scans")
        for detail in (scans.get("payload") or {}).get("studentDetails") or []:
            print(f"    {detail.get('studentName')}")
            for scan in detail.get("studentScans") or []:
                when = datetime.fromtimestamp(scan["scanTime"], tz=UTC).astimezone()
                print(
                    f"      {when:%Y-%m-%d %H:%M %Z}  "
                    f"{scan.get('scanLocation')}  ({scan.get('scanMethod')})"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
