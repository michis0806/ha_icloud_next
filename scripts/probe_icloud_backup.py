"""Probe iCloud Next's saved session and optionally round-trip disposable files.

Run with an isolated environment containing pyicloud and requests-toolbelt.
Without --write-test this only validates a COPY of the saved session and lists
Drive's root internally. No password login or device approval is attempted.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import resource
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from pyicloud import PyiCloudService
from pyicloud.services.drive import DriveService
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)

MIB = 1024 * 1024


def emit(event: str, **data: object) -> None:
    print(json.dumps({"event": event, **data}), flush=True)


class ApprovalRequired(RuntimeError):
    """A saved session cannot be used without a new interactive approval."""


class SessionOnlyService(PyiCloudService):
    """Never fall back to password login or request a notification."""

    def authenticate(self, *args, **kwargs):
        raise ApprovalRequired("Interactive login required")

    def request_2fa_code(self):
        raise ApprovalRequired("Interactive 2FA required")

    def _request_pcs_for_service(self, app_name):
        consent = self._check_pcs_consent()
        emit(
            "web_access_checked",
            approval_required=bool(consent.get("isICDRSDisabled", False)),
        )
        if consent.get("isICDRSDisabled", False):
            raise ApprovalRequired("Drive requires PCS approval")


class NamedReader:
    """Expose an upload filename without exposing the temporary local path."""

    def __init__(self, source, name):
        self.source = source
        self.name = name

    def __getattr__(self, name):
        return getattr(self.source, name)


class StreamingPosts:
    """Adapt requests' buffered multipart upload to a bounded-memory encoder."""

    def __init__(self, session, progress=None):
        self.session = session
        self.progress = progress
        self.original = session.post

    def __enter__(self):
        self.session.post = self.post
        return self

    def __exit__(self, *args):
        self.session.post = self.original

    def post(self, url, **kwargs):
        files = kwargs.pop("files", None)
        if files is not None:
            encoder = MultipartEncoder(
                fields={
                    name: (reader.name, reader, "application/octet-stream")
                    for name, reader in files.items()
                }
            )
            body = MultipartEncoderMonitor(
                encoder,
                lambda monitor: (
                    self.progress(monitor.bytes_read) if self.progress else None
                ),
            )
            kwargs["data"] = body
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                "Content-Type": body.content_type,
            }
        kwargs.setdefault("timeout", (15, 300))
        return self.original(url, **kwargs)


def connect(apple_id, session_dir):
    service = SessionOnlyService(
        apple_id,
        password="",
        cookie_directory=str(session_dir),
        authenticate=False,
        refresh_interval=None,
    )
    original_request = service.session.request

    def bounded_request(*args, **kwargs):
        kwargs.setdefault("timeout", (15, 60))
        return original_request(*args, **kwargs)

    service.session.request = bounded_request
    service.data = service._validate_token()
    service._update_state()
    if service.requires_2fa:
        service.session.close()
        raise ApprovalRequired("Saved session requires 2FA")
    return service


def drive_client(service):
    # Construct the client from Apple's validated routing without the convenience
    # property's implicit PCS notification/approval workflow.
    return DriveService(
        service_root=service.get_webservice_url("drivews"),
        document_root=service.get_webservice_url("docws"),
        session=service.session,
        params=service.params,
    )


def find_child(folder, name):
    matches = [node for node in folder.get_children(force=True) if node.name == name]
    if len(matches) != 1:
        raise RuntimeError("Test item not uniquely found")
    return matches[0]


