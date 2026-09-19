#!/usr/bin/env python3
"""Export learned arrival history into a portable bundle.

The integration's whole value is the history it has accumulated: every
arrival it has seen, the rung-crossing legs measured from each, and the
GPS track the bus actually drove. A rewrite that starts from nothing
spends weeks being wrong before it is useful, and every one of those
weeks lands on somebody standing at a bus stop. This hands that history
to whatever comes next.

Input is Home Assistant's own store file, ``.storage/wheresthebus_arrivals``
under the HA config directory. Nothing here talks to Home Assistant or to
the WheresTheBus API; it is a pure file-to-file conversion, so it can be
run against a copy taken from a backup.

THE OUTPUT CONTAINS REAL ROUTE COORDINATES and must never be committed or
shared. A few miles of turns is a fingerprint that can be matched against
a road network and put back on the map, and this is a child's daily route
to and from school. The default destination is outside the repository and
the script refuses to write inside it.

    python3 scripts/export_history.py ~/wtb-arrivals.json

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Mirrors const.ANCHOR_LADDER_*; the store records rungs by index, and an
# index is meaningless to a reader without the distance it stands for.
LADDER_MILES = (3.0, 2.0, 1.0, 0.5)
LADDER_KM = (4.8, 3.2, 1.6, 0.8)

EXPORT_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _load_store(path: Path) -> dict[str, Any]:
    """Return the ``data`` payload of a Home Assistant store file."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        _fail(f"no such file: {path}")
    except json.JSONDecodeError as err:
        _fail(f"{path} is not valid JSON: {err}")

    if not isinstance(raw, dict):
        _fail(f"{path} is not a Home Assistant store file")

    # A store file wraps its payload; a payload extracted by hand is also
    # accepted, since that is what a support copy-paste usually produces.
    data = raw.get("data") if "data" in raw else raw
    if not isinstance(data, dict) or "riders" not in data:
        _fail(
            f"{path} has no 'riders' key — is this wheresthebus_arrivals, "
            "or did you point at wheresthebus_scans by mistake?"
        )
    return data


def _summarise(arrivals: list[dict[str, Any]], ladder: tuple[float, ...]) -> dict:
    """Describe what a rider's history actually contains.

    A bundle you cannot sanity-check is a bundle you have to trust. These
    are the numbers worth reading before wiring anything to it: how many
    arrivals, how far back, which rungs carry data, and how much of the
    history has a usable track behind it.
    """
    by_run: dict[str, dict[str, Any]] = {}
    for run in ("am", "pm"):
        of_run = [item for item in arrivals if item["run"] == run]
        if not of_run:
            continue
        minutes = []
        for item in of_run:
            when = datetime.fromisoformat(item["arrival"])
            minutes.append(when.hour * 60 + when.minute)
        legs_present = {
            rung: sum(1 for item in of_run if rung in item["legs"])
            for rung in (str(index) for index in range(len(ladder)))
        }
        by_run[run] = {
            "arrivals": len(of_run),
            "with_track": sum(1 for item in of_run if item["track"]),
            "substitutes": sum(1 for item in of_run if item["substitute"]),
            "first": min(item["arrival"] for item in of_run),
            "last": max(item["arrival"] for item in of_run),
            # Local clock time, which is what the historical basis predicts on.
            "median_local_minute": int(statistics.median(minutes)),
            "legs_per_rung": {
                f"{ladder[int(rung)]}": count for rung, count in legs_present.items()
            },
        }
    return by_run


def _convert(data: dict[str, Any]) -> dict[str, Any]:
    """Turn the store payload into the documented export shape."""
    in_km = bool(data.get("km"))
    ladder = LADDER_KM if in_km else LADDER_MILES

    riders = []
    for child_id, items in (data.get("riders") or {}).items():
        arrivals = [
            {
                "run": item["run"],
                "arrival": item["at"],
                "closest": item.get("closest"),
                # Rung index -> seconds from crossing it to arriving.
                "legs": {str(k): int(v) for k, v in (item.get("legs") or {}).items()},
                "substitute": bool(item.get("sub")),
                # [seconds before arrival, latitude, longitude], oldest first.
                # The single most valuable field here: it is what route
                # matching runs against.
                "track": [list(point) for point in (item.get("track") or [])],
                "recedes": item.get("recedes", 0),
                "stale": item.get("stale", 0),
                "boarded": item.get("boarded"),
                "replayed": bool(item.get("replayed")),
            }
            for item in items
        ]
        arrivals.sort(key=lambda entry: entry["arrival"])
        riders.append(
            {
                "child_id": child_id,
                "arrivals": arrivals,
                "summary": _summarise(arrivals, ladder),
            }
        )

    return {
        "export_version": EXPORT_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(),
        "source": "home-assistant custom_components/wheresthebus",
        "arrival_schema": data.get("schema"),
        "units": "km" if in_km else "miles",
        "anchor_ladder": list(ladder),
        "track_point_format": ["seconds_before_arrival", "latitude", "longitude"],
        "riders": riders,
    }


def main() -> None:
    """Convert the store file named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "destination",
        type=Path,
        nargs="?",
        default=Path.home() / "wtb-arrivals.json",
        help="where to write the bundle (default: ~/wtb-arrivals.json)",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=Path.home() / "wheresthebus_arrivals",
        help="the .storage/wheresthebus_arrivals file copied off the HA host",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=None,
        help="pretty-print with this indent (default: compact, much smaller)",
    )
    args = parser.parse_args()

    destination = args.destination.expanduser().resolve()
    # The repository is public. Writing real coordinates into it, even into
    # a gitignored path, is one `git add -f` away from a disclosure that
    # cannot be taken back.
    if REPO_ROOT in destination.parents or destination == REPO_ROOT:
        _fail(
            f"refusing to write route coordinates inside the repository "
            f"({REPO_ROOT}). Choose a destination outside it."
        )

    bundle = _convert(_load_store(args.store.expanduser()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(bundle, indent=args.indent))

    total = sum(len(rider["arrivals"]) for rider in bundle["riders"])
    tracked = sum(
        1
        for rider in bundle["riders"]
        for arrival in rider["arrivals"]
        if arrival["track"]
    )
    points = sum(
        len(arrival["track"])
        for rider in bundle["riders"]
        for arrival in rider["arrivals"]
    )
    size_kb = destination.stat().st_size / 1024

    print(f"wrote {destination}  ({size_kb:.0f} KB)")
    print(
        f"  {len(bundle['riders'])} rider(s), {total} arrivals, "
        f"{tracked} with a track, {points} track points"
    )
    for rider in bundle["riders"]:
        for run, facts in rider["summary"].items():
            print(
                f"  rider {rider['child_id']} {run}: {facts['arrivals']} arrivals "
                f"({facts['with_track']} tracked), {facts['first'][:10]}"
                f" to {facts['last'][:10]}, legs per rung {facts['legs_per_rung']}"
            )
    print("\nThis file contains real route coordinates. Keep it local.")


if __name__ == "__main__":
    main()
