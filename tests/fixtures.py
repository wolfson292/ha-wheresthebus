"""API payloads in the shape the WheresTheBus parent app returns.

Every name, identifier, address and coordinate here is invented.  Only the
structure is real: key names, nesting, the epoch-seconds ``scanTime``, the
miles/kilometres ``isDistKm`` flag, and the empty-string placeholders the API
uses instead of nulls.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

LOGIN_PAYLOAD = {
    "emailId": "parent@example.com",
    "firstName": "Alex",
    "lastName": "Rivera",
    "sessionId": "00000000-1111-2222-3333-444444444444",
    "shardId": "sh_05",
    "basePath": "https://mdt.wheresthebus.com/sh_05/",
}

USER_INFO = {
    "emailId": "parent@example.com",
    "distNotifEnable": True,
    "scanNotifEnable": True,
    "showQRCode": 1,
    "childBuses": [
        {
            "childId": 12345678,
            "busNo": "1234",
            "routeNo": "1234",
            "busTime": "5:48",
            "sub": "",
            "tripSegVal": None,
        }
    ],
    "shardId": "sh_05",
    "distId": 1,
}

ALL_RIDERS = [
    {
        "amBusNo": "1234",
        "latePmStopLat": 0.0,
        "homLon": -74.00583,
        "amStopLat": 40.71550,
        "latePmStopTime": None,
        "amStopLon": -74.00200,
        "riderName": "Robin Alex Rivera",
        "latePmBusNo": None,
        "amStopAddress": "MAPLE RD & 3RD ST",
        "studentId": "10000001",
        "latePmStopAddress": None,
        "pmStopLon": -74.00200,
        "pmStopAddress": "MAPLE RD & 3RD ST",
        "pmBusNo": "1234",
        "pmStopLat": 40.71550,
        "pmStopTime": "5:48 P.M.",
        "pmStopId": 900000017,
        "amStopId": 900000019,
        "schoolName": "Riverside Middle School",
        "amStopTime": "7:56 A.M.",
        "latePmStopLon": 0.0,
        "latePmStopId": None,
        "homLat": 40.71028,
    }
]

# 2026-08-26, US/Eastern: 08:12 at the neighbourhood stop, 09:30 at school,
# 16:20 back at school for the ride home.
STUDENT_SCANS = {
    "studentDetails": [
        {
            "studentName": "Robin Rivera",
            "studentScans": [
                {
                    "scanTime": 1787746325,
                    "scanLocation": "Maple Rd, Springfield",
                    "scanMethod": "Keypad",
                    "bus": "1234",
                },
                {
                    "scanTime": 1787751003,
                    "scanLocation": "Riverside Middle School",
                    "scanMethod": "Tablet",
                    "bus": "1234",
                },
                {
                    "scanTime": 1787775599,
                    "scanLocation": "Riverside Middle School",
                    "scanMethod": "Keypad",
                    "bus": "1234",
                },
            ],
        }
    ],
    "studentInfo": [{"stud_id": "10000001", "full_name": "ROBIN RIVERA"}],
}

RIDER_INFO = {
    "refreshTime": 15,
    "isDistKm": 0,
    "homLat": 40.71028,
    "homLon": -74.00583,
    "schLat": 40.75820,
    "schLon": -73.98550,
    "busLat": 40.73100,
    "busLon": -73.99500,
    "curr_seq": 1736,
    "stpLat": 40.71550,
    "stpLon": -74.00200,
    "dist": 3.2,
    "stsClr": "#EFF942",
    "stsMsg": "current",
    "etaMsg": "",
    "noMsg": "",
    "IndBsMsg": "",
    "lst10Min": [],
    "childBuses": USER_INFO["childBuses"],
    "serverTime": 1787778893,
}