def sha256_stream(chunks):
    digest = hashlib.sha256()
    size = 0
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def run(args):
    entries = json.loads(
        (args.config_dir / ".storage/core.config_entries").read_text()
    )["data"]["entries"]
    entries = [
        e for e in entries if e["domain"] == "icloud_next" and not e.get("disabled_by")
    ]
    if args.entry_id:
        entries = [e for e in entries if e["entry_id"] == args.entry_id]
    if len(entries) != 1:
        raise RuntimeError("Select exactly one enabled icloud_next account")
    apple_id = entries[0]["data"]["apple_id"]
    account_path = re.sub(r"[^a-z0-9]+", "_", apple_id.lower()).strip("_")
    original_dir = args.config_dir / ".storage/icloud_next" / account_path
    with tempfile.TemporaryDirectory(prefix="icloud-next-backup-probe-") as temporary:
        temporary = Path(temporary)
        session_dir = temporary / "session"
        shutil.copytree(original_dir, session_dir)
        session_dir.chmod(0o700)
        service = connect(apple_id, session_dir)
        emit(
            "session_validated",
            password_login=False,
            session_copy=True,
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )
        try:
            drive = drive_client(service)
            root = drive.root
            root.get_children(force=True)
            emit(
                "drive_accessible",
                peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            )
            if not args.write_test:
                return
            available = service.account.storage.usage.available_storage_in_bytes
            required = sum(args.size_mib) * MIB + MIB
            emit("storage_checked", available_bytes=available, required_bytes=required)
            if available < required:
                raise RuntimeError("Not enough free storage for this probe")
            folder_name = f"HomeAssistant-Backup-Probe-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
            root.mkdir(folder_name)
            emit("test_folder_created", name=folder_name)
            folder = find_child(root, folder_name)
            owned_names = set()
            try:
                for size_mib in args.size_mib:
                    name = f"payload-{size_mib}MiB.bin"
                    local = temporary / name
                    with local.open("wb") as output:
                        for _ in range(size_mib):
                            output.write(os.urandom(MIB))
                    with local.open("rb") as source:
                        expected, expected_size = sha256_stream(
                            iter(lambda: source.read(MIB), b"")
                        )
                    started = time.monotonic()
                    owned_names.add(name)
                    last_progress = [-1]

                    def progress(size, last_progress=last_progress, size_mib=size_mib):
                        milestone = size // (16 * MIB)
                        if milestone > last_progress[0]:
                            last_progress[0] = milestone
                            emit(
                                "upload_progress",
                                multipart_bytes=size,
                                size_mib=size_mib,
                            )

                    with (
                        local.open("rb") as source,
                        StreamingPosts(service.session, progress),
                    ):
                        folder.upload(NamedReader(source, name))
                    emit(
                        "upload_finished",
                        size_mib=size_mib,
                        seconds=round(time.monotonic() - started, 2),
                        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    )
                    metadata_name = f"{name}.metadata.json"
                    owned_names.add(metadata_name)
                    metadata = json.dumps(
                        {"filename": name, "sha256": expected, "size": expected_size}
                    ).encode()
                    emit("metadata_upload_started", size_mib=size_mib)
                    with StreamingPosts(service.session):
                        folder.upload(NamedReader(io.BytesIO(metadata), metadata_name))
                    # Reconstruct the client from persisted copied cookies, as after restart.
                    emit("client_reconnect_started", size_mib=size_mib)
                    service.session.close()
                    service = connect(apple_id, session_dir)
                    folder = find_child(drive_client(service).root, folder_name)
                    node = find_child(folder, name)
                    emit("download_started", size_mib=size_mib)
                    with node.open(stream=True, timeout=(15, 120)) as response:
                        response.raise_for_status()
                        actual, actual_size = sha256_stream(response.iter_content(MIB))
                    if (actual, actual_size) != (expected, expected_size):
                        raise RuntimeError("Downloaded content differs")
                    with find_child(folder, metadata_name).open(
                        timeout=(15, 60)
                    ) as response:
                        response.raise_for_status()
                        if response.json() != json.loads(metadata):
                            raise RuntimeError("Downloaded metadata differs")
                    emit(
                        "round_trip_verified",
                        size_mib=size_mib,
                        bytes=actual_size,
                        sha256=actual,
                        reconnected=True,
                        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    )
                    local.unlink()
            finally:
                emit("cleanup_started", name=folder_name)
                # Only remove names generated by this probe in its unique folder.
                for node in folder.get_children(force=True):
                    if node.name in owned_names:
                        node.delete()
                remaining = folder.get_children(force=True)
                if not remaining:
                    folder.delete()
                    emit("test_folder_deleted", name=folder_name)
                else:
                    emit("cleanup_incomplete", name=folder_name)
        finally:
            service.session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("/homeassistant"))
    parser.add_argument("--entry-id")
    parser.add_argument("--write-test", action="store_true")
    parser.add_argument("--size-mib", type=int, nargs="+", default=[1, 64])
    args = parser.parse_args()
    if any(size < 1 or size > 2048 for size in args.size_mib):
        parser.error("Test size must be between 1 and 2048 MiB")
    logging.disable(logging.CRITICAL)
    try:
        run(args)
    except Exception as err:  # noqa: BLE001 - never print secret-bearing API errors
        # API exception text may contain signed URLs or account identifiers.
        emit(
            "probe_failed",
            error_type=type(err).__name__,
            approval_required=isinstance(err, ApprovalRequired),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
