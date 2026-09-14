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
ATTR_SUBSTITUTE_BUS: Final = "substitute_bus"
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
# of the run's centre, which is what keeps the decoy passes out.
RUN_AM: Final = "am"
RUN_PM: Final = "pm"
RUN_WINDOW_MINUTES: Final = 30

# Watching the approach has to start well before the arrival window opens.
# The window used to be centred on the timetable, and the afternoon timetable
# here reads 17:48 against a real arrival around 17:20 — so the window opened
# at 17:18, by which time the bus had already crossed the 3, 2 and 1 mile
# rungs.  Every one of them was discarded and the estimate fell back to the
# clock median all afternoon.  Runs are now centred on the LEARNED arrival
# and the approach is watched from this many minutes before it.
APPROACH_LEAD_MINUTES: Final = 45

# A rung crossing only counts while the bus keeps closing.  On 11 Sep the bus
# sat at 2.7 miles at 17:05, drifted back out to 3.8 serving other stops, then
# came in for real at 17:13 — anchoring on the first touch would have been
# eight minutes wrong.  A crossing is discarded once the bus is back outside
# that rung by this factor, which is loose enough to ignore GPS jitter.
RECEDE_HYSTERESIS: Final = 1.15

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
# Bumped whenever what is recorded per arrival changes shape. Stored history
# from an older schema is replayed from the recorder again, because arrivals
# learned under a narrower scheme are not wrong so much as incomplete —
# 1.7.0 shipped a four-rung ladder that would otherwise have sat with only
# the one rung its predecessor recorded, and behaved exactly as before.
ARRIVAL_SCHEMA: Final = 6

# The shape of each approach is kept alongside its timings: a list of
# (seconds before arrival, distance) samples.  The ladder only records four
# instants, which is enough to anchor an estimate and not nearly enough to
# work out why one was wrong — whether the bus crawled the whole way or sat
# still and then sprinted.  Keeping the track means a better model can be
# fitted to journeys already recorded, instead of waiting months to collect
# them again.  Samples closest to the arrival are kept when the cap bites,
# because that is the part of the journey the estimate hangs on.
# Enough to cover a whole ride at the default poll interval, with headroom:
# the afternoon run is around seventy minutes and the cap used to be forty,
# so a route match could not reach back to where the journey began.
TRACK_SAMPLE_LIMIT: Final = 200

# Positions are stored to five decimal places, a little over a metre. Seven
# would triple the size of the store to record GPS noise.
COORD_PRECISION: Final = 5

# How close a past position must be to count as the same place on the route.
# Wide enough to absorb GPS scatter and a bus stopping on either side of a
# road, tight enough that the outbound and homeward passes through the same
# junction stay distinguishable.
ROUTE_MATCH_RADIUS_MILES: Final = 0.25
ROUTE_MATCH_RADIUS_KM: Final = 0.4
MIN_ROUTE_SAMPLES: Final = 2

ANCHOR_LADDER_MILES: Final = (3.0, 2.0, 1.0, 0.5)
ANCHOR_LADDER_KM: Final = (4.8, 3.2, 1.6, 0.8)

# ``gps_age`` reported minutes-since-fix, so it changed every single minute a
# bus was running — 720 recorder rows in three days for a diagnostic nobody
# reads. It is replaced by the instant of the fix, which only moves when a new
# fix actually lands and which Home Assistant renders as "3 minutes ago" by
# itself.
#
# The API reports the age in whole minutes, so the fix instant is only known
# to within a minute: computing it straight from the clock would wobble across
# minute boundaries and churn just as badly. A newly computed instant has to
# differ from the standing one by more than this to replace it, which absorbs
# the quantisation while still moving the moment a real fix arrives.
GPS_FIX_HYSTERESIS: Final = timedelta(seconds=90)

# Retired entities, removed from the registry on setup so they do not linger
# as permanently unavailable rows in the UI.
#
# ``gps_age`` counted minutes since the last fix, so it changed every single
# minute a bus was running — 720 recorder rows in three days. ``last_gps_fix``
# records the instant instead, which Home Assistant renders as "3 minutes ago"
# without writing anything until a new fix lands.
#
# ``eta`` mapped the API's ``etaMsg``, which came back empty on every response
# across every install it was watched on. It never once produced a value.
RETIRED_SENSOR_KEYS: tuple[str, ...] = ("gps_age", "eta")

