"""Isolated, blocking iCloud Drive storage for Home Assistant backup agents.

Each operation uses a private copy of the saved session. The active Find My
client is never modified, and this client cannot fall back to password or 2FA.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlsplit

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloudAccountLockedException,
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudException,
    PyiCloudFailedLoginException,
)
from pyicloud.services.drive import DriveService
from pyicloud.session import PyiCloudSession
from requests.exceptions import RequestException
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)

_LOGGER = logging.getLogger(__name__)
CHUNK_SIZE = 1024 * 1024
MAX_METADATA_SIZE = 1024 * 1024
MARKER_NAME = "icloud-next-store.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
NAMESPACE_RE = re.compile(r"[a-zA-Z0-9_-]{1,64}")


class BackupStoreError(Exception):
    """An operation could not be completed; exception text contains no secrets."""


class BackupStoreAuthenticationError(BackupStoreError):
    """The saved session needs to be renewed by iCloud Next."""


class BackupStoreLockedError(BackupStoreAuthenticationError):
    """Apple has locked the account; password retries must stop."""


class BackupStoreNotFound(BackupStoreError):
    """The requested complete backup is absent."""


class BackupStoreIntegrityError(BackupStoreError):
    """A file does not match the committed backup size or checksum."""


class BackupStoreCancelled(BackupStoreError):
    """The caller cancelled the operation."""


def _operation(method):
    """Translate transport errors without exposing Apple response bodies/URLs."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except BackupStoreError:
            raise
        except PyiCloudAccountLockedException as err:
            raise BackupStoreLockedError("Apple account is locked") from err
        except (
            PyiCloud2FARequiredException,
            PyiCloudAuthRequiredException,
            PyiCloudFailedLoginException,
        ) as err:
            if getattr(args[0], "_validated", False):
                raise BackupStoreError("iCloud Drive access is not authorized") from err
            raise BackupStoreAuthenticationError(
                "Saved session is not authorized"
            ) from err
        except PyiCloudAPIResponseException as err:
            if str(err.code) in {"401", "409", "421", "450"} and not getattr(
                args[0], "_validated", False
            ):
                raise BackupStoreAuthenticationError(
                    "Saved session has expired"
                ) from err
            raise BackupStoreError("iCloud Drive request failed") from err
        except OSError as err:
            if err.errno == errno.ENOSPC:
                raise
            raise BackupStoreError("iCloud backup operation failed") from err
        except (
            PyiCloudException,
            RequestException,
            ValueError,
            KeyError,
            TypeError,
        ) as err:
            raise BackupStoreError("iCloud backup operation failed") from err

    return wrapped


class _BackupSession(PyiCloudSession):
    """Finite request timeouts and cancellation for the isolated backup client."""

    cancel: Event

    def request(self, method, url, **kwargs):
        if self.cancel.is_set():
            raise BackupStoreCancelled("Backup operation was cancelled")
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (15, 120)
        return super().request(method, url, **kwargs)


class _SessionOnlyService(PyiCloudService):
    def authenticate(self, *args, **kwargs):
        raise BackupStoreAuthenticationError("Interactive authentication is required")

    def request_2fa_code(self):
        raise BackupStoreAuthenticationError("Two-factor authentication is required")

    def _request_pcs_for_service(self, app_name):
        raise BackupStoreError("Drive requires an explicit device approval")


class _NamedReader:
    def __init__(self, source, name: str, cancel: Event):
        self.source = source
        self.name = name
        self.cancel = cancel

    def read(self, size=-1):
        if self.cancel.is_set():
            raise BackupStoreCancelled("Backup upload was cancelled")
        if size < 0:
            raise BackupStoreError("An unbounded backup read was requested")
        return self.source.read(size)

    def __getattr__(self, name):
        return getattr(self.source, name)


