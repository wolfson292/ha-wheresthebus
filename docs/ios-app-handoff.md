# Handoff: a standalone iOS school-bus app

<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

You are being asked to build a native iOS app that talks to the WheresTheBus
parent API directly, makes its own arrival predictions, keeps its own history,
and drives its own Live Activity — replacing a Home Assistant integration that
does all of this today.

This document is the accumulated result of that integration running daily
against one real school bus from 26 August to 18 September 2026. The
prediction algorithm in it was wrong in a dozen specific ways before it was
right, and every one of those ways is written down here. **The failure
catalogue near the end is the most valuable part of this document.** It is
cheaper to read it than to rediscover it, and rediscovering it means a child
standing at a kerb after the bus has gone.

Reference implementation: `github.com/wolfson292/ha-wheresthebus` (GPL-3.0).
Where this document and that code disagree, the code is right — but read the
docstrings, which carry the reasoning rather than the mechanics.

---

## 1. What already works, and what you are replacing

The Home Assistant integration polls the API every 30 seconds, matches the
bus's live position against GPS tracks of past journeys, and predicts arrival
at one specific stop. Measured accuracy on a recorded afternoon run:
**worst case 1.19 minutes out in the last 5 minutes** of the journey, and a
sub-minute typical error once route matching engages.

Three things pushed this toward a native app:

1. **iOS Live Activities are second-class through Home Assistant.** A push
   from HA needs a valid push-to-start token, and those go stale silently
   (see §9). A native app owns ActivityKit directly and simply does not have
   this problem. This is the single biggest reason to do the rewrite.
2. **Polling cadence.** The afternoon boarding window wants 30-second
   resolution; HA's setup made that awkward to vary.
3. **Everything lives in one place** — no automations, no templates, no
   entity plumbing between the model and the display.

What you lose, and must consciously replace: Home Assistant runs continuously.
**An iOS app does not.** See §10, which is the hardest problem in this project
and the one most likely to be underestimated.

---

## 2. The WheresTheBus API

Undocumented, reverse-engineered from the Flutter web client. All calls are
`POST` with a JSON body. Responses are JSON but **do not always set a JSON
content type** — force-decode rather than trusting the header.

### Hosts and auth

```
Front door : https://mdt.wheresthebus.com/
Path prefix: wtbparentapp/api/v2/
```

`POST {root}{path}login` with:

```json
{
  "emailId": "...", "password": "...",
  "imeiNo": "<stable per-install device id you invent>",
  "deviceType": "FlutterWeb", "sso": 0, "deviceOS": "Web_safari_Flutter"
}
```

- The server **rejects logins that do not look like a known client**, so send
  `deviceType` / `deviceOS` / `versionInstalled` verbatim. (`APP_VERSION` is
  `5.2.2` at time of writing.) If you change these to something iOS-shaped,
  test that login still works before relying on it.
- Login **307-redirects to a per-account shard** (e.g. `.../sh_05/`). The HTTP
  client must replay the POST body against the redirect target. `URLSession`
  does this for 307 by default — verify, don't assume.
- The response payload carries `sessionId`, `basePath`, `shardId`,
  `firstName`, `lastName`. **Every later call uses `basePath`, not the front
  door.**

Authenticated calls put the session in *both* the query string and the body:

```
POST {basePath}{path}{endpoint}?sessionId={sid}
body: { ...args, "sessionId": sid }
```

Every response has a `resCode`; `0` means success and anything else is usually
a dead session. **Re-login once and retry, then give up** — the reference
client does exactly this and it is what makes long-running polling survive.
A `401` should be treated as an auth failure immediately.

### Endpoints

| Endpoint | Args | Returns |
|---|---|---|
| `getUserInfo` | `imeiNo`, `versionInstalled`, `tokenId: ""`, `deviceNotif: true` | Account settings + `childBuses` |
| `getAllRiders` | — | `allRiders`: roster with AM/PM stop details |
| `getRiderInfoEx` | `bid` (bus no.), `chdId` (child id), `lastServerTime` | Live position + distance for one child |
| `getStudentScan` | — | Recent badge-scan events (**today only**) |

