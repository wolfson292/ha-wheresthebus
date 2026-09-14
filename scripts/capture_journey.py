r"""Turn a recorded run into a replayable test fixture.

The recorder already holds every position the bus reported. This pulls one
run out of it, moves the coordinates to an unrelated origin, and writes a
journey the backtest can score the estimate against.

Every prediction fault in this integration was discoverable this way, offline,
before release. Several were instead discovered by somebody standing at a bus
stop reading a wrong number. A captured journey costs one command and turns
"does this change make the estimate better or worse" into a question with an
answer.

Usage:

    python scripts/capture_journey.py dump.json pm \\
        --boarded 2026-09-11T16:14:21-04:00 \\
        --arrived 2026-09-11T17:20:07-04:00 \\
        --out tests/journeys/2026-09-11-pm.json

where dump.json is the recorder history for the bus tracker over the run,
including attributes. In Home Assistant's template editor:

    {{ states.device_tracker.RIDER_bus }}

or via the REST API:

    GET /api/history/period/<start>?filter_entity_id=device_tracker.RIDER_bus

PRIVACY: the output is NOT safe to publish, and tests/journeys/ is gitignored
for that reason. Coordinates are re-placed around a fictional origin, which
hides the latitude and longitude — but it deliberately preserves every
distance and bearing between points, and a few miles of turns is a fingerprint.
Matched against the road network it goes straight back on the map. This is a
child's daily route to and from school. Keep it local.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import json
from math import cos, radians
from pathlib import Path

MILES_PER_DEGREE = 3958.7613 * radians(1)
# Somewhere unremarkable, and nowhere near the runs this was written against.
FICTIONAL_STOP = (40.0, -75.0)


def _relocate(lat: float, lon: float, stop: tuple[float, float]) -> tuple[float, float]:
    """Re-place a point around the fictional stop, preserving the geometry."""
    north = (lat - stop[0]) * MILES_PER_DEGREE
    east = (lon - stop[1]) * MILES_PER_DEGREE * cos(radians(stop[0]))
    return (
        round(FICTIONAL_STOP[0] + north / MILES_PER_DEGREE, 5),
        round(
            FICTIONAL_STOP[1]
            + east / (MILES_PER_DEGREE * cos(radians(FICTIONAL_STOP[0]))),
            5,
        ),
    )


def main() -> None:
    """Read a recorder dump and write an anonymised journey."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="recorder history JSON")
    parser.add_argument("run", choices=("am", "pm"))
    parser.add_argument("--boarded", required=True)
    parser.add_argument("--arrived", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.dump.read_text())
    # Accept either the REST shape (a list of lists) or this repo's tooling.
    states = raw["data"]["entities"][0]["states"] if "data" in raw else raw[0]

    first = next(s for s in states if "stop_latitude" in (s.get("attributes") or {}))
    attributes = first["attributes"]
    stop = (float(attributes["stop_latitude"]), float(attributes["stop_longitude"]))

    samples = []
    for state in states:
        found = state.get("attributes") or {}
        if "latitude" not in found:
            continue
        lat, lon = _relocate(float(found["latitude"]), float(found["longitude"]), stop)
        samples.append(
            {
                "at": state.get("last_updated") or state.get("last_changed"),
                "lat": lat,
                "lon": lon,
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "note": (
                    "A real run, recorded at the live poll interval. Coordinates "
                    "are moved to an unrelated origin: distances and bearings "
                    "between points are preserved, the location is not."
                ),
                "run": args.run,
                "stop": {"lat": FICTIONAL_STOP[0], "lon": FICTIONAL_STOP[1]},
                "boarded": args.boarded,
                "arrived": args.arrived,
                "samples": samples,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"{len(samples)} samples -> {args.out}")


if __name__ == "__main__":
    main()
