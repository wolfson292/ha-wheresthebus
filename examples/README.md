# Examples

A dashboard and a set of alerting automations built on top of the integration.
Both are working configurations with the personal details replaced by
placeholders — they are meant to be copied and edited, not installed as-is.

| File | What it is |
| --- | --- |
| [`dashboard.yaml`](dashboard.yaml) | Four-view dashboard: Overview, Map, History, and a Predictions page |
| [`automations.yaml`](automations.yaml) | Four banner alerts plus a three-part iOS Live Activity |

## What to replace

| Placeholder | Replace with |
| --- | --- |
| `RIDER` | Your rider's entity slug, e.g. `jane_smith` for `sensor.jane_smith_next_arrival` |
| `NOTIFY_TARGET` | Your notify service, e.g. `notify.mobile_app_janes_iphone`, or a notify group |
| `/school-bus/overview` | Your dashboard path, if you name it something else |

Find the slug under **Settings → Devices & Services → WheresTheBus**, on the
rider's device. Then:

```bash
sed -i '' -e 's/RIDER/jane_smith/g' \
          -e 's|NOTIFY_TARGET|notify.mobile_app_janes_iphone|g' \
          examples/automations.yaml examples/dashboard.yaml
```

## Installing

**Dashboard** — Settings → Dashboards → Add dashboard → New dashboard from
scratch. Open it, then three-dots → Raw configuration editor, and paste
everything below the header comment.

**Automations** — paste into your `automations.yaml`, or create each one via
Settings → Automations → three-dots → Edit in YAML.

## Adjust the time windows

The windows in `automations.yaml` bracket an 07:56 and 17:48 timetable. Change
them to straddle your own scheduled stop times, which the integration exposes
as `sensor.RIDER_morning_stop_time` and `sensor.RIDER_afternoon_stop_time`.

These matter more than they look. Buses routinely pass a stop on unrelated
earlier routes, and without the windows every position-based alert fires on
those passes. On the route these were written against, the bus reached the
stop at 06:13 every morning ahead of an 07:56 pickup.

## How the alerts fit together

The prediction-based alerts (10 and 5 minutes) require
`prediction_source: learned`, so they stay silent until the integration has
actually observed arrivals for that run. Until then the position-based
"closing in" covers the gap, and stands aside once the prediction is
trustworthy. They never both fire for the same run.

"Arriving now" is position-based and always active — it cannot be wrong about
where the bus is, even when the prediction is.

## Requirements

- The Live Activity automations need **Home Assistant 2026.7+** and iOS 17.2+.
  The phone must complete a token handshake first: if nothing appears, open the
  companion app and sync Live Activities in its settings.
- The dashboard needs **2024.11+** for sections views and `grid_options`.
- The banner alerts work on any version that runs the integration.
