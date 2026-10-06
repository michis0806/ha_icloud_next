"""Mitteilungen auf Apple-Geräten anzeigen ("Wo ist?" → Mitteilung anzeigen).

Das ist keine normale Push-Benachrichtigung, sondern die Find-My-Funktion: Apple
blendet Titel und Text auf dem Gerät ein, auf Wunsch mit Ton. Die Standard-Aktion
notify.send_message kennt keinen Ton; dafür gibt es icloud_next.display_message.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform

from . import api
from .entity import GeraetEntity

STANDARD_TITEL = "Home Assistant"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ortung = entry.runtime_data["ortung"]
    entities = [
        MitteilungEntity(ortung, entry, geraet_id)
        for geraet_id, g in ortung.data["geraete"].items()
        if g["nachricht_moeglich"] and not g["zubehoer"]
    ]
    async_add_entities(entities)
    async_get_current_platform().async_register_entity_service(
        "display_message",
        {
            vol.Required("message"): cv.string,
            vol.Optional("title"): cv.string,
            vol.Optional("sound", default=False): cv.boolean,
        },
        "async_mitteilung",
    )
    # Für das Aufräumen in __init__.py: was bei diesem Laden angelegt wurde
    entry.runtime_data["unique_ids"].update(e.unique_id for e in entities)
    entry.runtime_data["plattformen"].add(__name__.rsplit(".", 1)[-1])


class MitteilungEntity(GeraetEntity, NotifyEntity):
    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "mitteilung")

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        await self.async_mitteilung(message, title, False)

    async def async_mitteilung(
        self, message: str, title: str | None = None, sound: bool = False
    ) -> None:
        try:
            await self.hass.async_add_executor_job(
                api.nachricht_senden,
                self.coordinator.dienst,
                self._kennung,
                title or STANDARD_TITEL,
                message,
                sound,
            )
        except api.ICloudError as err:
            raise HomeAssistantError(f"Mitteilung nicht gesendet: {err}") from err
