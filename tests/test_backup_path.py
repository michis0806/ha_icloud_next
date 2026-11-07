"""Backup path defaults, validation and real Home Assistant options flow."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

from icloud_next.backup_path import default_backup_path, normalize_backup_path
from icloud_next.config_flow import ICloudNextOptionsFlow
from icloud_next.const import CONF_APPLE_ID, CONF_BACKUP_PATH


@pytest.mark.parametrize(
    "path",
    [
        "",
        " / ",
        ".",
        "..",
        "Backups/../private",
        "a/./b",
        "a//b",
        "a\\b",
        "a\nb",
        "\nBackups",
        "a/\x00b",
        "x" * 256,
    ],
)
def test_invalid_backup_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        normalize_backup_path(path)


def test_nested_path_is_relative_to_drive_root() -> None:
    assert (
        normalize_backup_path(" /Backups/Home Assistant/ ") == "Backups/Home Assistant"
    )


@pytest.mark.asyncio
async def test_options_default_existing_folder_and_save_normalized_path(
    tmp_path: Path,
) -> None:
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        data={CONF_APPLE_ID: "account@example.invalid"},
        options={"scan_interval": 30, "with_family": False, "future_option": True},
    )
    hass.config_entries = SimpleNamespace(async_get_known_entry=lambda _id: entry)
    flow = ICloudNextOptionsFlow()
    flow.hass = hass
    flow.handler = "test-entry"

    form = await flow.async_step_init()
    assert form["type"] is FlowResultType.FORM
    defaults = form["data_schema"]({})
    assert defaults[CONF_BACKUP_PATH] == default_backup_path(entry.data[CONF_APPLE_ID])
    assert defaults["scan_interval"] == 30
    assert defaults["with_family"] is False

    submitted = {**defaults, CONF_BACKUP_PATH: "/Backups/Home Assistant/"}
    result = await flow.async_step_init(submitted)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKUP_PATH] == "Backups/Home Assistant"
    assert result["data"]["future_option"] is True
    assert result["data"]["scan_interval"] == 30
    assert result["data"]["with_family"] is False
    assert CONF_BACKUP_PATH not in entry.options

    entry.options = result["data"]
    reopened = await flow.async_step_init()
    assert reopened["data_schema"]({})[CONF_BACKUP_PATH] == "Backups/Home Assistant"


@pytest.mark.asyncio
async def test_invalid_path_keeps_options_form_and_user_input(tmp_path: Path) -> None:
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(data={CONF_APPLE_ID: "account@example.invalid"}, options={})
    hass.config_entries = SimpleNamespace(async_get_known_entry=lambda _id: entry)
    flow = ICloudNextOptionsFlow()
    flow.hass = hass
    flow.handler = "test-entry"
    result = await flow.async_step_init(
        {"scan_interval": 45, "with_family": False, CONF_BACKUP_PATH: "../private"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BACKUP_PATH: "invalid_backup_path"}
    assert entry.options == {}
    fields = result["data_schema"].schema
    path_field = next(key for key in fields if key.schema == CONF_BACKUP_PATH)
    assert path_field.description["suggested_value"] == "../private"
