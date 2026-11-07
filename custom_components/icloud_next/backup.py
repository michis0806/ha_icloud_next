"""Home Assistant backup agent for iCloud Drive."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
import tempfile
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any, BinaryIO

from homeassistant.components.backup import (
    DOMAIN as BACKUP_DOMAIN,
)
from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
    OnProgressCallback,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.async_iterator import async_close_iterator

from . import api, gesperrt_melden
from .backup_path import backup_namespace, default_backup_path
from .backup_transport import (
    BackupStoreAuthenticationError,
    BackupStoreError,
    BackupStoreIntegrityError,
    BackupStoreLockedError,
    BackupStoreNotFound,
    BackupStorePathError,
    ICloudBackupStore,
)
from .const import CONF_APPLE_ID, CONF_BACKUP_PATH, DATA_BACKUP_AGENT_LISTENERS, DOMAIN

_LOGGER = logging.getLogger(__name__)
_CHUNK_SIZE = 1024 * 1024
_METADATA_CACHE_SECONDS = 300


async def async_get_backup_agents(
    hass: HomeAssistant, **kwargs: Any
) -> list[BackupAgent]:
    """Return one backup agent for each loaded iCloud Next account."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    agents: list[BackupAgent] = []
    for entry in entries:
        agent = entry.runtime_data.get("backup_agent")
        if agent is None:
            agent = ICloudNextBackupAgent(hass, entry)
            entry.runtime_data["backup_agent"] = agent
        agents.append(agent)
    return agents


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant,
    *,
    listener: Callable[[], None],
    **kwargs: Any,
) -> Callable[[], None]:
    """Register a callback for loaded-account changes."""
    listeners: list[Callable[[], None]] = hass.data.setdefault(
        DATA_BACKUP_AGENT_LISTENERS, []
    )
    listeners.append(listener)

    @callback
    def remove_listener() -> None:
        """Unregister the callback."""
        if listener in listeners:
            listeners.remove(listener)
        if not listeners:
            hass.data.pop(DATA_BACKUP_AGENT_LISTENERS, None)

    return remove_listener


