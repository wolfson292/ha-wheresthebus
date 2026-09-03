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
| `sensor.<rider>_gps_age` | Age of the last GPS fix in minutes (diagnostic). |
| `sensor.<rider>_next_arrival` | When the bus is next expected at the rider's stop. |
| `sensor.<rider>_eta` | The app's ETA message, when the district publishes one. |
| `sensor.<rider>_last_scan` | Timestamp of the most recent ID scan. |
| `sensor.<rider>_last_pickup` | Timestamp the rider was last picked up. |
| `sensor.<rider>_last_drop_off` | Timestamp the rider was last dropped off. |
| `sensor.<rider>_bus_number` | Bus number (diagnostic). |
| `sensor.<rider>_morning_stop_time` | Scheduled AM stop time (diagnostic). |
| `sensor.<rider>_afternoon_stop_time` | Scheduled PM stop time (diagnostic). |

The scan sensors carry `scan_location`, `scan_method`, `bus_number`,
`stop_address` and `school_name` attributes. The tracker carries the stop
coordinates and the status colour the app uses.

### Bus status and GPS age

The API reports status as a sentence written for a human — `current`, then
`1 min. ago`, `2 min. ago`, and so on up to `inactive` — which changes every
single minute a bus is moving. That is unusable as an entity state and fills
the recorder with unbounded strings, so it is split in two: `bus_status` holds
one of three values, and `gps_age` holds the number of minutes. The original
string is still available as the `raw_status` attribute on `bus_status`, and
wording the parser does not recognise leaves `bus_status` unknown rather than
discarding it.

### Predicted arrival

`next_arrival` is a **timestamp**, not a minutes-remaining number: a countdown
would rewrite itself on every poll, and Home Assistant renders a timestamp as
relative time anyway. It also means alerting automations are plain `time`
triggers with a negative offset — no templates.

Once the bus is within a mile of the stop the estimate **re-anchors to the
live approach**: the moment it crossed that mile, plus the typical time the
final leg takes. Before that it is the median of previously observed arrival
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

Two things make the observations trustworthy:

- **Only arrivals within 30 minutes of the scheduled stop time count.** Buses
  routinely pass a stop on unrelated earlier routes — one was observed at the
  stop at 06:13 for an 07:56 pickup — and learning from those would be worse
  than useless.
- **The bus must actually reach the stop** (within 0.3 mi). On a run where
  nobody boards, the route can stay half a mile out; that is not an arrival.

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
