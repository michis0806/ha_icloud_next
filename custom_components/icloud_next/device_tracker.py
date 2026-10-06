"""Positionen je Gerät und zusammengefasst je Person."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GeraetEntity, ICloudEntity, person_kennung


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ortung = entry.runtime_data["ortung"]
    entities: list[TrackerEntity] = [
        GeraetTracker(ortung, entry, geraet_id)
        for geraet_id in ortung.data["geraete"]
    ]
    entities += [
        PersonTracker(ortung, entry, person)
        for person in ortung.data.get("zusammengefasst") or {}
    ]
    async_add_entities(entities)
    # Für das Aufräumen in __init__.py: was bei diesem Laden angelegt wurde
    entry.runtime_data["unique_ids"].update(e.unique_id for e in entities)
    entry.runtime_data["plattformen"].add(__name__.rsplit(".", 1)[-1])


def _attribute(d: dict[str, Any]) -> dict[str, Any]:
    zeit = d.get("ortungszeit")
    return {
        "ortungszeit": zeit.isoformat() if zeit else None,
        "veraltet": d.get("veraltet"),
        "positionsquelle": d.get("positionsquelle"),
    }


class GeraetTracker(GeraetEntity, TrackerEntity):
    _attr_name = None  # Entity heißt wie das Gerät

    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "position")
        # AirPods & Co. liefern selten eine Position — standardmäßig aus.
        self._attr_entity_registry_enabled_default = not self._g["zubehoer"]

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return (self._g or {}).get("breite")

    @property
    def longitude(self) -> float | None:
        return (self._g or {}).get("laenge")

    @property
    def location_accuracy(self) -> float:
        return round((self._g or {}).get("genauigkeit") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        g = self._g or {}
        return {**_attribute(g), "hoehe": g.get("hoehe"), "person": g.get("person_name")}


class PersonTracker(ICloudEntity, TrackerEntity):
    """Beste Position einer Person über alle ihre Geräte (Logik im Coordinator)."""

    _attr_name = None

    def __init__(self, coordinator, entry, person: str) -> None:
        super().__init__(coordinator, entry, person_kennung(person), "person")
        self._person = person

    @property
    def _p(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("zusammengefasst", {}).get(self._person)

    @property
    def available(self) -> bool:
        return super().available and self._p is not None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return (self._p or {}).get("breite")

    @property
    def longitude(self) -> float | None:
        return (self._p or {}).get("laenge")

    @property
    def location_accuracy(self) -> float:
        return round((self._p or {}).get("genauigkeit") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self._p or {}
        return {**_attribute(p), "geraet": p.get("geraet"), "frisch": p.get("frisch")}