class ICloudNextBackupAgent(BackupAgent):
    """Store Home Assistant backups in an account's private iCloud Drive area."""

    domain = DOMAIN

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        self._hass = hass
        self._entry = entry
        self._session = entry.runtime_data["sitzung"]
        self.name = entry.title
        self._namespace = backup_namespace(entry.data[CONF_APPLE_ID])
        self._backup_path = entry.options.get(
            CONF_BACKUP_PATH, default_backup_path(entry.data[CONF_APPLE_ID])
        )
        self.unique_id = self._namespace
        self._operation_lock = asyncio.Lock()
        self._cancel_events: set[threading.Event] = set()
        self._workers: set[asyncio.Future[Any]] = set()
        self._spools: set[Path] = set()
        self._shutting_down = False
        self._metadata_cache: list[AgentBackup] | None = None
        self._metadata_cache_until = 0.0

    async def async_upload_backup(
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        on_progress: OnProgressCallback,
        **kwargs: Any,
    ) -> None:
        """Spool the HA stream privately, then upload it from the executor."""
        async with self._operation_lock:
            self._ensure_available()
            try:
                await self._async_file_io(_ensure_spool_space, backup.size)
                path, file = await self._async_create_spool()
            except OSError as err:
                raise self._filesystem_error(err) from err
            size = 0
            stream: AsyncIterator[bytes] | None = None
            try:
                stream = await open_stream()
                async for chunk in stream:
                    self._ensure_available()
                    if not isinstance(chunk, bytes):
                        raise self._agent_error("operation_failed")
                    for offset in range(0, len(chunk), _CHUNK_SIZE):
                        part = chunk[offset : offset + _CHUNK_SIZE]
                        size += len(part)
                        if size > backup.size:
                            raise self._agent_error("operation_failed")
                        await self._async_file_io(file.write, part)
                await self._async_file_io(file.flush)
                await self._async_file_io(file.close)
                if size != backup.size:
                    raise self._agent_error("operation_failed")

                def report_progress(bytes_uploaded: int) -> None:
                    if bytes_uploaded < 0:
                        return
                    self._hass.loop.call_soon_threadsafe(
                        lambda: on_progress(bytes_uploaded=bytes_uploaded)
                    )

                self._invalidate_metadata()
                await self._async_store_call(
                    "upload_backup", backup.as_dict(), path, report_progress
                )
            except OSError as err:
                raise self._filesystem_error(err) from err
            finally:
                try:
                    if stream is not None:
                        await async_close_iterator(stream)
                finally:
                    try:
                        if not file.closed:
                            await self._async_file_io(file.close)
                    finally:
                        await self._async_remove_spool(path)

    async def async_download_backup(
        self, backup_id: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        """Download to a private spool and return a bounded async reader."""
        async with self._operation_lock:
            self._ensure_available()
            try:
                path, file = await self._async_create_spool()
            except OSError as err:
                raise self._filesystem_error(err) from err
            try:
                await self._async_file_io(file.close)
                await self._async_store_call("download_backup", backup_id, path)
            except OSError as err:
                await self._async_remove_spool(path)
                raise self._filesystem_error(err) from err
            except BaseException:
                await self._async_remove_spool(path)
                raise
        return self._async_read_spool(path)

    async def async_delete_backup(self, backup_id: str, **kwargs: Any) -> None:
        """Delete the named backup from iCloud Drive."""
        async with self._operation_lock:
            self._ensure_available()
            self._invalidate_metadata()
            await self._async_store_call("delete_backup", backup_id)

    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
        """Return complete, validated backup metadata records."""
        self._ensure_available()
        if self._session.abgelehnt:
            raise self._agent_error("auth_required")
        if (
            self._metadata_cache is not None
            and self._hass.loop.time() < self._metadata_cache_until
        ):
            return list(self._metadata_cache)
        async with self._operation_lock:
            self._ensure_available()
            records = await self._async_store_call("list_backups")
            backups = []
            for record in records:
                try:
                    backups.append(AgentBackup.from_dict(record))
                except (KeyError, TypeError, ValueError):
                    _LOGGER.warning("Ignoring invalid iCloud backup metadata")
            self._metadata_cache = backups
            self._metadata_cache_until = (
                self._hass.loop.time() + _METADATA_CACHE_SECONDS
            )
            return list(backups)

    def _invalidate_metadata(self) -> None:
        self._metadata_cache = None
        self._metadata_cache_until = 0.0

    async def async_get_backup(self, backup_id: str, **kwargs: Any) -> AgentBackup:
        """Return the metadata for a backup, or the standard not-found error."""
        async with self._operation_lock:
            self._ensure_available()
            record = await self._async_store_call("get_backup", backup_id)
            try:
                backup = AgentBackup.from_dict(record)
            except (KeyError, TypeError, ValueError) as err:
                raise self._agent_error("integrity_failed") from err
            if backup.backup_id != backup_id:
                raise self._agent_error("integrity_failed")
            return backup

    async def async_shutdown(self) -> None:
        """Stop active transport work and remove all private temporary files."""
        self._shutting_down = True
        for cancel in tuple(self._cancel_events):
            cancel.set()
        workers = tuple(self._workers)
        if workers:
            await asyncio.gather(
                *(self._wait_for_worker(worker) for worker in workers),
                return_exceptions=True,
            )
        async with self._operation_lock:
            for path in tuple(self._spools):
                await self._async_remove_spool(path)

    async def _async_store_call(self, operation: str, *args: Any) -> Any:
        """Run one blocking store operation using a locked session snapshot."""
        self._ensure_available()
        if self._session.abgelehnt:
            raise self._agent_error("auth_required")
        try:
            async with self._session.lock:
                if self._session.abgelehnt:
                    raise self._agent_error("auth_required")
                session_files = await self._hass.async_add_executor_job(
                    api.backup_sitzung_sichern, self._session._ordner
                )
        except OSError as err:
            raise self._filesystem_error(err) from err
        except api.ICloudError as err:
            raise self._agent_error("operation_failed") from err

        self._ensure_available()
        cancel = threading.Event()
        self._cancel_events.add(cancel)
        worker = self._hass.async_add_executor_job(
            self._execute_store_call, session_files, cancel, operation, args
        )
        self._workers.add(worker)
        worker.add_done_callback(self._observe_worker)
        try:
            return await self._wait_for_worker(worker, cancel)
        except BackupStoreLockedError as err:
            self._handle_locked_account()
            raise self._agent_error("account_locked") from err
        except BackupStoreAuthenticationError as err:
            if not self._session.abgelehnt:
                self._session.ablehnen(
                    api.ICloudAuthError("iCloud authentication expired")
                )
            raise self._agent_error("auth_required") from err
        except BackupStoreNotFound as err:
            raise BackupNotFound(
                translation_domain=BACKUP_DOMAIN,
                translation_key="backup_not_found",
                translation_placeholders={"backup_id": str(args[0]) if args else ""},
            ) from err
        except BackupStoreIntegrityError as err:
            raise self._agent_error("integrity_failed") from err
        except BackupStorePathError as err:
            raise self._agent_error("invalid_backup_destination") from err
        except BackupStoreError as err:
            raise self._agent_error("operation_failed") from err
        except OSError as err:
            raise self._filesystem_error(err) from err
        finally:
            cancel.set()
            self._cancel_events.discard(cancel)
            self._workers.discard(worker)

    def _execute_store_call(
        self,
        session_files: dict[str, bytes],
        cancel: threading.Event,
        operation: str,
        args: tuple[Any, ...],
    ) -> Any:
        """Call the synchronous transport inside its isolated session context."""
        with ICloudBackupStore(
            apple_id=self._entry.data[CONF_APPLE_ID],
            session_files=session_files,
            namespace=self._namespace,
            cancel=cancel,
            backup_path=self._backup_path,
        ) as store:
            return getattr(store, operation)(*args)

    async def _async_file_io(self, function: Callable[..., Any], *args: Any) -> Any:
        worker = self._hass.async_add_executor_job(function, *args)
        return await self._wait_for_worker(worker)

    async def _async_create_spool(self) -> tuple[Path, BinaryIO]:
        worker = self._hass.async_add_executor_job(_create_spool)
        try:
            path, file = await self._wait_for_worker(worker)
        except asyncio.CancelledError:
            if worker.done() and not worker.cancelled() and worker.exception() is None:
                path, file = worker.result()
                await self._async_file_io(file.close)
                await self._async_file_io(_remove_spool, path)
            raise
        self._spools.add(path)
        return path, file

    async def _async_remove_spool(self, path: Path) -> None:
        self._spools.discard(path)
        await self._async_file_io(_remove_spool, path)

    async def _async_read_spool(self, path: Path) -> AsyncIterator[bytes]:
        try:
            file = await self._async_file_io(path.open, "rb")
        except OSError as err:
            await self._async_remove_spool(path)
            raise self._filesystem_error(err) from err
        try:
            while chunk := await self._async_file_io(file.read, _CHUNK_SIZE):
                self._ensure_available()
                yield chunk
        finally:
            try:
                await self._async_file_io(file.close)
            finally:
                await self._async_remove_spool(path)

    async def _wait_for_worker(
        self,
        worker: asyncio.Future[Any],
        cancel: threading.Event | None = None,
    ) -> Any:
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
                if cancel is not None:
                    cancel.set()
            except Exception:  # noqa: BLE001 - propagate worker.result() below
                break
        if cancelled:
            if not worker.cancelled():
                worker.exception()
            raise asyncio.CancelledError
        return worker.result()

    @callback
    def _observe_worker(self, worker: asyncio.Future[Any]) -> None:
        if not worker.cancelled():
            worker.exception()

    def _ensure_available(self) -> None:
        if self._shutting_down:
            raise self._agent_error("operation_failed")

    def _handle_locked_account(self) -> None:
        self._session.abgelehnt = True
        self._session.abbrechen()
        gesperrt_melden(self._hass, self._entry)
        self._entry.async_start_reauth(self._hass)

    @staticmethod
    def _agent_error(translation_key: str) -> BackupAgentError:
        return BackupAgentError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
        )

    def _filesystem_error(self, err: OSError) -> BackupAgentError:
        key = "insufficient_space" if err.errno == errno.ENOSPC else "operation_failed"
        return self._agent_error(key)


def _create_spool() -> tuple[Path, BinaryIO]:
    fd, path = tempfile.mkstemp(prefix="icloud-next-backup-", dir="/tmp")
    return Path(path), os.fdopen(fd, "wb")


def _remove_spool(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _LOGGER.debug("Unable to remove private backup spool")


def _ensure_spool_space(size: int) -> None:
    if shutil.disk_usage("/tmp").free < size:
        raise OSError(errno.ENOSPC, "Not enough space for the backup spool")
