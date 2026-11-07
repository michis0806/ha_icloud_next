"""Offline tests for the Home Assistant iCloud Next backup agent."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
import pytest_asyncio
from homeassistant.components.backup import (
    AgentBackup,
    BackupAgentError,
    BackupNotFound,
)
from homeassistant.core import HomeAssistant

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

from icloud_next import api, backup
from icloud_next.backup_transport import (
    BackupStoreAuthenticationError,
    BackupStoreIntegrityError,
    BackupStoreNotFound,
)
from icloud_next.const import CONF_APPLE_ID, DATA_BACKUP_AGENT_LISTENERS, DOMAIN

APPLE_ID = " Test.User@example.invalid "
PAYLOAD = b"encrypted-backup-payload" * 90_000


@pytest_asyncio.fixture
async def hass(tmp_path: Path) -> HomeAssistant:
    """Create a real Home Assistant object on the pytest event loop."""
    return HomeAssistant(str(tmp_path))


class FakeSession:
    """Minimal saved-session state consumed by the real backup agent."""

    def __init__(self, config_dir: Path) -> None:
        self.lock = asyncio.Lock()
        self.abgelehnt = False
        self._ordner = str(config_dir)
        self.rejections: list[Exception] = []

    def ablehnen(self, error: Exception) -> None:
        self.abgelehnt = True
        self.rejections.append(error)

    def abbrechen(self) -> None:
        return None


class FakeEntry:
    """Config-entry state used by the agent without loading an integration."""

    def __init__(self, config_dir: Path) -> None:
        self.data = {CONF_APPLE_ID: APPLE_ID}
        self.entry_id = "entry-id-is-not-the-agent-id"
        self.title = "iCloud Next test account"
        self.runtime_data = {"sitzung": FakeSession(config_dir)}
        self.unload_callbacks: list[Any] = []
        self.reauth_started = False

    def async_on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def async_start_reauth(self, hass: HomeAssistant) -> None:
        self.reauth_started = True


class FakeBackend:
    """State shared by short-lived fake transport instances."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] | None = None
        self.uploaded_metadata: dict[str, Any] | None = None
        self.uploaded_payload: bytes | None = None
        self.download_payload = PAYLOAD
        self.error: Exception | None = None
        self.calls: list[str] = []
        self.progress_values = [len(PAYLOAD) // 2, len(PAYLOAD)]
        self.upload_started = threading.Event()
        self.upload_release = threading.Event()
        self.block_upload = False
        self.upload_path: Path | None = None
        self.constructor_args: list[dict[str, Any]] = []


class FakeStore:
    """Fake the synchronous iCloud transport while exercising the real agent."""

    def __init__(self, backend: FakeBackend, **kwargs: Any) -> None:
        self.backend = backend
        backend.constructor_args.append(kwargs)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def _raise_if_configured(self) -> None:
        if self.backend.error is not None:
            raise self.backend.error

    def upload_backup(
        self, metadata: dict[str, Any], source_path: Path, on_progress
    ) -> None:
        self.backend.calls.append("upload_backup")
        self.backend.upload_path = source_path
        self.backend.upload_started.set()
        if self.backend.block_upload:
            self.backend.upload_release.wait(timeout=5)
        self._raise_if_configured()
        self.backend.uploaded_metadata = metadata
        self.backend.uploaded_payload = source_path.read_bytes()
        for value in self.backend.progress_values:
            on_progress(value)

    def download_backup(self, backup_id: str, target_path: Path) -> None:
        self.backend.calls.append("download_backup")
        self._raise_if_configured()
        target_path.write_bytes(self.backend.download_payload)

    def list_backups(self) -> list[dict[str, Any]]:
        self.backend.calls.append("list_backups")
        self._raise_if_configured()
        return [self.backend.metadata] if self.backend.metadata else []

    def get_backup(self, backup_id: str) -> dict[str, Any]:
        self.backend.calls.append("get_backup")
        self._raise_if_configured()
        if self.backend.metadata is None:
            raise BackupStoreNotFound("not found")
        return self.backend.metadata

    def delete_backup(self, backup_id: str) -> None:
        self.backend.calls.append("delete_backup")
        self._raise_if_configured()


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Install an offline transport and deterministic session snapshot."""
    result = FakeBackend()
    monkeypatch.setattr(
        backup,
        "ICloudBackupStore",
        lambda **kwargs: FakeStore(result, **kwargs),
    )
    monkeypatch.setattr(
        api, "backup_sitzung_sichern", lambda _folder: {"session": b"saved"}
    )
    return result


def metadata(size: int = len(PAYLOAD)) -> AgentBackup:
    """Return a real HA metadata instance, marked as encrypted."""
    return AgentBackup(
        addons=[],
        backup_id="backup-id-1",
        date="2026-11-08T12:00:00+00:00",
        database_included=True,
        extra_metadata={"source": "test"},
        folders=[],
        homeassistant_included=True,
        homeassistant_version="2026.11.0",
        name="test archive",
        protected=True,
        size=size,
    )


def make_agent(
    hass: HomeAssistant, tmp_path: Path
) -> tuple[backup.ICloudNextBackupAgent, FakeEntry]:
    entry = FakeEntry(tmp_path)
    return backup.ICloudNextBackupAgent(hass, entry), entry


async def open_payload(payload: bytes) -> Any:
    async def iterator():
        yield payload[:1_300_000]
        yield payload[1_300_000:]

    return iterator()


@pytest.mark.asyncio
async def test_upload_preserves_stream_metadata_and_event_loop_progress(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    item = metadata()
    progress: list[int] = []
    progress_threads: list[int] = []
    progress_event = asyncio.Event()

    def on_progress(*, bytes_uploaded: int, **kwargs: Any) -> None:
        progress.append(bytes_uploaded)
        progress_threads.append(threading.get_ident())
        progress_event.set()

    await agent.async_upload_backup(
        open_stream=lambda: open_payload(PAYLOAD),
        backup=item,
        on_progress=on_progress,
    )
    await asyncio.wait_for(progress_event.wait(), timeout=1)

    assert backend.uploaded_payload == PAYLOAD
    assert backend.uploaded_metadata == item.as_dict()
    assert backend.uploaded_metadata["protected"] is True
    assert progress == backend.progress_values
    assert set(progress_threads) == {threading.get_ident()}
    assert backend.upload_path is not None and not backend.upload_path.exists()
    assert agent._spools == set()


@pytest.mark.asyncio
async def test_download_streams_data_and_removes_spool(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    stream = await agent.async_download_backup("backup-id-1")
    path = next(iter(agent._spools))
    chunks = [chunk async for chunk in stream]

    assert b"".join(chunks) == PAYLOAD
    assert all(len(chunk) <= 1024 * 1024 for chunk in chunks)
    assert not path.exists()
    assert agent._spools == set()


@pytest.mark.asyncio
async def test_not_found_auth_and_integrity_errors_are_translated(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, entry = make_agent(hass, tmp_path)

    backend.error = BackupStoreNotFound("missing")
    with pytest.raises(BackupNotFound):
        await agent.async_get_backup("absent")

    backend.error = BackupStoreAuthenticationError("expired")
    with pytest.raises(BackupAgentError) as auth_error:
        await agent.async_get_backup("backup-id-1")
    assert auth_error.value.translation_key == "auth_required"
    assert entry.runtime_data["sitzung"].abgelehnt
    assert len(entry.runtime_data["sitzung"].rejections) == 1
    assert (
        api.ICloudAuthError in type(entry.runtime_data["sitzung"].rejections[0]).__mro__
    )

    entry.runtime_data["sitzung"].abgelehnt = False
    backend.error = BackupStoreIntegrityError("corrupt")
    with pytest.raises(BackupAgentError) as integrity_error:
        await agent.async_get_backup("backup-id-1")
    assert integrity_error.value.translation_key == "integrity_failed"


@pytest.mark.asyncio
async def test_locked_account_stops_relogin_timer_and_starts_reauth(
    hass: HomeAssistant,
    tmp_path: Path,
    backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from icloud_next.backup_transport import BackupStoreLockedError

    agent, entry = make_agent(hass, tmp_path)
    notified: list[Any] = []
    monkeypatch.setattr(
        backup, "gesperrt_melden", lambda _hass, value: notified.append(value)
    )
    backend.error = BackupStoreLockedError("locked")

    with pytest.raises(BackupAgentError) as error:
        await agent.async_get_backup("backup-id-1")

    assert error.value.translation_key == "account_locked"
    assert entry.runtime_data["sitzung"].abgelehnt
    assert notified == [entry]
    assert entry.reauth_started


@pytest.mark.asyncio
async def test_rejected_session_does_not_contact_transport(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend, monkeypatch
) -> None:
    agent, entry = make_agent(hass, tmp_path)
    entry.runtime_data["sitzung"].abgelehnt = True
    snapshots: list[str] = []
    monkeypatch.setattr(
        api, "backup_sitzung_sichern", lambda folder: snapshots.append(folder)
    )

    with pytest.raises(BackupAgentError) as error:
        await agent.async_list_backups()

    assert error.value.translation_key == "auth_required"
    assert not backend.calls
    assert not snapshots


@pytest.mark.asyncio
async def test_agent_id_is_stable_hashed_and_listener_unregisters(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = FakeEntry(tmp_path)
    hass.config_entries = SimpleNamespace(async_loaded_entries=lambda domain: [entry])

    agents = await backup.async_get_backup_agents(hass)
    again = await backup.async_get_backup_agents(hass)
    agent = agents[0]
    expected_id = hashlib.sha256(APPLE_ID.strip().lower().encode()).hexdigest()[:24]

    assert agent is again[0]
    assert agent.unique_id == expected_id
    assert agent.agent_id == f"{DOMAIN}.{expected_id}"
    assert APPLE_ID.strip().lower() not in agent.agent_id
    assert entry.runtime_data["backup_agent"] is agent

    called: list[bool] = []
    remove = backup.async_register_backup_agents_listener(
        hass, listener=lambda: called.append(True)
    )
    hass.data[DATA_BACKUP_AGENT_LISTENERS][0]()
    remove()
    assert called == [True]
    assert DATA_BACKUP_AGENT_LISTENERS not in hass.data


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_before_removing_upload_spool(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    backend.block_upload = True
    task = asyncio.create_task(
        agent.async_upload_backup(
            open_stream=lambda: open_payload(PAYLOAD),
            backup=metadata(),
            on_progress=lambda **kwargs: None,
        )
    )
    assert await asyncio.to_thread(backend.upload_started.wait, 2)
    assert backend.upload_path is not None and backend.upload_path.exists()

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert backend.upload_path.exists()

    backend.upload_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not backend.upload_path.exists()
    assert agent._spools == set()


@pytest.mark.asyncio
async def test_shutdown_during_upload_waits_and_cleans_spools(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    backend.block_upload = True
    upload = asyncio.create_task(
        agent.async_upload_backup(
            open_stream=lambda: open_payload(PAYLOAD),
            backup=metadata(),
            on_progress=lambda **kwargs: None,
        )
    )
    assert await asyncio.to_thread(backend.upload_started.wait, 2)
    assert backend.upload_path is not None and backend.upload_path.exists()

    shutdown = asyncio.create_task(agent.async_shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert backend.upload_path.exists()
    backend.upload_release.set()
    await upload
    await shutdown

    assert backend.upload_path is not None and not backend.upload_path.exists()
    assert agent._spools == set()


@pytest.mark.asyncio
async def test_shutdown_during_session_snapshot_prevents_transport_start(
    hass: HomeAssistant,
    tmp_path: Path,
    backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    snapshot_started = threading.Event()
    release_snapshot = threading.Event()

    def blocked_snapshot(_folder: str) -> dict[str, bytes]:
        snapshot_started.set()
        release_snapshot.wait(timeout=3)
        return {"session": b"saved"}

    monkeypatch.setattr(api, "backup_sitzung_sichern", blocked_snapshot)
    upload = asyncio.create_task(
        agent.async_upload_backup(
            open_stream=lambda: open_payload(PAYLOAD),
            backup=metadata(),
            on_progress=lambda **kwargs: None,
        )
    )
    assert await asyncio.to_thread(snapshot_started.wait, 2)
    spool_path = next(iter(agent._spools))
    assert spool_path.exists()

    shutdown = asyncio.create_task(agent.async_shutdown())
    await asyncio.sleep(0)
    assert agent._shutting_down
    release_snapshot.set()

    with pytest.raises(BackupAgentError):
        await upload
    await shutdown

    assert not backend.calls
    assert not backend.constructor_args
    assert agent._workers == set()
    assert agent._spools == set()
    assert not spool_path.exists()


@pytest.mark.asyncio
async def test_metadata_cache_is_invalidated_by_delete(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    backend.metadata = metadata().as_dict()
    assert await agent.async_list_backups() == [metadata()]
    assert await agent.async_list_backups() == [metadata()]
    assert backend.calls == ["list_backups"]
    await agent.async_delete_backup(metadata().backup_id)
    backend.metadata = None
    assert await agent.async_list_backups() == []
    assert backend.calls == ["list_backups", "delete_backup", "list_backups"]


@pytest.mark.asyncio
async def test_incomplete_stream_is_rejected_before_cloud_upload(
    hass: HomeAssistant, tmp_path: Path, backend: FakeBackend
) -> None:
    agent, _entry = make_agent(hass, tmp_path)
    with pytest.raises(BackupAgentError):
        await agent.async_upload_backup(
            open_stream=lambda: open_payload(PAYLOAD[:-1]),
            backup=metadata(),
            on_progress=lambda **kwargs: None,
        )
    assert backend.calls == []
    assert not agent._spools


@pytest.mark.asyncio
async def test_full_temporary_storage_is_reported_before_upload(
    hass: HomeAssistant,
    tmp_path: Path,
    backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import errno

    agent, _entry = make_agent(hass, tmp_path)

    def no_space(size: int) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(backup, "_ensure_spool_space", no_space)
    with pytest.raises(BackupAgentError) as error:
        await agent.async_upload_backup(
            open_stream=lambda: open_payload(PAYLOAD),
            backup=metadata(),
            on_progress=lambda **kwargs: None,
        )
    assert error.value.translation_key == "insufficient_space"
    assert backend.calls == []
    assert not agent._spools
