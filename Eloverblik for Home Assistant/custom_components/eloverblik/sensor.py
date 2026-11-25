"""Sensors for the Eloverblik integration.

Fork of homeassistant-eloverblik by JonasPed (Apache-2.0), changed for Power Monitor:
the sensors are the ones the Power Monitor desktop app sends (latest full day, last 7 days,
this month, last month, this year, latest hour, readings until), with the same entity ids,
so dashboards made for the desktop app keep working.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EloverblikConfigEntry
from . import calc
from .const import ATTRIBUTION, DOMAIN
from .coordinator import EloverblikCoordinator


@dataclass(frozen=True, kw_only=True)
class EloverblikSensorDescription(SensorEntityDescription):
    value_fn: Callable[[calc.Summary], Any]
    attrs_fn: Callable[[calc.Summary], dict[str, Any]] = lambda s: {}


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


SENSORS: tuple[EloverblikSensorDescription, ...] = (
    EloverblikSensorDescription(
        key="latest_day",
        translation_key="latest_day",
        name="Latest full day",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: _r(s.latest_day_total),
        attrs_fn=lambda s: {
            "date": s.latest_day.isoformat() if s.latest_day else None,
            "hourly_kwh": [round(v, 3) for v in s.latest_day_hours],
        },
    ),
    EloverblikSensorDescription(
        key="last_7_days",
        translation_key="last_7_days",
        name="Last 7 days",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s: _r(s.last7[2]) if s.last7 else None,
        attrs_fn=lambda s: {"from": s.last7[0].isoformat(), "to": s.last7[1].isoformat()} if s.last7 else {},
    ),
    EloverblikSensorDescription(
        key="this_month",
        translation_key="this_month",
        name="This month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s: _r(s.month[2]) if s.month else None,
        attrs_fn=lambda s: {
            "month": f"{s.month[0]}-{s.month[1]:02d}",
            "until": s.latest_day.isoformat() if s.latest_day else None,
        }
        if s.month
        else {},
    ),
    EloverblikSensorDescription(
        key="last_month",
        translation_key="last_month",
        name="Last month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda s: _r(s.last_month[2]) if s.last_month else None,
        attrs_fn=lambda s: {"month": f"{s.last_month[0]}-{s.last_month[1]:02d}"} if s.last_month else {},
    ),
    EloverblikSensorDescription(
        key="this_year",
        translation_key="this_year",
        name="This year",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        value_fn=lambda s: _r(s.year[1]) if s.year else None,
        attrs_fn=lambda s: {
            "year": s.year[0],
            "until": s.latest_day.isoformat() if s.latest_day else None,
        }
        if s.year
        else {},
    ),
    EloverblikSensorDescription(
        key="latest_hour",
        translation_key="latest_hour",
        name="Latest hour",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda s: _r(s.latest_hour[1]) if s.latest_hour else None,
        attrs_fn=lambda s: {"hour_start": calc.local_dt(s.latest_hour[0]).strftime("%Y-%m-%d %H:00")}
        if s.latest_hour
        else {},
    ),
    EloverblikSensorDescription(
        key="data_until",
        translation_key="data_until",
        name="Readings until",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=lambda s: datetime.fromtimestamp(s.data_until, tz=timezone.utc) if s.data_until else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: EloverblikConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensors for one metering point."""
    coordinator = entry.runtime_data
    async_add_entities(EloverblikSensor(coordinator, description) for description in SENSORS)


class EloverblikSensor(CoordinatorEntity[EloverblikCoordinator], SensorEntity):
    """One number from the latest readings."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: EloverblikSensorDescription

    def __init__(self, coordinator: EloverblikCoordinator, description: EloverblikSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.metering_point}-{description.key}"
        # Same entity ids as the Power Monitor desktop app sends.
        self.entity_id = f"sensor.{DOMAIN}_{coordinator.short_id}_{description.key}"
        if coordinator.kind == "E18" and description.device_class == SensorDeviceClass.ENERGY:
            self._attr_icon = "mdi:solar-power"
        elif description.device_class == SensorDeviceClass.ENERGY:
            self._attr_icon = "mdi:flash"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.metering_point)},
            name=coordinator.address,
            manufacturer="Eloverblik (Energinet DataHub)",
            model=f"{coordinator.kind_label} metering point",
            serial_number=coordinator.metering_point,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://eloverblik.dk",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {"metering_point": self.coordinator.metering_point}
        attrs.update(self.entity_description.attrs_fn(self.coordinator.data))
        return attrs
