"""Suchton auf Apple-Geräten abspielen ("Wo ist?" → Ton abspielen)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import api
from .entity import GeraetEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ortung = entry.runtime_data["ortung"]
    entities = [
        TonKnopf(ortung, entry, geraet_id)
        for geraet_id, g in ortung.data["geraete"].items()
        if g["ton_moeglich"]
    ]
    async_add_entities(entities)
    # Für das Aufräumen in __init__.py: was bei diesem Laden angelegt wurde
    entry.runtime_data["unique_ids"].update(e.unique_id for e in entities)
    entry.runtime_data["plattformen"].add(__name__.rsplit(".", 1)[-1])


class TonKnopf(GeraetEntity, ButtonEntity):
    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "ton_abspielen")
        # Wie die Positionen: bei AirPods & Co. standardmäßig aus
        self._attr_entity_registry_enabled_default = not self._g["zubehoer"]

    async def async_press(self) -> None:
        try:
            await self.hass.async_add_executor_job(
                api.ton_abspielen, self.coordinator.dienst, self._kennung
            )
        except api.ICloudError as err:
            raise HomeAssistantError(f"Ton nicht abgespielt: {err}") from err
