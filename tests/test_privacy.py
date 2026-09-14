"""Guard the repository against publishing where the rider actually goes.

This is not a hypothetical. Real recorded positions have twice reached the
public history of this project: a whole afternoon's GPS track committed as a
backtest fixture, and the home stop itself written into two test files as the
point the fixtures were built around.

Both were argued for at the time. The track was translated to a fictional
origin, which was presented as anonymisation and is not: moving a route keeps
every distance and bearing, and the shape is the identifier. The stop was a
bare pair of numbers with no name attached, which is worse, because it needs
no route-matching at all — it can be pasted straight into a map.

So the rule is mechanical rather than a matter of judgement, because judgement
is exactly what failed: coordinates in this repository must lie inside the
fictional town that tests/fixtures.py invents. Nowhere else, for any reason.
Recorded journeys stay out of the tree entirely and are gitignored.
"""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The fictional town, with room to spare. Every coordinate that ships must be
# in here.
LAT_RANGE = (40.70, 40.76)
LON_RANGE = (-74.06, -73.98)

SCANNED = (".py", ".md", ".yaml", ".yml", ".json", ".toml")

# Numbers that merely look like coordinates. Listed by exact literal so a new
# one has to be added deliberately rather than slipping under a range.
NOT_COORDINATES = {
    "1.4826",  # consistency constant turning a MAD into a std deviation
    "1.609344",  # kilometres per mile
}

# Four decimal places is roughly eleven metres — precise enough to be a place
# rather than a rounded constant.
PRECISE = re.compile(r"-?\d+\.\d{4,}")


def _ls_files() -> list[str]:
    """Every file git is tracking, which is exactly what gets published."""
    git = shutil.which("git")
    assert git, "git is needed to tell what this repository publishes"
    out = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [name for name in out.stdout.split("\0") if name]


def _scanned() -> list[Path]:
    return [REPO / name for name in _ls_files() if name.endswith(SCANNED)]


def test_no_coordinate_outside_the_fictional_town_is_committed() -> None:
    """Any precise number in coordinate range must be an invented one."""
    strays: list[str] = []

    for path in _scanned():
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for literal in PRECISE.findall(line):
                if literal in NOT_COORDINATES:
                    continue
                value = float(literal)
                if not 1.0 <= abs(value) <= 180.0:
                    continue  # too large or too small to be a position
                in_lat = LAT_RANGE[0] <= value <= LAT_RANGE[1]
                in_lon = LON_RANGE[0] <= value <= LON_RANGE[1]
                if not (in_lat or in_lon):
                    rel = path.relative_to(REPO)
                    strays.append(f"{rel}:{line_no}: {literal}")

    assert not strays, (
        "coordinates outside the fictional town are committed — these may be "
        "real positions and must not be published:\n  " + "\n  ".join(strays)
    )


def test_recorded_journeys_are_ignored_rather_than_tracked() -> None:
    """The directory real journeys land in must never be in the tree."""
    tracked = _ls_files()

    assert not [name for name in tracked if "tests/journeys/" in name]
    assert not [name for name in tracked if name.endswith(".journey.json")]

    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "tests/journeys/" in ignore
    assert "*.journey.json" in ignore
