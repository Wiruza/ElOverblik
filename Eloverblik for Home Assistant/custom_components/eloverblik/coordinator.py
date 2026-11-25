"""Fetches readings from Eloverblik, keeps them, and feeds the Energy dashboard.

Fork of homeassistant-eloverblik by JonasPed (Apache-2.0), changed for Power Monitor:
* one DataUpdateCoordinator instead of throttled per-sensor polling,
* readings are kept in Home Assistant's storage, so a restart doesn't download a year again,
* the hourly history goes into the external statistic `eloverblik:consumption_<meter id>`,
  the same one the Power Monitor desktop app fills, so an Energy dashboard set up with the
  desktop app keeps working after switching to this integration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import EnergyConverter

from . import calc
from .api import (
    EloverblikAuthError,
    EloverblikClient,
    EloverblikError,
    EloverblikRateLimitError,
    kind_label,
)
from .const import (
    CONF_ADDRESS,
    CONF_HISTORY_DAYS,
    CONF_KIND,
    CONF_METERING_POINT,
    CONF_REFRESH_TOKEN,
    DEFAULT_HISTORY_DAYS,
    DOMAIN,
    OVERLAP_DAYS,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def statistic_id_for(metering_point: str, kind: str) -> str:
    """Same id as the Power Monitor desktop app uses."""
    meter = "".join(c for c in metering_point if c.isalnum()).lower()
    what = "production" if kind == "E18" else "consumption"
    return f"{DOMAIN}:{what}_{meter}"


def statistic_name_for(address: str, kind: str) -> str:
    what = "Electricity produced" if kind == "E18" else "Electricity used"
    return f"{what} – {address}"


class EloverblikCoordinator(DataUpdateCoordinator[calc.Summary]):
    """Keeps one metering point's hourly readings up to date."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_METERING_POINT]}",
            update_interval=UPDATE_INTERVAL,
        )
        self.metering_point: str = entry.data[CONF_METERING_POINT]
        self.kind: str = entry.data.get(CONF_KIND, "E17")
        self.address: str = entry.data.get(CONF_ADDRESS) or f"Meter …{self.short_id}"
        self.history_days: int = int(entry.data.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))
        self.client = EloverblikClient(async_get_clientsession(hass), entry.data[CONF_REFRESH_TOKEN])
        self.hours: calc.Hours = {}
        self.last_problem: str | None = None
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{self.metering_point}")
        self._loaded = False

    @property
    def short_id(self) -> str:
        return "".join(c for c in self.metering_point if c.isalnum())[-6:].lower()

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def statistic_id(self) -> str:
        return statistic_id_for(self.metering_point, self.kind)

    async def _async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            for row in stored.get("hours", []):
                try:
                    ts, kwh, quality = int(row[0]), float(row[1]), int(row[2])
                except (TypeError, ValueError, IndexError):
                    continue
                self.hours[ts] = (kwh, quality)
        self._loaded = True

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"hours": [[ts, round(kwh, 4), q] for ts, (kwh, q) in sorted(self.hours.items())]}
        )

    async def _async_update_data(self) -> calc.Summary:
        if not self._loaded:
            await self._async_load()

        today = calc.today_dk()
        newest = max(self.hours) if self.hours else None
        start = calc.history_start(today, newest, self.history_days, OVERLAP_DAYS)
        # Readings arrive a day or more late, and a period of only "today" is refused,
        # so every request ends at the start of today.
        new_hours = 0
        problems: list[str] = []
        for chunk_from, chunk_to in calc.chunks(start, today):
            try:
                results = await self.client.async_get_time_series(
                    [self.metering_point], chunk_from, chunk_to
                )
            except EloverblikAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except EloverblikRateLimitError as err:
                if self.hours:
                    _LOGGER.info("%s Keeping the readings already downloaded.", err)
                    break
                raise UpdateFailed(str(err)) from err
            except EloverblikError as err:
                if self.hours:
                    _LOGGER.warning("%s Keeping the readings already downloaded.", err)
                    break
                raise UpdateFailed(str(err)) from err
            for data in results:
                if data.problem:
                    problems.append(data.problem)
                    continue
                for ts, value in data.hours.items():
                    if ts not in self.hours:
                        new_hours += 1
                    self.hours[ts] = value

        self.last_problem = "; ".join(dict.fromkeys(problems)) or None
        if self.last_problem:
            _LOGGER.warning("Eloverblik reported for %s: %s", self.metering_point, self.last_problem)
        if new_hours:
            _LOGGER.debug("Downloaded %s new hourly readings for %s", new_hours, self.metering_point)
            await self._async_save()

        await self._async_import_statistics()
        return calc.summarize(self.hours)

    async def _async_import_statistics(self) -> None:
        """Add the hours after the newest stored statistic, continuing its running total."""
        if not self.hours:
            return
        stat_id = self.statistic_id
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, True, {"sum"}
        )
        after: int | None = None
        base = 0.0
        if last.get(stat_id):
            row = last[stat_id][0]
            start = row.get("start")
            if isinstance(start, datetime):
                after = int(start.timestamp())
            elif start is not None:
                after = int(float(start))
            base = float(row.get("sum") or 0.0)
        rows = calc.statistics_rows(self.hours, after, base)
        if not rows:
            return
        metadata = StatisticMetaData(
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=statistic_name_for(self.address, self.kind),
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=EnergyConverter.UNIT_CLASS,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        statistics = [
            StatisticData(start=datetime.fromtimestamp(ts, tz=timezone.utc), state=total, sum=total)
            for ts, total in rows
        ]
        async_add_external_statistics(self.hass, metadata, statistics)
        _LOGGER.debug("Added %s hours to %s", len(statistics), stat_id)
