"""Config-Flow für iCloud Next.

Ablauf: Apple-ID und Passwort -> (falls Apple 2FA verlangt) Zustellweg wählen ->
Code eingeben. Der Code wird erst nach der Wahl angefordert; pyicloud schickt in
dieser Integration nie von sich aus einen (siehe api.py). Die Neuanmeldung
(Reauth) nutzt dieselben Schritte.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback

from . import api, familie_aktiv
from .const import (
    CONF_APPLE_ID,
    CONF_SCAN_INTERVAL,
    CONF_WITH_FAMILY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    METHOD_PUSH,
    METHOD_SMS,
    MIN_SCAN_INTERVAL,
)

SCHEMA_USER = vol.Schema(
    {
        vol.Required(CONF_APPLE_ID): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_WITH_FAMILY, default=True): bool,
    }
)
SCHEMA_CODE = vol.Schema({vol.Required("code"): str})


class ICloudNextConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._daten: dict[str, Any] = {}
        self._dienst: Any = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ICloudNextOptionsFlow()

    # ------------------------------------------------------------ Anmeldung

    async def _anmelden(self) -> tuple[ConfigFlowResult | None, dict[str, str]]:
        """Anmelden; liefert das nächste Flow-Ergebnis oder Fehler fürs Formular."""
        ordner = await self.hass.async_add_executor_job(
            api.sitzungsordner, self.hass.config.config_dir, self._daten[CONF_APPLE_ID]
        )
        try:
            self._dienst = await self.hass.async_add_executor_job(
                api.anmelden,
                self._daten[CONF_APPLE_ID],
                self._daten[CONF_PASSWORD],
                ordner,
                self._daten.get(CONF_WITH_FAMILY, True),
            )
            zwei_fa = await self.hass.async_add_executor_job(api.braucht_2fa, self._dienst)
        except api.ICloudSecurityKeyError:
            return self.async_abort(reason="security_key"), {}
        except api.ICloudAuthError:
            return None, {"base": "invalid_auth"}
        except api.ICloudError:
            return None, {"base": "cannot_connect"}
        if zwei_fa:
            return await self.async_step_methode(), {}
        return await self._abschliessen(), {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_APPLE_ID] = user_input[CONF_APPLE_ID].strip().lower()
            await self.async_set_unique_id(user_input[CONF_APPLE_ID])
            self._abort_if_unique_id_configured()
            self._daten = user_input
            ergebnis, errors = await self._anmelden()
            if ergebnis is not None:
                return ergebnis
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA_USER, user_input),
            errors=errors,
        )

    # ------------------------------------------------------------------ 2FA

    async def async_step_methode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        wege = await self.hass.async_add_executor_job(api.zustellwege, self._dienst)
        if not wege:
            return self.async_abort(reason="no_2fa_method")
        nummer = await self.hass.async_add_executor_job(api.telefonnummer, self._dienst)
        return self.async_show_menu(
            step_id="methode",
            menu_options=wege,
            description_placeholders={
                "apple_id": self._daten[CONF_APPLE_ID],
                "nummer": nummer or "-",
            },
        )

    async def _senden(self, weg: str) -> ConfigFlowResult:
        try:
            await self.hass.async_add_executor_job(api.code_senden, self._dienst, weg)
        except api.ICloudError:
            return self.async_abort(reason="send_failed")
        self._weg = weg
        return await self.async_step_code()

    async def async_step_push(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._senden(METHOD_PUSH)

    async def async_step_sms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._senden(METHOD_SMS)

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            ok = await self.hass.async_add_executor_job(
                api.code_pruefen, self._dienst, user_input["code"]
            )
            if ok:
                return await self._abschliessen()
            errors["base"] = "invalid_code"
        return self.async_show_form(
            step_id="code",
            data_schema=SCHEMA_CODE,
            errors=errors,
            description_placeholders={
                "weg": "SMS" if getattr(self, "_weg", "") == METHOD_SMS else "Apple-Gerät",
            },
        )

    # ------------------------------------------------------------ Abschluss

    async def _abschliessen(self) -> ConfigFlowResult:
        daten = {
            CONF_APPLE_ID: self._daten[CONF_APPLE_ID],
            CONF_PASSWORD: self._daten[CONF_PASSWORD],
            CONF_WITH_FAMILY: self._daten.get(CONF_WITH_FAMILY, True),
        }
        if self.source == "reauth":
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=daten)
        return self.async_create_entry(title=daten[CONF_APPLE_ID], data=daten)

    # --------------------------------------------------------------- Reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._daten = {
                **entry.data,
                CONF_WITH_FAMILY: familie_aktiv(entry),
                # Leeres Feld = gespeichertes Passwort weiterverwenden
                CONF_PASSWORD: user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD],
            }
            ergebnis, errors = await self._anmelden()
            if ergebnis is not None:
                return ergebnis
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"apple_id": entry.data[CONF_APPLE_ID]},
        )


class ICloudNextOptionsFlow(OptionsFlow):
    """Abfrageintervall und Familie nachträglich ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        entry = self.config_entry
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                vol.Required(CONF_WITH_FAMILY, default=familie_aktiv(entry)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