Send `tokenId: ""` on `getUserInfo`. A non-empty token registers *this* client
as a push target and will steal notifications from the family's real phones.
For a native app that genuinely wants the vendor's pushes this is worth
revisiting — but do it deliberately, and know that it changes behaviour for
every device on the account.

### `getRiderInfoEx` — the one that matters

Polled every 30 seconds. The API advertises a 15-second refresh; 30 keeps the
marker useful at half the request rate against somebody else's service. Be a
good citizen here.

Key fields: `busLat`, `busLon`, `dist` (miles or km per `isDistKm`),
`stpLat`/`stpLon` (the rider's stop), `homLat`/`homLon`, `schLat`/`schLon`,
`stsMsg`, `serverTime`, `curr_seq`, `isDistKm`.

Feed `serverTime` back as `lastServerTime` to get only new breadcrumb points.

**`stsMsg` is the GPS freshness string and it is critical.** It is written for
humans — `"current"`, `"3 min. ago"`, `"12 mins ago"`, `"inactive"` — and it
changes every minute while a bus runs. Parse it into a bounded state plus an
age in minutes:

```
starts with "current"  -> (current,  age 0)
starts with "inactive" -> (inactive, age unknown)
matches /(\d+)\s*min/  -> (stale,    age = that number)
anything else          -> (unknown,  age unknown)   # do not crash on new vocabulary
```

**A stale reading is the last known position repeated, not a new one.** This
single fact is responsible for two separate bugs in the reference
implementation (§11.2, §11.3). Treat `stale` as "I know where it was N minutes
ago", never as "I know where it is".

### Scans

`getStudentScan` returns bare "ID received" events with a location and time —
**it does not say whether the rider got on or off.** Infer it:

- Scan location matches the school → drop-off in the morning, pickup in the
  afternoon.
- Scan anywhere else (i.e. the neighbourhood stop) → the reverse.
- School name unknown or no match → alternate by position within the day
  (a normal day is pickup, drop-off, pickup, drop-off).

Normalise both names to lowercase alphanumerics before comparing, and accept a
substring match either way round.

**The endpoint only ever returns the current day.** Accumulate scans locally
and persist them, or you lose all history at midnight.

Observed scan → API visibility lag: **about 5 minutes 28 seconds.** Do not
design anything that needs the scan to be instant.

---

## 3. The prediction algorithm

Three bases, tried strictly in this order. The first that applies wins.

```
1. route      — where the bus is along the route, matched against past journeys
2. approach   — an anchor-ladder rung crossed today, plus the typical leg from it
3. historical — the median clock time this run has actually arrived
                (falling back to the published timetable when nothing is learned)
```

Before any of them: **ask once whether this run has already happened today**,
and if so, skip it entirely. This was fixed once in the historical branch
alone and came straight back through the route branch the moment that was
added — on 17 Sep the bus reached the stop at 08:04 and the estimate went on
predicting the morning arrival for five more minutes, drifting later each
poll (08:05:15, 08:07:44, 08:09:46). Ask in one place that all three share.

### 3.1 Route matching — the good one

The insight that makes this work: **distance to the stop is a lossy projection
of a school run.** The bus is not driving toward the stop, it is driving a
route — serving other children, turning in cul-de-sacs, and often moving
directly away from the stop while making perfect progress. Measured as
straight-line distance a U-turn looks like a setback, and an estimate built on
it slides forward with the clock and never converges. Observed on 14 Sep:
twelve minutes later over twelve minutes while the bus worked stops four miles
out.

The route, though, is nearly the same every day. So the question is not "how
far is the bus from the stop" but **"where on the route is it, and how long did
that take last time."**

Each past journey is stored as a track of `(seconds_before_arrival, lat, lon)`,
oldest first, sampled every ~30 s. To predict:

1. For each past track, and for each **segment** between consecutive fixes,
   project today's position onto that segment and interpolate the remaining
   time between the two endpoints.
2. Discard segments further than `ROUTE_MATCH_RADIUS` (0.25 mi) from the
   query.
3. Apply the heading filter (§3.2).
4. Rank matches by `(gap, drift, age)` — **distance first**, elapsed time only
   as a tie-break.
5. Apply the ambiguity guard (§3.3).
6. Take the minimum, giving one "seconds remaining" per past journey.
7. Across journeys: MAD outlier rejection, then **median** (see §3.6 — the
   median of two is the midpoint, not the later value).
8. Arrival = **when the fix was taken** + median remaining. *Not* `now` — see
   §11.2.

**Project onto segments, not onto sample points.** Sampling every 30 s is a
quarter of a mile at road speed. Matching to the nearest recorded *point*
quantises the estimate to that spacing — the bus creeps the whole way between
two fixes and the answer does not move, then jumps — and the nearest point can
be the one *behind* the bus. Measured at the midpoints of legs the bus actually
drove:

| | mean error | worst |
|---|---|---|
| nearest-sample | 0.67 min | **3.77 min** |
| point-to-segment | <0.01 min | **0.01 min** |

Point-to-segment projection, with `scale = cos(mean latitude)` to make degrees
locally square:

```
run  = (end - start)                      # in scaled lon, raw lat
t    = clamp(dot(point - start, run) / |run|², 0, 1)
meet = start + t * run
gap  = haversine(point, meet)
age  = start_age + t * (end_age - start_age)
```

A track with a single sample has no segment; fall back to a plain point match.

### 3.2 Heading — how to tell the two passes of a crossing apart

A route crosses itself. The same junction is driven outbound and homeward, and
the two want different answers — often 90 seconds apart at the very same spot.

Compute the bus's current heading from its recent fixes, and each past sample's
heading from the fix before it. **Drop past samples whose heading differs by
more than 90°.**

- Require at least `0.02 mi` of movement before trusting a bearing. A bus
  idling at a stop jitters a few metres and the bearing of jitter is noise
  pointing in a random direction.
- Take the heading from the last fix that genuinely *moved*, not the last fix.
- **A sample with no heading of its own is a bus that was standing still
  there.** That is a real place on the route and keeps its claim. Only refuse
  a sample *known* to be going the other way.

Direction beats elapsed time at this job because it owes nothing to the clock.
Elapsed compares today's running time against a past journey's, which assumes
today is going roughly like that journey did — exactly the assumption the
estimate exists to test. A bus ten minutes down drifts against every past
sample equally and the tie-break stops discriminating at the moment it matters
most.

### 3.3 The ambiguity guard

If the matched ages for one track span more than **300 seconds**, return
nothing for that track.

If a place meant wildly different things at different times of a past journey,
it does not locate today's. Answering anyway is worse than not answering: the
caller has a historical estimate that is honestly vague, and replacing it with
a confident wrong number is how a parked bus came out **31 minutes adrift** on
15 Sep.

### 3.4 The anchor ladder (basis 2)

When route matching declines, fall back to rung crossings. Rungs at
`3.0, 2.0, 1.0, 0.5` miles. For each past arrival, record how many seconds
elapsed from crossing each rung to reaching the stop. Today, re-anchor to the
**tightest rung the bus has actually been watched crossing**:

```
estimate = crossed_at + median(legs for that rung, outliers rejected)
```

This tightens as the bus closes in, instead of ignoring live position until one
fixed threshold is met. A single 1.0-mile anchor left everything above it
running on the clock median: on 3 Sep the bus was 2.2 miles out and 6 minutes
away while the estimate still said 15.

Rules that took real failures to learn:

- **A crossing needs the previous reading to have been genuinely outside** —
  `previous > threshold × 1.02`. The depot sits at *exactly* 3.0 miles, so a
  bare "was above, now at or below" is eventually satisfied by GPS jitter while
  the bus stands still, and the recede-hysteresis below can never undo it
  because a few metres of wobble never reaches 3.45.
- **A crossing is discarded if the bus goes back outside** that rung by
  ×1.15 without ever arriving. On 11 Sep the bus sat at 2.7 mi, drifted back
  out to 3.8 serving other stops, then came in for real eight minutes later.
- **A rung the bus was already inside when watching began gets no crossing at
  all.** On 14 Sep the bus was parked at exactly 3.0 mi from 06:58; the first
  reading after the window opened at 07:16 was read as "just crossed the 3-mile
  rung", the typical 9½-minute leg was hung off it, 07:25 was predicted for a
  bus that came at 08:01, and the five-minute warning went out at 07:20.
- A stale reading **cannot time a crossing.** When the feed thaws the bus has
  already moved; stamping the rung at the thaw records a leg far shorter than
  the bus actually took. Clear the previous-distance on staleness.

### 3.5 Historical (basis 3)

Median of the local clock times this run has actually arrived, outliers
rejected. Falls back to the published timetable when nothing is learned yet.

**The published timetable can be badly wrong.** The afternoon one here reads
17:48 against a real arrival around 17:20. Everything downstream — run
windows, approach windows — must be positioned relative to the *learned*
centre, not the timetable, or the windows open after the bus has already gone.

Look up to **8 days ahead** when choosing the next run, so a Friday evening
reaches Monday. Service days are weekdays *plus* any day an arrival has
actually been observed — learned rather than hard-coded, so a route that
genuinely runs at weekends keeps working, while a rider who happens not to have
ridden on a Wednesday yet does not lose Wednesdays.

### 3.6 Outlier rejection and the median

```
threshold = max(4.45 × MAD, floor)      # 4.45 ≈ 3σ via the 1.4826 MAD scaling
floor     = 12 minutes for clock times, or the same in seconds for remainders
```

Fewer than 3 samples: keep everything — two arrivals cannot tell you which is
the anomaly. Never discard everything, however strange the data looks. Keep
excluded days in history; exclude them only from the calculation.

**The median of an even-length list is the midpoint of the middle pair, not
the upper one.** With a two-sample minimum that is the common case, and taking
the upper value biased every route estimate late by however far the two
journeys disagreed.

### 3.7 Steadying the published answer

The route match is accurate *and* noisy: it re-answers from wherever the bus is
now, and that moves by whole minutes between polls. Measured on 16 Sep the
target swung across twelve minutes (17:18–17:30), changing every 30–60 seconds,
while the bus arrived within a minute of where the median had sat the whole
time. Publishing every wobble made the display jitter and drove **25
notification pushes in 20 minutes**.

**Hold the published arrival while it stays inside the current estimate band.**
Republish when it falls outside. Use a flat 120-second tolerance only when
there is no band, with a 30-second floor below which nothing is worth
republishing.

Do **not** move the band to cover the held value. The band is your statement of
what you know; dragging it to agree with a held number makes it say something
you have not measured.

Key the hold per `(rider, run, date)`. A shared key lets one rider's wobble pin
another's.

Floor the band width at 30 seconds. On 17 Sep a two-sample match reported
earliest and latest as the same instant — uncertainty of zero, from a sample of
two.

---

## 4. Recording an arrival

Arrival = the **first** reading within `0.3 mi` of the stop, inside the run
window. Not the closest reading: a bus dwelling at the stop gives two or three
readings under the threshold, and "closest" lands on whichever poll had the
best fix, dating the arrival — and every leg measured back from it — by a poll
of noise.

Per arrival, store: run (`am`/`pm`), arrival instant, closest distance, the
per-rung legs, a substitute-bus flag, the GPS track, a count of recedes, a
count of stale readings, the boarding time, and whether it was replayed from
history rather than watched live.

- Track points are `[seconds_before_arrival, lat, lon]`, oldest first, capped
  at **200** (about 100 minutes at 30 s, comfortably more than the ~70-minute
  afternoon run). When the cap bites, **keep the samples nearest the arrival**
  — that is the part the estimate hangs on.
- Coordinates to **5 decimal places** (~1 m). Seven triples the storage to
  record GPS noise.
- **Never append a stale fix to a track.** Sixty identical points across a
  half-hour freeze made every later journey through that spot match a block of
  ages 30 minutes wide, which the ambiguity guard then refused outright. The
  contamination and the guard against it were both self-inflicted.
- Keep **30 arrivals per run**, counted per run rather than shared. A combined
  cap lets the two daily runs compete, so a stretch of missed afternoons
  quietly evicts mornings that were still worth learning from.
- A substitute bus keeps its own time. Record it so the arrival stays visible,
  but hold it out of what the estimate learns from.

**Run windows.** The bus passes the stop on unrelated routes at other times —
observed touching the stop at 06:13 for an 07:56 pickup. Only recognise an
arrival within **±30 minutes** of the run's learned centre. Watch the approach
from **45 minutes** before that centre, so the outer rungs are seen at all.

---

## 5. The journey stage machine

One enum, computed in one place. This started as ten independent branches each
working out for itself whether a journey was under way, and four separate
faults came out of the gaps between them: a progress bar that filled for two
hours after the rider reached school, a bar lurching between 78 % and 7 %, a
flickering title and colour, and a countdown that ran 61 hours to the following
Monday.

```
idle | to_stop | at_stop | to_school | at_school | from_school | home
```

Evaluated in this order — **ordering is the whole design.** Having arrived
somewhere outranks being on the way there; being aboard outranks the bus merely
being nearby.

0. **The ride home is over, and stays over.** An afternoon arrival is terminal
   for the day.
1. **Just scanned off** (within 3 minutes) → `at_school` if the drop-off was in
   the morning, `home` if the afternoon. Reading every drop-off as the school
   announced "Arrived at school — dropped off safely" as the rider stepped off
   the bus outside the house.
2. **Bus at the stop**, approach window open → `home` in the afternoon,
   `at_stop` in the morning. Decide *which run this is* from the clock, not
   from which run is predicted next: those differ the moment the bus arrives,
   because the prediction rolls straight on to the afternoon.
3. **Aboard** (pickup scan today, no later drop-off) → `to_school` /
   `from_school`, progress against **elapsed time**, because distance to the
   home stop says nothing while the bus works its route. The target must be
   *today* — once the journey ends the predictions roll to the next school day,
   and a bar filling toward a target three days out is never right.
4. **Approach, morning only** → `to_stop`, progress by distance closed.
5. Otherwise `idle`.

**Rule 4 is morning-only and that asymmetry is the point.** In the morning
there cannot be a scan yet, because the scan happens on boarding. In the
afternoon there can be, and its absence means the rider is not on the bus. An
afternoon approach that fired on the clock alone showed, on 15 Sep, a bus
coming home that was never coming — the rider did not ride, the bus ran a
nearby route anyway, the window opened at 16:36, and the stage then flapped in
and out of idle as the estimate slid past. Three journeys in seventy minutes.

Give each journey a stable id — `YYYYMMDD-am` / `YYYYMMDD-pm`, from the clock
— and use it as the Live Activity identity. Derive `am`/`pm` from the clock
rather than from the prediction's run attribute, which flips to the afternoon
the instant the morning pickup passes and would rename a journey halfway
through it.

---

## 6. Did the rider actually board?

Boarding is normally a fact: the rider scans a badge. But the scan was missed
three times in the first nine school days, and a missed scan used to cost the
whole afternoon's notification.

The phone answers the same question independently. Two positions: where it was
while the bus loaded at school, and where it is once the bus has left.

```
aboard = travelled(origin → now) ≥ 0.5 mi   AND   distance(phone, bus) ≤ 0.25 mi
```

**Both halves are required.** Distance travelled alone cannot tell a bus from a
lift home in a car. Proximity alone cannot tell riding from standing in a
school car park while the bus loads twenty metres away — which is exactly the
moment the question is asked, so proximity alone reads as aboard every single
afternoon.

A missing fix is **not** an answer of "no". It has not been shown that they are
on the bus, but it has not been shown they are at school either; fall back to
the scan.

On a native iOS app this is dramatically easier than it was in Home Assistant —
you have CoreLocation directly, and the rider's device may be in the same
family sharing group. Consider significant-location-change monitoring rather
than forced polling.

---

## 7. What to show

Sensors the integration exposes, as a checklist of what turned out to be worth
surfacing: next arrival (with `earliest`/`latest` band, basis, sample count,
spread, outliers rejected), distance to stop, bus status, journey stage +
progress + target, last GPS fix instant, school arrival estimate, last scan,
last pickup, last drop-off, morning/afternoon scheduled times, bus number, and
a map marker for the bus.

Two display lessons:

- **Lead with the time, not the distance.** A watch truncates hard, and the
  distance is already drawn as the progress bar — "4.5 miles from…" spent the
  whole visible line restating the bar.
- **Report the fix instant, not its age.** An age in minutes changes every
  single minute a bus is running; the OS renders "3 minutes ago" from an
  instant by itself. The API reports age in whole minutes, so apply ~90 s of
  hysteresis before replacing a standing fix instant, or it wobbles across
  minute boundaries and churns anyway.

---

## 8. Tuning constants

All distances in miles (the integration carries km equivalents).

| Constant | Value | Why |
|---|---|---|
| Bus poll interval | 30 s | API advertises 15; 30 halves load on a third party |
| Scan poll interval | 300 s | Too slow around afternoon loading — see §12 |
| Arrival threshold | 0.3 mi | A run where nobody boards can stay ½ mi out |
| Run window | ±30 min | Keeps decoy passes out |
| Approach lead | 45 min | Outer rungs must be seen |
| Anchor ladder | 3.0 / 2.0 / 1.0 / 0.5 mi | Re-anchors as the bus closes |
| Crossing margin | ×1.02 | Jitter at exactly the rung is not a crossing |
| Recede hysteresis | ×1.15 | Loose enough to ignore GPS jitter |
| Route match radius | 0.25 mi | Absorbs scatter, keeps the two passes distinct |
| Min route samples | 2 | Below this, fall through to the ladder |
| Heading tolerance | 90° | Splits onward from back-the-way-it-came |
| Heading min movement | 0.02 mi | Below this a bearing is noise |
| Ambiguity spread | 300 s | Refuse rather than answer confidently wrong |
| Arrival hysteresis | 120 s / 30 s floor | Only when there is no band |
| Route band floor | 30 s | Two agreeing journeys are not certainty |
| MAD multiplier | 4.45 | ≈3σ via the 1.4826 scaling |
| Outlier floor | 12 min | Stops over-eager rejection on a tight run |
| Track sample cap | 200 | Covers a ~70-minute run with headroom |
| Coordinate precision | 5 dp | ~1 m; 7 dp records noise |
| Arrival history | 30 per run | ~6 school weeks |
| Aboard: moved / together | 0.5 / 0.25 mi | Both required |
| Arrived dwell | 3 min | How long "arrived" is worth saying |

---

## 9. Live Activities — read this before designing notifications

This cost more debugging time than the entire prediction algorithm.

**Through Home Assistant**, Live Activities need a push-to-start token from
APNs. What was learned the hard way:

- Push-to-start **does not work when the app is closed**
  ([home-assistant/iOS#5766](https://github.com/home-assistant/iOS/issues/5766)).
- On **iOS 27 they broke more widely**
  ([home-assistant/iOS#5812](https://github.com/home-assistant/iOS/issues/5812)):
  push-to-start silently fails from scripts and automations while the app's own
  sample activities still work. The token **goes stale on Apple's side with no
  signal.** The community workaround is to toggle the Live Activities
  permission off and on to force re-registration; it works for some and not
  others. Both issues were open as of 18 Sep 2026.
- An app **update** also kills the token.
- Failures are **completely silent** — no error, no log line, a clean
  automation trace, and nothing on the phone.

**A native app largely sidesteps all of this**, because it can start an
activity locally with `Activity.request(...)` while in the foreground, and
update it locally while running. That is the main prize of this rewrite. You
will still need push updates for activities that must change while the app is
suspended (§10), but a *locally started* activity updated by push is a far more
reliable path than push-to-start.

Design rules that survive the move, learned from real misbehaviour:

- **Only send a stage change as time-sensitive.** A periodic refresh goes out
  passive. Repeating what the activity already shows is not worth a buzz on the
  wrist every few minutes — this was a real complaint.
- **Re-push on stage change, when the target moves, and on a short periodic
  backstop.** A backstop alone is not enough: the chronometer ticks on the
  phone without a push, so if the target moves and nothing says so, the card
  counts down to the old time.
- **Round the target to the minute** before using it as a re-push trigger. Raw,
  it carries microseconds and moves every poll, so watching it pushes every 30
  seconds to say the same thing.
- **Ending an activity is expensive** when starts are scarce; a native app
  changes this calculus, but still avoid ending and restarting across one
  journey. Tag the activity with the journey id so a new journey is a new
  activity and a failed start leaves nothing wedged.
- iOS retires an activity roughly **8 hours** after its last update, which is
  why a morning card is gone before the afternoon bus leaves school.

---

## 10. The hard problem: iOS background execution

**Home Assistant polls every 30 seconds forever. Your app will not be
running.** This is the single largest design risk in the rewrite and it is not
solved by anything in this document.

The prediction algorithm needs a position every ~30 seconds during a ~20-minute
approach window, twice a school day. iOS gives you, roughly:

- **Foreground** — full rate, but only while the user is looking.
- **`BGAppRefreshTask`** — opportunistic, minutes-to-hours apart, no guarantees.
  Nowhere near 30 s.
- **Location background mode** — continuous, but you must genuinely be using
  location, and you must justify it to App Review. Significant-location-change
  and region monitoring around the stop are plausible and honest uses here.
- **Silent push (`content-available`)** — throttled by iOS, not guaranteed, and
  needs a server to send them.
- **Live Activity push updates** — the activity can be updated by APNs while
  the app is suspended. This is the most promising channel for the actual
  display, and it also needs a server.

Realistically you need **a small always-on component** that polls WheresTheBus,
runs the prediction, and pushes Live Activity updates to the device. That could
be a tiny cloud worker or something on the home network. Decide this early — it
changes the architecture, the credential-handling story, and the privacy story.

If you build a server component, note that it will hold the family's
WheresTheBus password, and it will know a child's live location. Treat both
accordingly: the credential belongs in the Keychain on-device and in a secret
manager server-side, never in source or a config file in a repo.

---

## 11. The failure catalogue

Ten real bugs, each of which shipped, with what they cost. **Read these as
tests to write, not as anecdotes.**

**11.1 The estimate that slid with the clock.** A distance-indexed estimate
recomputed `now + typical_remaining` every poll, so while the bus worked stops
without closing, it moved later exactly as fast as the clock and never
converged — "about twenty minutes away" for twelve minutes straight. Fixed by
matching position instead of distance.

**11.2 …and it came back through a different door.** The route matcher read the
live position without checking `stsMsg`. During a feed freeze it matched the
same frozen position every poll, got the same remaining time, and published it
as that many minutes from a clock that kept advancing. A six-minute freeze
walked the arrival six minutes later, then snapped back on thaw. **Anchor the
estimate to when the fix was taken** (`now − gps_age`), not to `now`. Note the
direction of the error: pushing the arrival *later* is what leaves a child on
the kerb after the bus has gone.

**11.3 Stale fixes contaminating the track** — §4. Sixty identical points made
later journeys unmatchable.

**11.4 The phantom approach.** 15 Sep: the rider did not ride, the bus ran a
nearby route, and the afternoon opened on the clock alone. The stage flapped,
three Live Activities were started and cleared in seventy minutes, and the next
morning's push budget was gone. Fixed by requiring a scan for the afternoon
(§5, rule 4).

**11.5 The 31-minute parked bus** — the ambiguity guard, §3.3.

**11.6 Jitter at the depot** — the crossing margin, §3.4.

**11.7 The rung nobody watched being crossed** — §3.4, predicted 07:25 for a
bus that came at 08:01.

**11.8 Arrival dated by the closest reading instead of the first** — §4.

**11.9 The median of two** — §3.6. Biased every two-sample estimate late.

**11.10 Tests that could not fail.** Four tests shipped green that passed
identically with the mechanism they named removed — including a privacy guard
that scanned only tracked files and so could not see the file just written, and
a backtest that scored each journey against a history consisting of that same
journey (every answer right by construction, 0.01 min error, and the assertion
that the error was smaller near the stop compared 0.01 against 0.02).

**Before trusting any test, run it against the code with the mechanism
removed and confirm it fails.** If it cannot be made to fail, it is asserting
something the old code already did.

---

## 12. Known-open work

Carried over; none of it is blocking.

- **Adaptive polling.** 5-minute scan polling is too slow around afternoon
  loading. Designed, not built. Worth investigating how the vendor's own app
  gets push notifications — if it registers a real `tokenId`, a native app
  could too, and stop polling for scans entirely.
- **Traffic.** Deliberately *not* modelled from an external API. Typical
  traffic is already implicit in the median over past journeys, and the missing
  piece is only today's deviation. The recommended approach is an endogenous
  "pace ratio": compare actual elapsed time over the stretch just driven
  against what history took over the same stretch. It absorbs traffic, weather,
  a substitute driver and a heavy load at once, with no new dependency.
  Constraints if you build it: per-track then median across tracks (never
  difference the aggregate); a 5–10 minute window with a robust slope, not two
  30-second endpoints; cap around [0.85, 1.25]; horizon-additive rather than
  fully multiplicative; freshness-gated; and apply it **asymmetrically** — a
  too-late estimate makes the rider miss the bus, a too-early one costs a few
  minutes on the kerb, so the slow side should widen the upper bound only.
  An external traffic API was rejected mainly because **it cannot be
  backtested** against journeys already recorded.
- **Recency weighting** was considered and rejected *for now*: the first two
  weeks of term are the least representative data of the year, so weighting
  recent data most currently weights the worst data most. Revisit with a term
  of data. Note the real seasonal effects are step changes (clubs starting, a
  stop added), which exponential decay handles badly.

---

## 13. The history export

Run this against a copy of the Home Assistant store file:

```bash
python3 scripts/export_history.py ~/wtb-arrivals.json --store ~/wheresthebus_arrivals
```

The store lives at `<ha-config>/.storage/wheresthebus_arrivals`. Get a copy via
the Samba, File Editor, or Terminal add-on, or out of a full backup. The script
does not talk to Home Assistant or to the API — it is a pure file conversion.

### Bundle shape

```json
{
  "export_version": 1,
  "exported_at": "2026-09-18T07:40:00-04:00",
  "source": "home-assistant custom_components/wheresthebus",
  "arrival_schema": 7,
  "units": "miles",
  "anchor_ladder": [3.0, 2.0, 1.0, 0.5],
  "track_point_format": ["seconds_before_arrival", "latitude", "longitude"],
  "riders": [
    {
      "child_id": "<api child id>",
      "summary": {
        "am": {
          "arrivals": 15, "with_track": 15, "substitutes": 0,
          "first": "...", "last": "...",
          "median_local_minute": 481,
          "legs_per_rung": {"3.0": 15, "2.0": 15, "1.0": 15, "0.5": 15}
        },
        "pm": { }
      },
      "arrivals": [
        {
          "run": "am",
          "arrival": "2026-09-17T12:04:34+00:00",
          "closest": 0.04,
          "legs": {"0": 570, "1": 420, "2": 240, "3": 95},
          "substitute": false,
          "track": [[1200, 40.73501, -74.02003], [1170, 40.73381, -74.01913]],
          "recedes": 0, "stale": 2,
          "boarded": null, "replayed": false
        }
      ]
    }
  ]
}
```

- `legs` keys are **indices into `anchor_ladder`**, values are seconds from
  crossing that rung to arriving.
- `track` is what route matching runs against — the most valuable field here.
- `summary` exists so you can sanity-check the import: if `with_track` is much
  lower than `arrivals`, most of the history predates track recording and only
  the ladder and clock bases will benefit.

**This file contains real route coordinates.** A few miles of turns can be
matched against a road network and put back on the map, and it is a child's
daily route to and from school. Keep it on-device and local; never commit it,
never put it in a bug report, never upload it to a service. The export script
refuses to write inside the source repository for this reason.

### Loading it

Import every arrival as-is. Predictions work from **2** route samples and 3
arrivals for outlier rejection, so a fortnight of history makes the app useful
on day one rather than in October.

---

## 14. Testing

The reference repo has 183 tests plus backtests that replay real recorded
journeys. Two things worth carrying over:

**Backtest against recorded journeys, and query between the fixes.** Scoring a
journey against its own track only asks about points the recorder already
holds, where any model that finds the right sample is exactly right — both a
good matcher and a bad one score 0.01 min. The bus spends nearly all its time
*between* those points. Score at the midpoints of the legs it actually drove.

**Falsify every test** (§11.10).

Keep recorded journeys outside the source tree. The reference repo reads them
from a path given by an environment variable so they never enter the
repository at all.
