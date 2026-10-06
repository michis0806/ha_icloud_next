"""Online, Stromsparmodus, veraltete Personen-Position und Speicherwarnungen."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GeraetEntity, ICloudEntity, konto_kennung, person_kennung

# Nur diese Geräteklassen kennen einen Stromsparmodus
MIT_STROMSPARMODUS = ("iPhone", "iPad", "Watch")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ortung = entry.runtime_data["ortung"]
    konto = entry.runtime_data["konto"]
    entities: list[BinarySensorEntity] = []
    for geraet_id, g in ortung.data["geraete"].items():
        entities.append(OnlineSensor(ortung, entry, geraet_id))
        if g["klasse"] in MIT_STROMSPARMODUS:
            entities.append(StromsparSensor(ortung, entry, geraet_id))
    for person in ortung.data.get("zusammengefasst") or {}:
        entities.append(PositionVeraltetSensor(ortung, entry, person))
    hub = konto_kennung(entry)
    entities += [
        SpeicherWarnung(konto, entry, hub, "speicher_fast_voll", "fast_voll"),
        SpeicherWarnung(konto, entry, hub, "speicher_ueber_kontingent", "ueber_kontingent"),
    ]
    async_add_entities(entities)
    # Für das Aufräumen in __init__.py: was bei diesem Laden angelegt wurde
    entry.runtime_data["unique_ids"].update(e.unique_id for e in entities)
    entry.runtime_data["plattformen"].add(__name__.rsplit(".", 1)[-1])


class OnlineSensor(GeraetEntity, BinarySensorEntity):
    """Apple-Gerätestatus 200 = online; 201 offline, 203 ausstehend."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "online")

    @property
    def is_on(self) -> bool | None:
        return (self._g or {}).get("online")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"statuscode": (self._g or {}).get("status_code")}


class StromsparSensor(GeraetEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "stromsparmodus")

    @property
    def is_on(self) -> bool | None:
        return (self._g or {}).get("stromsparmodus")


class PositionVeraltetSensor(ICloudEntity, BinarySensorEntity):
    """An, wenn für die Person kein Gerät eine frische Position liefert."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry, person: str) -> None:
        super().__init__(coordinator, entry, person_kennung(person), "position_veraltet")
        self._person = person

    @property
    def _p(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("zusammengefasst", {}).get(self._person)

    @property
    def available(self) -> bool:
        return super().available and self._p is not None

    @property
    def is_on(self) -> bool | None:
        p = self._p
        return None if p is None else not p["frisch"]


class SpeicherWarnung(ICloudEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry, hub: str, schluessel: str, feld: str) -> None:
        super().__init__(coordinator, entry, hub, schluessel)
        self._feld = feld

    @property
    def is_on(self) -> bool | None:
        return ((self.coordinator.data or {}).get("speicher") or {}).get(self._feld)
