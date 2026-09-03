"""Read past distance readings back out of Home Assistant's recorder.

The integration learns from arrivals it watches happen, so a fresh install
knows nothing until a few school days have gone by. But the distance sensor
has been writing to the recorder the whole time, and every arrival is plainly
visible in it as a dip towards zero. This fetches that history so it can be
replayed instead of relearned.

Recorder access only; turning readings into arrivals lives in the coordinator
alongside the run-window logic it depends on.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import BACKFILL_DAYS, DOMAIN

_LOGGER = logging.getLogger(__name__)


def distance_entity_id(hass: HomeAssistant, child_id: int) -> str | None:
    """Return the distance sensor's entity id, whatever it was renamed to."""
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{child_id}_distance_to_stop"
    )


async def async_distance_history(hass: HomeAssistant, entity_id: str) -> list[State]:
    """Return recent distance readings, or nothing if unavailable.

    Returns an empty list when the recorder is not set up rather than raising:
    it is optional, some installations run without it, and a missing history
    should cost the estimate a few days of learning, not the integration its
    startup.
    """
    if "recorder" not in hass.config.components:
        _LOGGER.debug("Recorder not loaded; skipping history replay")
        return []

    # Imported here, not at module level: the recorder is an optional
    # integration and importing it eagerly would make it a hard dependency.
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )

    end = dt_util.utcnow()
    start = end - timedelta(days=BACKFILL_DAYS)

    rows = await get_instance(hass).async_add_executor_job(
        partial(
            state_changes_during_period,
            hass,
            start,
            end,
            entity_id,
            no_attributes=True,
            include_start_time_state=False,
        )
    )
    return rows.get(entity_id, [])
