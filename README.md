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
| `sensor.<rider>_bus_status` | The status message the app shows, e.g. `current`. |
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