# Monday to Friday, as weekday() numbers them. Predictions skip days outside
# the run's service days, so a Friday evening does not predict Saturday.
SCHOOL_WEEK: Final = 5
# How far ahead to look for the next run. A Friday evening has to reach
# Monday, and a week covers any holiday the history has taught us about.
DAYS_AHEAD: Final = 8

# The stages of one school run, as one enum on a single sensor.
#
# Ten automation branches used to each work this out for themselves from
# whatever signals were nearest to hand, and four separate faults came out of
# the gaps between them. It is computed once now, in journey.py.
STAGE_IDLE: Final = "idle"
STAGE_TO_STOP: Final = "to_stop"
STAGE_AT_STOP: Final = "at_stop"
STAGE_TO_SCHOOL: Final = "to_school"
STAGE_AT_SCHOOL: Final = "at_school"
STAGE_FROM_SCHOOL: Final = "from_school"
STAGE_TO_HOME: Final = "to_home"
STAGE_HOME: Final = "home"
JOURNEY_STAGES: Final = [
    STAGE_IDLE,
    STAGE_TO_STOP,
    STAGE_AT_STOP,
    STAGE_TO_SCHOOL,
    STAGE_AT_SCHOOL,
    STAGE_FROM_SCHOOL,
    STAGE_TO_HOME,
    STAGE_HOME,
]

# How long "arrived" is worth saying for before the day goes quiet again.
ARRIVED_DWELL_MINUTES: Final = 3

# Local hour dividing the morning run from the afternoon one.
NOON_HOUR: Final = 12

ATTR_STAGE_PROGRESS: Final = "progress"
ATTR_STAGE_TARGET: Final = "target"
ATTR_STAGE_BOARDED: Final = "boarded"
ATTR_JOURNEY_ID: Final = "journey_id"

# How the estimate was arrived at, loosest to tightest.
#
# BASIS_PROGRESS is the one that answers "where is it along the route". Every
# past journey's track records how far out the bus was at each moment, so
# today's distance can be looked up against them: the last time the bus was
# this far out, how long did it still have? That responds continuously to a
# run going quickly - a stop skipped because nobody was aboard - where the
# rung ladder only re-anchors at four fixed distances and the clock median
# does not respond at all.
# Where the bus is on the ROUTE, matched against where past journeys
# physically were. This is the only basis that survives a bus driving away
# from the stop, which it does constantly: it serves other children, turns
# round in cul-de-sacs, and doubles back. Straight-line distance reads all of
# that as setbacks, so an estimate built on it slides forward with the clock
# and never converges.
BASIS_ROUTE: Final = "route"
BASIS_PROGRESS: Final = "progress"
BASIS_APPROACH: Final = "approach"
BASIS_HISTORICAL: Final = "historical"

ATTR_RUN: Final = "run"
ATTR_PREDICTION_SOURCE: Final = "prediction_source"
ATTR_SAMPLES: Final = "samples"
ATTR_SPREAD_MINUTES: Final = "spread_minutes"
ATTR_OUTLIERS_EXCLUDED: Final = "outliers_excluded"
ATTR_PREDICTION_BASIS: Final = "prediction_basis"
ATTR_RIDE_MINUTES: Final = "typical_ride_minutes"
ATTR_SCHEDULED: Final = "scheduled"
# Which rung the live estimate is hanging on, and how many past journeys back
# it.  Without these an anchored estimate and a clock-median one look alike,
# and there is no way to tell a rung with one sample from a rung with twenty.
ATTR_ANCHOR_DISTANCE: Final = "anchored_at"
ATTR_ANCHOR_SAMPLES: Final = "anchor_samples"
ATTR_WINDOW_CENTRE: Final = "window_centre"
# How many past journeys were far enough out to say anything about this
# distance. Two is enough to take a median of; one is an anecdote.
ATTR_PROGRESS_SAMPLES: Final = "progress_samples"
ATTR_ROUTE_SAMPLES: Final = "route_samples"
# The observed range this arrival has fallen in, judged the same way as the
# estimate. Not a statistical interval — off a handful of journeys the honest
# thing to show is the range actually seen. It closes towards nothing as the
# bus nears the stop, because what is left to vary is the part of the journey
# still to run.
ATTR_EARLIEST: Final = "earliest"
ATTR_LATEST: Final = "latest"
ATTR_UNCERTAINTY: Final = "uncertainty_minutes"
MIN_PROGRESS_SAMPLES: Final = 2
SOURCE_LEARNED: Final = "learned"
SOURCE_SCHEDULED: Final = "scheduled"