class _StreamingDrive(DriveService):
    """Drive's three-phase upload, with a bounded-memory multipart body."""

    cancel: Event
    on_progress: Callable[[int], None] | None = None

    def send_file(self, folder_id, file_object, zone="com.apple.CloudDocs", **kwargs):
        document_id, content_url = self._get_upload_contentws_url(file_object, zone)
        endpoint = urlsplit(content_url)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username:
            raise BackupStoreError("iCloud returned an invalid upload endpoint")
        encoder = MultipartEncoder(
            fields={
                file_object.name: (
                    file_object.name,
                    file_object,
                    "application/octet-stream",
                )
            }
        )
        position = file_object.tell()
        file_object.seek(0, 2)
        size = file_object.tell() - position
        file_object.seek(position)

        def progress(monitor):
            if self.cancel.is_set():
                raise BackupStoreCancelled("Backup upload was cancelled")
            if self.on_progress is not None:
                # Multipart accounting includes the envelope around the file.
                self.on_progress(min(size, monitor.bytes_read))

        monitor = MultipartEncoderMonitor(encoder, progress)
        response = self.session.post(
            content_url,
            data=monitor,
            headers={"Content-Type": monitor.content_type},
        )
        try:
            self._raise_if_error(response)
            info = response.json()["singleFile"]
        finally:
            response.close()
        if self.cancel.is_set():
            raise BackupStoreCancelled("Backup upload was cancelled")
        self._update_contentws(
            folder_id, info, document_id, file_object, zone, **kwargs
        )


def _backup_key(backup_id: str) -> str:
    if not isinstance(backup_id, str) or not backup_id or len(backup_id) > 512:
        raise BackupStoreError("Invalid backup identifier")
    return hashlib.sha256(backup_id.encode()).hexdigest()


def _valid_backup(data: Any) -> bool:
    fields = {
        "addons",
        "backup_id",
        "date",
        "database_included",
        "extra_metadata",
        "folders",
        "homeassistant_included",
        "homeassistant_version",
        "name",
        "protected",
        "size",
    }
    return (
        isinstance(data, dict)
        and fields <= data.keys()
        and isinstance(data["backup_id"], str)
        and bool(data["backup_id"])
        and len(data["backup_id"]) <= 512
        and isinstance(data["date"], str)
        and isinstance(data["name"], str)
        and isinstance(data["addons"], list)
        and isinstance(data["extra_metadata"], dict)
        and isinstance(data["folders"], list)
        and type(data["size"]) is int
        and data["size"] >= 0
        and type(data["protected"]) is bool
        and type(data["database_included"]) is bool
        and type(data["homeassistant_included"]) is bool
    )


