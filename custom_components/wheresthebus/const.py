"""Constants for the WheresTheBus integration."""
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wheresthebus"

# The parent app talks to a front-door host that 307-redirects the login to a
# per-account shard (e.g. ``https://mdt.wheresthebus.com/sh_05/``).  Every call
# after the login uses the ``basePath`` returned by the login payload.
API_ROOT: Final = "https://mdt.wheresthebus.com/"
API_PATH: Final = "wtbparentapp/api/v2/"

# Values the Flutter web client sends.  The server rejects logins that do not
# look like a known client, so these are sent verbatim.
APP_VERSION: Final = "5.2.2"
DEVICE_TYPE: Final = "FlutterWeb"
DEVICE_OS: Final = "Web_safari_Flutter"

CONF_DEVICE_ID: Final = "device_id"
CONF_BUS_SCAN_INTERVAL: Final = "bus_scan_interval"
CONF_STUDENT_SCAN_INTERVAL: Final = "student_scan_interval"

# ``getRiderInfoEx`` advertises a 15 second refresh.  30 seconds keeps the bus
# marker useful while halving the request rate against a third-party service.
DEFAULT_BUS_SCAN_INTERVAL: Final = 30
MIN_BUS_SCAN_INTERVAL: Final = 15
MAX_BUS_SCAN_INTERVAL: Final = 900

# Roster and ID-scan history change a handful of times a day.
DEFAULT_STUDENT_SCAN_INTERVAL: Final = 300
MIN_STUDENT_SCAN_INTERVAL: Final = 60
MAX_STUDENT_SCAN_INTERVAL: Final = 3600

REQUEST_TIMEOUT: Final = timedelta(seconds=30)

ATTR_BUS_NUMBER: Final = "bus_number"
ATTR_RAW_STATUS: Final = "raw_status"
ATTR_ROUTE_NUMBER: Final = "route_number"
ATTR_SCAN_LOCATION: Final = "scan_location"
ATTR_SCAN_METHOD: Final = "scan_method"
ATTR_SCHOOL_NAME: Final = "school_name"
ATTR_STATUS_COLOR: Final = "status_color"
ATTR_STOP_ADDRESS: Final = "stop_address"
ATTR_STOP_LATITUDE: Final = "stop_latitude"
ATTR_STOP_LONGITUDE: Final = "stop_longitude"
ATTR_STUDENT_ID: Final = "student_id"

SCAN_PICKUP: Final = "pickup"
SCAN_DROPOFF: Final = "dropoff"

# ``stsMsg`` is a human-readable GPS-freshness string ("current", "3 min.
# ago", "inactive").  It is collapsed to these three states so the sensor has
# bounded cardinality instead of changing every single minute.
STATUS_CURRENT: Final = "current"
STATUS_STALE: Final = "stale"
STATUS_INACTIVE: Final = "inactive"
BUS_STATUS_OPTIONS: Final = [STATUS_CURRENT, STATUS_STALE, STATUS_INACTIVE]

# The scan endpoint only ever returns the current day, so scans are
# accumulated locally and persisted to survive both midnight and restarts.
# Each store versions its own schema independently.  Bumping a shared constant
# makes Home Assistant demand a migration for a store whose shape never
# changed, and it raises NotImplementedError rather than carrying on.
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = "wheresthebus_scans"
ARRIVAL_STORAGE_VERSION: Final = 1
ARRIVAL_STORAGE_KEY: Final = "wheresthebus_arrivals"
# Bounded by count rather than by age: it keeps storage small without the
# retained history depending on how long Home Assistant has been running, and
# 50 scans is comfortably more than two weeks of school days.
SCAN_HISTORY_LIMIT: Final = 50

# The bus visits the rider's stop twice a day, and passes it on unrelated
# routes at other times — observed touching the stop at 06:13 for an 07:56
# pickup.  Arrivals are only recognised within this many minutes either side
# of the scheduled stop time, which is what keeps the decoy passes out.
RUN_AM: Final = "am"
RUN_PM: Final = "pm"
RUN_WINDOW_MINUTES: Final = 30

# How close the bus must come for a pass to count as "it stopped here".  A run
# where nobody boards can stay half a mile out, so a loose threshold would
# learn arrivals that never happened.
ARRIVAL_THRESHOLD_MILES: Final = 0.3
ARRIVAL_THRESHOLD_KM: Final = 0.5
# Kept per run, not shared between them: a combined cap lets a rider's two
# daily runs compete for slots, so a stretch of missed afternoons would quietly
# evict the mornings that were still worth learning from. 30 each is roughly
# six school weeks.
ARRIVAL_HISTORY_LIMIT: Final = 30

# Occasionally a bus runs badly late — a breakdown, a substitute driver, a
# closed road.  Those days are real, but they are not the pattern, so they are
# excluded from the prediction while still being kept in history.
#
# The cutoff is the median absolute deviation scaled to a standard deviation
# (x1.4826) at three sigma, which adapts to how tight a given run actually is.
# The floor stops that being over-eager: a run clustered inside two minutes
# would otherwise reject an ordinary five-minute delay as anomalous.
OUTLIER_MAD_MULTIPLIER: Final = 4.45
OUTLIER_FLOOR_MINUTES: Final = 12

# Once the bus is this close the run is under way, and how long the last leg
# takes is far steadier than what time the bus set off.  Measured over four
# mornings, the clock time of arrival varied by 8 minutes while the time from
# this distance to the stop varied by 2.5 — so once the bus crosses it, the
# estimate re-anchors to "crossed at + typical last leg" and stops relying on
# what time it arrived on previous days.
# How far back to replay recorder history when an arrival has no recorded
# final leg yet. The recorder's own retention caps this in practice.
BACKFILL_DAYS: Final = 14

# A ladder of anchors, not one. Each rung records how long the rest of the
# journey took from that distance, and the estimate re-anchors to the tightest
# rung the bus has crossed — so it tightens as the bus closes in, instead of
# ignoring the live position until one fixed threshold is met.
#
# A single 1.0 mile anchor left everything above it running on the clock
# median: on 3 Sep the bus was 2.2 miles out and 6 minutes away while the
# estimate still said 15.
ANCHOR_LADDER_MILES: Final = (3.0, 2.0, 1.0, 0.5)
ANCHOR_LADDER_KM: Final = (4.8, 3.2, 1.6, 0.8)

BASIS_APPROACH: Final = "approach"
BASIS_HISTORICAL: Final = "historical"

ATTR_RUN: Final = "run"
ATTR_PREDICTION_SOURCE: Final = "prediction_source"
ATTR_SAMPLES: Final = "samples"
ATTR_SPREAD_MINUTES: Final = "spread_minutes"
ATTR_OUTLIERS_EXCLUDED: Final = "outliers_excluded"
ATTR_PREDICTION_BASIS: Final = "prediction_basis"
ATTR_SCHEDULED: Final = "scheduled"
SOURCE_LEARNED: Final = "learned"
SOURCE_SCHEDULED: Final = "scheduled"
