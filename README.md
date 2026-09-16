# WheresTheBus for Home Assistant

A custom integration that brings [WheresTheBus](https://wheresthebus.com/) school
bus tracking into Home Assistant: where your child's bus is right now, how far it
is from their stop, and when their ID was last scanned getting on or off.

Works with any district that uses the WheresTheBus parent app — the same email
and password you use in the app.

## What you get

One device per rider on your account, with these entities:

| Entity | Description |
| --- | --- |
| `device_tracker.<rider>_bus` | Live GPS position of the bus. Drops onto a map card, and works with zone triggers. |
| `sensor.<rider>_distance_to_stop` | Distance from the bus to the rider's stop. |
| `sensor.<rider>_bus_status` | Freshness of the bus's GPS fix: `current`, `stale` or `inactive`. |
| `sensor.<rider>_last_gps_fix` | When the bus was last heard from (diagnostic). |
| `sensor.<rider>_journey` | Which stage of the run the rider is in right now. |
| `sensor.<rider>_next_arrival` | When the bus is next expected at the rider's stop, with an `earliest`/`latest` band. |
| `sensor.<rider>_school_arrival` | When the morning ride is expected to reach school. |
| `sensor.<rider>_last_scan` | Timestamp of the most recent ID scan. |
| `sensor.<rider>_last_pickup` | Timestamp the rider was last picked up. |
| `sensor.<rider>_last_drop_off` | Timestamp the rider was last dropped off. |
| `sensor.<rider>_bus_number` | Bus number (diagnostic). |
| `sensor.<rider>_morning_stop_time` | Scheduled AM stop time (diagnostic). |
| `sensor.<rider>_afternoon_stop_time` | Scheduled PM stop time (diagnostic). |

The scan sensors carry `scan_location`, `scan_method`, `bus_number`,
`stop_address` and `school_name` attributes. The tracker carries the stop
coordinates and the status colour the app uses.

### Bus status and GPS freshness

The API reports status as a sentence written for a human — `current`, then
`1 min. ago`, `2 min. ago`, and so on up to `inactive` — which changes every
single minute a bus is moving. That is unusable as an entity state and fills
the recorder with unbounded strings, so it is split in two: `bus_status` holds
one of three values, and `last_gps_fix` holds the **instant** of the fix. The
original string is still available as the `raw_status` attribute on
`bus_status`, and wording the parser does not recognise leaves `bus_status`
unknown rather than discarding it.

The instant matters rather than an age in minutes: an age changes every minute
the bus is running, which wrote 720 recorder rows in three days on a live
install for a diagnostic nobody reads. Home Assistant renders a timestamp as
"3 minutes ago" by itself. Because the API only reports the age to the nearest
minute, a newly computed instant has to differ from the standing one by more
than 90 seconds to replace it — enough to absorb the rounding, small enough to
move the moment a real fix lands. A bus that has gone inactive reports no fix
at all rather than an increasingly stale one.

### Replaying real journeys before shipping

The estimate is a pure function of recorded positions, and the recorder keeps
those. So *"what would this have told you, minute by minute, and how wrong
would it have been"* is answerable at a desk, against real runs, before a
release rather than after one.

`scripts/capture_journey.py` turns a recorder dump into a replayable journey,
and `tests/test_backtest.py` replays them and scores the error at every sample.

**Recorded journeys stay local.** `tests/journeys/` is gitignored and must
remain so. The capture script moves the coordinates to a fictional origin,
which hides the latitude and longitude — but it deliberately preserves every
distance and bearing between points, because that geometry is the thing being
tested. A few miles of turns is a fingerprint: matched against the road network
it goes straight back on the map, and it is a child's route to and from school.
Without any journeys present the backtests skip, which is the honest outcome —
claiming to have validated against real runs when there were none would be
worse than saying so.

This exists because it was missing. Two faults it would have caught in seconds:
an estimate that slid forward with the clock whenever the bus stopped closing,
flat-lining at "about twenty minutes away" for a quarter of an hour; and a
reading checked against a single agreeable instant, called validated, and wrong
within the hour. Both were found instead by somebody standing at a bus stop.

**Any change to how the arrival is estimated should be scored against these
journeys first.** A change that cannot be shown to help on runs already
recorded is a guess.

### The journey stage

`journey` answers one question — *what is happening right now* — as an enum
with the numbers that belong to it:

| Stage | Meaning | `progress` measures |
| --- | --- | --- |
| `idle` | No run under way | — |
| `to_stop` | Bus approaching the home stop, morning | Distance closed from 3 miles |
| `at_stop` | Bus at the stop — time to board | 100 |
| `to_school` | Aboard, riding to school | Elapsed against the learned ride |
| `at_school` | Scanned off at school, briefly | 100 |
| `from_school` | Aboard, riding home | Elapsed against the predicted arrival |
| `home` | At the home stop, or scanned off there | 100 |

Alongside it: `target` (the instant being counted towards), `boarded`, and
`journey_id` — a date-plus-run identifier so a notification carrying it can
never update yesterday's.

This exists because the same question was previously answered independently
in ten branches of a notification automation, each from whatever signals were
nearest to hand. Four faults came out of the gaps between them: a progress bar
that filled for two hours after the rider reached school, because that branch's only
stop was a scan the school skips about two days in three; a bar that lurched
between 78% and 7%, because two branches measured progress on different
scales; a title and colour that flickered, because they were chosen in two
places that disagreed; and a countdown that ran 61 hours to the following
Monday, because one branch never checked that its target was still today.

None of those were hard problems. They were the same problem, in a place where
it could not be tested. The stage machine is a pure function — no coordinator,
no clock of its own — so every transition is checked directly at whatever
instant of a school day it happens at.

Ordering is the part worth knowing: having arrived somewhere outranks being on
the way there, and being aboard outranks the bus merely being nearby. Without
that last rule an afternoon approach describes a rider already on the bus as
though they were still waiting at the kerb for it.

### Predicted arrival

`next_arrival` is a **timestamp**, not a minutes-remaining number: a countdown
would rewrite itself on every poll, and Home Assistant renders a timestamp as
relative time anyway. It also means alerting automations are plain `time`
triggers with a negative offset — no templates.

The estimate is answered by **matching where the bus is against where past
journeys physically were**. Each arrival records the positions the bus passed
through and how long it still had from each of them, so today's position can be
looked up directly: *the last time a bus was here, how long did it take?*

Straight-line distance to the stop cannot answer that, because a school bus is
not travelling towards the stop. It serves other children, turns round in
cul-de-sacs, and spends long stretches driving directly away — and measured as
distance, every one of those looks like a setback. An estimate built on
distance slides forward with the clock and never converges: observed moving
twelve minutes later over twelve minutes while the bus worked stops four miles
out, reporting "about twenty minutes away" the entire time.

Matched as a position, none of that is ambiguous. A U-turn matches a U-turn. A
pause outside another school matches the same pause. Standing still matches the
same place, which has the same answer however long the bus sits there. And a
bus that is genuinely running ahead — a stop skipped because nobody was aboard
— matches a point that came late in previous journeys, so the estimate moves
earlier immediately.

A route crosses itself, so the same junction is driven twice — and the two
passes want different answers. What separates them is **which way the bus is
pointing**. At a U-turn the outbound and homeward samples sit metres apart and
about 180 degrees opposed, so a past position heading the other way is dropped
however close it is.

Direction is used rather than timing because it owes nothing to the schedule.
Comparing how long today's journey has been running against a past one assumes
today is going roughly like that one did, which is the very thing the estimate
exists to test: a bus ten minutes down drifts against every past sample
equally, and the tie-break stops discriminating exactly when it matters. A
heading is true whatever the clock says. Elapsed time is still there, demoted
to separating same-direction passes.

A position only answers when it actually pins the journey down. Where the
matching samples from a past run disagree by more than five minutes about how
long was left, the place is treated as having no answer — a bus parked at the
depot matches the whole of yesterday's wait there, and the two ends of that
wait are half an hour apart. Measured on one morning, the moving samples
spanned 0.0 to 2.5 minutes and the parked ones 34.5, so there is no borderline
to argue over. Refusing hands the question back to the historical estimate,
which is vague but honest; answering produced a confident number 31 minutes
wrong for the first half of the window.

A bus standing still has no heading of its own, so its direction is read from
the last fix that genuinely moved — it reached that spot going somewhere. A
sample with nothing behind it at all is never filtered out: refusing every
position whose direction is unknown would discard the start of every journey.

`prediction_basis` reads `route`, and `route_samples` says how many past
journeys came near this spot. When none did — a detour, or a substitute on
another route — the estimate says so by falling back rather than reporting a
confident number derived from nothing.

The published arrival **holds still unless the estimate really moves** — more
than two minutes. The route match is accurate and noisy at once: on one ride
home it swung across twelve minutes, changing every thirty to sixty seconds,
while the bus arrived within a minute of where the median sat the whole time.
Republishing each wobble made the dashboard band jitter and drove a
notification to push twenty-five times in twenty minutes. The band is not
frozen with it — its width still narrows as the bus closes in, recentred on
the arrival being shown, so the two never contradict each other.

The target is rounded to the minute it will be displayed as. That is not
cosmetic: the estimate carries microseconds and is recomputed every thirty
seconds, so a raw target moves constantly while saying the same thing, and
anything watching it for a reason to act fires on every poll. Rounded, it
changes exactly when a reader would see it change — which lets a notification
re-push the moment the estimate really moves, and stay quiet when it has not.

**The afternoon needs a scan — or the rider's phone.** Whether the rider is on the bus is not a thing
to infer from the clock — they scan a badge to board it. The ride home is
measured from that scan, against the predicted arrival. Without one there is
no ride home to show, and nothing is displayed.

Set **Rider's phone** in the options to cover a forgotten scan. It is asked
only on an afternoon with no scan, and only twice: where the phone is when the
window opens, while the bus is still loading, and where it is once the bus has
gone. Travelled a good distance **and** with the bus means aboard. Either test
alone is useless — a car going the other way has travelled, and a phone on the
kerb beside a loading bus is right next to it.

An iCloud3 tracker is asked directly for a fresh fix, which returns an eight
metre position in twenty to thirty seconds; its own polling is fifteen minutes
when a phone sits still, which is far too stale for a bus pulling away. Any
other tracker is simply read at whatever rate it updates itself.

The scan always wins. On a day the badge is scanned none of this runs, and no
location is requested at all.

The morning is deliberately different: the scan happens on boarding, so in the
morning there cannot be one yet, and the approach is shown on the window and
the live distance. Requiring a scan there would hide the one reading that
decides when to walk out of the door.

That asymmetry was learned the hard way. An afternoon approach that opened on
the clock alone showed a bus coming home on a day the rider was not aboard,
then flapped in and out of idle as the estimate slid past — three Live
Activities started and cleared in seventy minutes, which spent the next
morning's iOS push-to-start budget and left it with no notification at all.

A run stays the next arrival until its window shuts or the bus actually comes.
A predicted time going by does not end it — the bus is late, not cancelled —
and the card reads "any moment now" rather than counting up. Rolling on to
tomorrow at that point made the afternoon of 15 Sep flap between "riding home"
and nothing three times in seventy minutes, each flap starting and clearing a
Live Activity, which spent the iOS push-to-start budget and left the next
morning with no notification at all.

Failing that, as the bus closes in the estimate **re-anchors to the live approach**. Each
arrival records how long the rest of the journey took from 3, 2, 1 and 0.5
miles out, and the estimate uses the tightest of those the bus has already
crossed today — the moment it crossed, plus the typical remaining time. So it
tightens as the bus approaches rather than ignoring its position until one
fixed threshold is met. Before that it is the median of previously observed arrival
times. The distinction matters — measured over four mornings the clock time
of arrival varied by 8 minutes while the final leg varied by 2.5, so a bus
that sets off early is reported early instead of being averaged back towards
its usual time. The `prediction_basis` attribute says which is in use.

On first start after upgrading, past arrivals are **recovered from Home
Assistant's own recorder** rather than relearned: the distance sensor has been
writing every position all along, and each arrival is plainly visible in it as
a dip towards zero inside a run's window. The same distance and window filters
are applied as on the live path, so nothing is learned from history that would
not have been learned live. This runs once, is skipped as soon as any final
leg has been recorded, and is silently skipped entirely where the recorder is
not enabled.

A prediction only lands on a day the bus is believed to run: weekdays, plus
any day an arrival has actually been observed on. Without that, a Friday
evening predicted Saturday morning. It is learned rather than hardcoded so a
route that genuinely runs at the weekend keeps working once it has been seen
doing so — but not inferred the other way round, because a rider who has not
happened to ride on a Wednesday yet must not lose Wednesdays.

A run already under way is **rebuilt from the recorder on startup**. The
closest approach so far and the rung crossings live only in memory, so
restarting mid-run left the integration blind to everything that had already
happened — and the bus passing by on its way elsewhere then read as a fresh
approach, re-anchoring the estimate and very nearly recording a second arrival
for the day.

The historical part is the median of **every retained arrival** for that run,
not the most recent one — a single bus stuck behind a train should not drag
tomorrow's prediction with it, and the median ignores an outlier that a mean
would chase. Until any arrival has been observed it falls back to the
district's scheduled stop time. The `prediction_source`, `samples` and
`spread_minutes` attributes report which is in use and how tightly the run
actually clusters.

Each run keeps its own history (the most recent 30, roughly six school
weeks). The two runs do not share a budget: a stretch of missed afternoons
would otherwise quietly evict mornings that were still worth learning from.

Three things make the observations trustworthy:

- **Runs are centred on the learned arrival, not the timetable.** A run counts
  as genuine within 30 minutes of that centre, and the approach is watched from
  45 minutes before it. Buses routinely pass a stop on unrelated earlier routes
  — one was observed at the stop at 06:13 for an 07:56 pickup — and learning
  from those would be worse than useless. Centring on the published time is
  not good enough: one district's afternoon timetable read 17:48 against a real
  arrival near 17:20, so a timetable-centred window opened *after* the bus had
  crossed the 3, 2 and 1 mile rungs. Every one was discarded and the estimate
  ran on the clock median all afternoon. Until a run has been observed at all,
  the timetable is the centre.
- **A rung is crossed, not occupied.** A crossing is recorded only when the bus
  moves from outside a rung to inside it. Being inside one when watching begins
  proves nothing about when it got there: on one morning the bus sat parked at
  exactly 3 miles, and the first reading after the window opened was read as
  "just crossed three miles". The usual nine-and-a-half minute leg was measured
  from that, predicting 07:25 for a bus that arrived at 08:01, and a five
  minute warning went out at 07:20. A rung the bus was already inside simply
  carries no timing for that day.
- **A rung only counts while the bus keeps closing.** A bus that crosses a rung
  and then drifts back outside it by more than 15%, without ever reaching the
  stop, loses that crossing — it was serving nearby stops, not making its final
  approach. Readings after the bus has arrived are the bus leaving, and never
  undo the approach that preceded them.
- **The bus must actually reach the stop** (within 0.3 mi). On a run where
  nobody boards, the route can stay half a mile out; that is not an arrival.

A drop-off scan says the rider got off the bus, not *where*. Which it was
depends on the run: in the morning it is the school, in the afternoon the home
stop. The afternoon leg went unscanned for the first eight days observed here,
and building on that produced an activity announcing "Arrived at school" as the
rider stepped off the bus outside the house.

`anchored_at` and `anchor_samples` report which rung the live estimate is
hanging on and how many past journeys back it; both are absent when the
estimate is only the clock median. `window_centre` reports where the run's
window was placed.

### What each journey records

Alongside the four rung timings, every arrival keeps the **shape of its
approach** — up to 80 `(seconds before arrival, distance)` samples, plus a
count of how often the bus turned back out and how many readings had a stale
GPS fix. The ladder says when the bus passed four points; the track says what
it did in between, which is what distinguishes a bus that crawled the whole
way from one that sat still and then sprinted.

None of this is used by the current estimator. It is recorded so that a better
one can be fitted to journeys already observed, instead of waiting a term to
collect them again. It is all in the diagnostics download, along with the
per-rung spread that says which rungs are worth trusting.

### Substitute buses

The API names a replacement vehicle in each rider's `sub` field when one is
covering the route. It is surfaced as a `substitute_bus` attribute on
`bus_number`, and arrivals on those days are recorded but **held out of what
the estimate learns from** — a different bus and driver run the route
differently, and averaging that in drags every following day.

### The ride to school

`school_arrival` predicts when the morning ride reaches school, learned from
the drop-off scans the school itself records — the same median and outlier
rejection used for stop arrivals. Its `typical_ride_minutes` attribute is the
median pickup-to-drop-off time, which is what lets a Live Activity fill a
progress bar across the ride rather than only counting elapsed time.

### Scan history is remembered

`getStudentScan` only ever returns the current day, so the scan sensors would
otherwise blank at midnight and again on every restart — a parent checking
before school would see nothing instead of yesterday afternoon's pickup.
Scans are merged into a rolling per-rider history (the most recent 50), stored
under `.storage`, and reloaded at startup.

### How pickup and drop-off are worked out

The WheresTheBus API reports scans as bare "ID received" events with a location
and a timestamp — it does not say whether the rider was boarding or alighting.
This integration infers the direction:

- A scan at the school is a **drop-off** in the morning and a **pickup** in the
  afternoon.
- A scan anywhere else (the neighbourhood stop) is the reverse.
- If the location can't be matched, scans alternate pickup → drop-off within
  each day.

This matches how a normal school day runs, but it is an inference. If your
district scans differently, `sensor.<rider>_last_scan` and its `scan_location`
attribute always give you the raw event to build your own template on.

## Installation

### HACS

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/wolfson292/ha-wheresthebus` with type **Integration**.
3. Find **WheresTheBus** in HACS and download it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for
   **WheresTheBus**.

### Manual

Copy `custom_components/wheresthebus` into your Home Assistant `config/custom_components`
directory and restart, then add the integration from the UI.

## Configuration

Sign in with your WheresTheBus parent app email and password. Riders are
discovered automatically.

Under the integration's **Configure** button you can set two poll intervals:

- **Bus location update interval** — default 30 seconds. The app itself refreshes
  every 15 seconds; 30 keeps the marker useful while halving the request rate.
- **Roster and ID scan update interval** — default 5 minutes. Stop details and
  scan history change only a handful of times a day.

Home Assistant polls continuously, so consider raising the bus interval outside
school hours if you would rather be gentle on the service.

## Example automation

```yaml
automation:
  - alias: Bus is close to the stop
    triggers:
      - trigger: numeric_state
        entity_id: sensor.robin_alex_rivera_distance_to_stop
        below: 0.5
    conditions:
      - condition: time
        after: "06:30:00"
        before: "09:00:00"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "The bus is under half a mile from the stop."
```

```yaml
automation:
  - alias: Rider was picked up
    triggers:
      - trigger: state
        entity_id: sensor.robin_alex_rivera_last_pickup
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            Picked up at
            {{ state_attr('sensor.robin_alex_rivera_last_pickup', 'scan_location') }}.
```

## Notes and limitations

- This uses the same private API as the WheresTheBus parent app. It is not an
  official or supported integration, and the API can change without notice.
- Position data is only as good as what the district publishes; buses that are
  not running report a stale or missing position.
- Distance is reported by the API in miles or kilometres according to your
  account setting, and Home Assistant converts it to your own unit system.
- Districts that use SAML single sign-on are **not** supported — the integration
  signs in with an email and password only.
- Not every district scans on alighting. Where they don't, "last drop-off" means
  *arrived at school*, not *arrived home* — check the `scan_location` attribute
  rather than assuming.
- Buses often pass a stop on an earlier route, so a bare "distance below X"
  automation can fire on the wrong run. Trigger on `sensor.<rider>_last_pickup`
  changing, or gate the proximity trigger on the scheduled stop time.

## Examples

A ready-made [dashboard and alerting automations](examples/) live in
`examples/` — a three-view dashboard, four banner alerts, and an iOS Live
Activity that counts down on the Lock Screen. Copy them and replace the
placeholders; see [examples/README.md](examples/README.md).

## Brand images

Home Assistant does **not** load brand images from this repository or from the
`custom_components` folder — the frontend fetches them from
`brands.home-assistant.io`. Getting the icon to show in the integrations list
therefore needs a pull request to
[home-assistant/brands](https://github.com/home-assistant/brands) adding
`custom_integrations/wheresthebus/icon.png` (256x256), `icon@2x.png` (512x512)
and their `logo` counterparts. The files in `brand/` are ready to submit.

The artwork is original. WheresTheBus publishes only a trademarked wordmark,
which is the wrong shape for a square icon and is not this project's to
redistribute under the GPL, so `scripts/make_brand_assets.py` draws a plain
school-bus badge instead. Regenerate with:

```bash
python3 scripts/make_brand_assets.py
```

## Development

```bash
python3 -m venv venv
./venv/bin/pip install pytest-homeassistant-custom-component ruff
./venv/bin/python -m pytest tests
./venv/bin/ruff check .
```

The fixtures in `tests/fixtures.py` reproduce the API's real response shapes —
key names, nesting, epoch timestamps, the miles/kilometres flag, the
empty-string placeholders — with entirely invented names, identifiers,
addresses and coordinates.

## License

Copyright (C) 2026 Scott Wolf

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the [GNU General Public License](LICENSE) for more
details.