class ICloudBackupStore:
    """Synchronous backup store; call only from a worker, never the event loop."""

    def __init__(
        self,
        apple_id: str,
        session_files: dict[str, bytes],
        namespace: str,
        cancel: Event,
    ):
        if not NAMESPACE_RE.fullmatch(namespace):
            raise BackupStoreError("Invalid backup namespace")
        self.apple_id = apple_id
        self.session_files = session_files
        self.namespace = namespace
        self.cancel = cancel
        self._temporary = None
        self._client = None
        self._drive = None
        self._folder_node = None
        self._validated = False

    @_operation
    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(
            prefix="icloud-next-backup-session-"
        )
        try:
            directory = Path(self._temporary.name)
            for name, content in self.session_files.items():
                if Path(name).name != name or not name.endswith(
                    (".session", ".cookiejar")
                ):
                    continue
                path = directory / name
                path.write_bytes(content)
                path.chmod(0o600)
            self._client = _SessionOnlyService(
                self.apple_id,
                password="",
                cookie_directory=str(directory),
                authenticate=False,
                refresh_interval=None,
            )
            original = self._client.session
            session = _BackupSession(
                self._client,
                verify=True,
                headers=dict(original.headers),
                client_id=self._client._client_id,
                cookie_directory=str(directory),
            )
            session.cancel = self.cancel
            self._client._session = session
            original.close()
            self._client.data = self._client._validate_token()
            self._client._update_state()
            if self._client.requires_2fa:
                raise BackupStoreAuthenticationError(
                    "Saved session requires two-factor authentication"
                )
            self._validated = True
            self._drive = _StreamingDrive(
                service_root=self._client.get_webservice_url("drivews"),
                document_root=self._client.get_webservice_url("docws"),
                session=session,
                params=self._client.params,
            )
            self._drive.cancel = self.cancel
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self._client is not None:
            self._client.session.close()
        if self._temporary is not None:
            self._temporary.cleanup()

    def _check_cancel(self):
        if self.cancel.is_set():
            raise BackupStoreCancelled("Backup operation was cancelled")

    def _children(self, node):
        self._check_cancel()
        return node.get_children(force=True)

    def _read_json(self, node):
        self._check_cancel()
        content = bytearray()
        with node.open(stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            for chunk in response.iter_content(64 * 1024):
                self._check_cancel()
                content.extend(chunk)
                if len(content) > MAX_METADATA_SIZE:
                    raise ValueError("Backup metadata exceeds the size limit")
        return json.loads(content)

    def _upload_json(self, folder, name, data):
        content = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        if len(content) > MAX_METADATA_SIZE:
            raise BackupStoreError("Backup metadata exceeds the size limit")
        self._drive.on_progress = None
        folder.upload(_NamedReader(io.BytesIO(content), name, self.cancel))

    def _folder(self, create=False):
        if self._folder_node is not None:
            return self._folder_node
        name = f"Home Assistant Backups - {self.namespace}"
        root = self._drive.root
        matches = [node for node in self._children(root) if node.name == name]
        if len(matches) > 1:
            raise BackupStoreError("The backup folder is not unique")
        created = False
        if not matches:
            if not create:
                return None
            root.mkdir(name)
            created = True
            matches = [node for node in self._children(root) if node.name == name]
        if len(matches) != 1 or matches[0].type != "folder":
            raise BackupStoreError("The backup folder is unavailable")
        folder = matches[0]
        markers = [node for node in self._children(folder) if node.name == MARKER_NAME]
        marker = {"format": 1, "namespace": self.namespace}
        if created:
            try:
                self._upload_json(folder, MARKER_NAME, marker)
            except BaseException:
                saved = self.cancel.is_set()
                self.cancel.clear()
                try:
                    if not self._children(folder):
                        folder.delete()
                except (PyiCloudException, RequestException, OSError, BackupStoreError):
                    _LOGGER.warning("Could not remove an empty iCloud backup folder")
                finally:
                    if saved:
                        self.cancel.set()
                raise
        elif len(markers) != 1 or self._read_json(markers[0]) != marker:
            raise BackupStoreError("The folder is not owned by this backup agent")
        self._folder_node = folder
        return folder

    def _records(self):
        folder = self._folder()
        if folder is None:
            return {}
        children = self._children(folder)
        by_id = {node.data["drivewsid"]: node for node in children}
        records = {}
        for node in children:
            if not re.fullmatch(r"ha-[0-9a-f]{64}\.metadata\.json", node.name):
                continue
            try:
                record = self._read_json(node)
                if (
                    not isinstance(record, dict)
                    or record.get("format") != 1
                    or record.get("namespace") != self.namespace
                ):
                    raise ValueError("Invalid backup format")
                backup = record.get("backup")
                if not _valid_backup(backup):
                    raise ValueError("Invalid backup metadata")
                key = _backup_key(backup["backup_id"])
                if node.name != f"ha-{key}.metadata.json":
                    raise ValueError("Invalid metadata name")
                archive = by_id.get(record.get("archive"))
                if archive is None:
                    continue
                if (
                    not isinstance(record.get("sha256"), str)
                    or not SHA256_RE.fullmatch(record["sha256"])
                    or not isinstance(record.get("filename"), str)
                    or not re.fullmatch(
                        rf"ha-{key}-[0-9a-f]{{32}}\.tar", record["filename"]
                    )
                    or archive.name != record["filename"]
                    or archive.type != "file"
                    or type(record.get("size")) is not int
                    or record["size"] != backup["size"]
                    or archive.size != record["size"]
                ):
                    raise ValueError("Invalid backup archive reference")
                records[backup["backup_id"]] = (record, archive, node)
            except (ValueError, KeyError, TypeError):
                _LOGGER.warning("Ignoring invalid iCloud backup metadata")
        return records

    @_operation
    def list_backups(self) -> list[dict]:
        return [record[0]["backup"] for record in self._records().values()]

    @_operation
    def get_backup(self, backup_id: str) -> dict:
        return self._record(backup_id)[0]["backup"]

    def _record(self, backup_id):
        _backup_key(backup_id)
        record = self._records().get(backup_id)
        if record is None:
            raise BackupStoreNotFound("Backup was not found")
        return record

    def _hash_file(self, path):
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                self._check_cancel()
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _remove_attempt(self, folder, archive_name, metadata_name):
        children = self._children(folder)
        for node in children:
            if node.name == metadata_name:
                try:
                    marker = self._read_json(node)
                except (ValueError, KeyError, TypeError):
                    continue
                if (
                    isinstance(marker, dict)
                    and marker.get("namespace") == self.namespace
                    and marker.get("filename") == archive_name
                ):
                    node.delete()
        for node in children:
            if node.name == archive_name:
                node.delete()

    @_operation
    def upload_backup(
        self, metadata: dict, source_path: Path, on_progress: Callable[[int], None]
    ):
        if not _valid_backup(metadata):
            raise BackupStoreError("Invalid backup metadata")
        checksum, size = self._hash_file(source_path)
        if size != metadata["size"]:
            raise BackupStoreIntegrityError(
                "Backup stream size differs from its metadata"
            )
        folder = self._folder(create=True)
        key = _backup_key(metadata["backup_id"])
        metadata_name = f"ha-{key}.metadata.json"
        existing = self._records().get(metadata["backup_id"])
        if existing is not None:
            if existing[0]["sha256"] == checksum and existing[0]["backup"] == metadata:
                on_progress(size)
                return
            raise BackupStoreError("A different backup already uses this identifier")
        # A corrupt/foreign marker must never be overwritten based on name alone.
        if any(node.name == metadata_name for node in self._children(folder)):
            raise BackupStoreError("Backup metadata filename is already occupied")
        name = f"ha-{key}-{uuid.uuid4().hex}.tar"
        try:
            self._drive.on_progress = on_progress
            with source_path.open("rb") as source:
                folder.upload(_NamedReader(source, name, self.cancel))
            self._drive.on_progress = None
            matches = [node for node in self._children(folder) if node.name == name]
            if len(matches) != 1 or matches[0].size != size:
                raise BackupStoreIntegrityError("Uploaded backup size differs")
            self._upload_json(
                folder,
                metadata_name,
                {
                    "format": 1,
                    "namespace": self.namespace,
                    "backup": metadata,
                    "archive": matches[0].data["drivewsid"],
                    "filename": name,
                    "size": size,
                    "sha256": checksum,
                },
            )
            committed = self._records().get(metadata["backup_id"])
            if committed is None or committed[0]["filename"] != name:
                raise BackupStoreError(
                    "A concurrent upload occupied the backup identifier"
                )
            on_progress(size)
        except BaseException:
            # The commit may have reached Apple even when its response was lost.
            # Only this attempt's unguessable archive and new metadata are removed.
            saved = self.cancel.is_set()
            self.cancel.clear()
            try:
                self._remove_attempt(folder, name, metadata_name)
            except (PyiCloudException, RequestException, OSError, BackupStoreError):
                _LOGGER.warning("Could not remove an incomplete iCloud backup")
            finally:
                if saved:
                    self.cancel.set()
            raise

    @_operation
    def download_backup(self, backup_id: str, target_path: Path):
        record, archive, _ = self._record(backup_id)
        if shutil.disk_usage(target_path.parent).free < record["size"]:
            raise OSError(errno.ENOSPC, "Not enough local storage for the backup")
        digest = hashlib.sha256()
        size = 0
        try:
            with (
                archive.open(stream=True, timeout=(15, 120)) as response,
                target_path.open("wb") as output,
            ):
                response.raise_for_status()
                for chunk in response.iter_content(CHUNK_SIZE):
                    self._check_cancel()
                    size += len(chunk)
                    if size > record["size"]:
                        raise BackupStoreIntegrityError(
                            "Downloaded backup is larger than expected"
                        )
                    output.write(chunk)
                    digest.update(chunk)
            if size != record["size"] or digest.hexdigest() != record["sha256"]:
                raise BackupStoreIntegrityError("Downloaded backup checksum differs")
        except BaseException:
            target_path.unlink(missing_ok=True)
            raise

    @_operation
    def delete_backup(self, backup_id: str):
        _, archive, metadata = self._record(backup_id)
        # Drop the commit marker first, so an interrupted delete cannot expose a
        # restorable-looking backup whose archive is already gone.
        metadata.delete()
        archive.delete()
