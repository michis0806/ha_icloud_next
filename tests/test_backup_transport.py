"""Offline contract tests for the iCloud backup transport."""

import hashlib
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PACKAGE_NAME = "icloud_next_transport_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT / "custom_components/icloud_next")]
sys.modules[PACKAGE_NAME] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.backup_transport",
    ROOT / "custom_components/icloud_next/backup_transport.py",
)
transport = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = transport
spec.loader.exec_module(transport)


def backup_metadata(backup_id="backup-1", size=0):
    return {
        "addons": [],
        "backup_id": backup_id,
        "date": "2026-11-08T00:00:00+00:00",
        "database_included": True,
        "extra_metadata": {},
        "folders": [],
        "homeassistant_included": True,
        "homeassistant_version": "2026.11.0",
        "name": "test backup",
        "protected": False,
        "size": size,
    }


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        return None

    def iter_content(self, size):
        for index in range(0, len(self.content), size):
            yield self.content[index : index + size]


class FakeNode:
    def __init__(self, name, node_id, node_type="file", content=b""):
        self.name = name
        self.type = node_type
        self.data = {"drivewsid": node_id}
        self.content = content
        self.deleted = False
        self.upload_order = []
        self._children = []

    @property
    def size(self):
        return len(self.content) if self.type == "file" else None

    def get_children(self, force=False):
        return list(self._children)

    def upload(self, reader):
        self.upload_order.append(reader.name)
        chunks = []
        while True:
            chunk = reader.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        node = FakeNode(reader.name, f"FILE::{len(self._children)}", content=content)
        self._children.append(node)
        return node

    def open(self, **kwargs):
        return FakeResponse(self.content)

    def delete(self):
        self.deleted = True
        self._parent._children.remove(self)

    def add(self, child):
        child._parent = self
        self._children.append(child)
        return child


class FakeRoot(FakeNode):
    def __init__(self, children=()):
        super().__init__("root", "ROOT", "folder")
        for child in children:
            self.add(child)

    def mkdir(self, name):
        self.add(FakeNode(name, f"FOLDER::{len(self._children)}", "folder"))


class FakeDrive:
    def __init__(self, root):
        self.root = root
        self.on_progress = None


def make_store(folder=None, root=None, cancel=None):
    store = object.__new__(transport.ICloudBackupStore)
    store.apple_id = "test@example.invalid"
    store.session_files = {}
    store.namespace = "ha-test"
    store.cancel = cancel or threading.Event()
    store._temporary = None
    store._client = None
    store._drive = FakeDrive(root or FakeRoot())
    store._folder_node = folder
    return store


def owned_folder(namespace="ha-test"):
    folder = FakeNode(
        f"Home Assistant Backups - {namespace}", "FOLDER::owned", "folder"
    )
    marker = {"format": 1, "namespace": namespace}
    folder.add(
        FakeNode(
            transport.MARKER_NAME,
            "FILE::marker",
            content=json.dumps(marker).encode(),
        )
    )
    return folder


def test_upload_commits_marker_last_and_round_trips_metadata(tmp_path):
    payload = b"backup archive payload" * 2000
    source = tmp_path / "backup.tar"
    source.write_bytes(payload)
    folder = owned_folder()
    store = make_store(folder)
    progress = []
    metadata = backup_metadata(size=len(payload))

    store.upload_backup(metadata, source, progress.append)

    assert folder.upload_order[-2:] == [
        next(name for name in folder.upload_order if name.endswith(".tar")),
        f"ha-{hashlib.sha256(b'backup-1').hexdigest()}.metadata.json",
    ]
    assert progress[-1] == len(payload)
    assert store.get_backup("backup-1") == metadata
    assert store.list_backups() == [metadata]


def test_foreign_folder_is_never_claimed_or_modified():
    foreign = FakeNode("Home Assistant Backups - ha-test", "FOLDER::foreign", "folder")
    foreign.add(FakeNode("user-file.txt", "FILE::user", content=b"keep me"))
    root = FakeRoot([foreign])
    store = make_store(root=root)

    with pytest.raises(transport.BackupStoreError, match="not owned"):
        store._folder(create=True)

    assert [node.name for node in foreign.get_children()] == ["user-file.txt"]
    assert foreign.get_children()[0].content == b"keep me"


def test_failed_new_folder_marker_upload_removes_empty_folder_for_retry():
    root = FakeRoot()
    store = make_store(root=root)
    upload_json = store._upload_json
    failed_once = False

    def fail_first_marker_upload(folder, name, data):
        nonlocal failed_once
        if name == transport.MARKER_NAME and not failed_once:
            failed_once = True
            raise OSError("offline")
        upload_json(folder, name, data)

    store._upload_json = fail_first_marker_upload

    with pytest.raises(OSError, match="offline"):
        store._folder(create=True)

    assert root.get_children() == []
    folder = store._folder(create=True)
    assert folder.name == "Home Assistant Backups - ha-test"
    assert [node.name for node in folder.get_children()] == [transport.MARKER_NAME]


def test_failed_marker_upload_preserves_folder_if_foreign_content_appeared():
    root = FakeRoot()
    store = make_store(root=root)

    def add_foreign_content_then_fail(folder, name, data):
        assert name == transport.MARKER_NAME
        folder.add(FakeNode("user-file.txt", "FILE::user", content=b"keep me"))
        raise OSError("offline")

    store._upload_json = add_foreign_content_then_fail

    with pytest.raises(OSError, match="offline"):
        store._folder(create=True)

    [folder] = root.get_children()
    assert [node.name for node in folder.get_children()] == ["user-file.txt"]
    assert folder.get_children()[0].content == b"keep me"
    with pytest.raises(transport.BackupStoreError, match="not owned"):
        store._folder(create=True)


def test_uncommitted_and_corrupt_pairs_are_not_listed():
    folder = owned_folder()
    folder.add(FakeNode("uncommitted.tar", "FILE::orphan", content=b"orphan"))
    key = hashlib.sha256(b"backup-1").hexdigest()
    bad_marker = {
        "format": 1,
        "namespace": "ha-test",
        "backup": backup_metadata(size=4),
        "archive": "FILE::missing",
        "filename": f"ha-{key}-{'a' * 32}.tar",
        "size": 4,
        "sha256": "z" * 64,
    }
    folder.add(
        FakeNode(
            f"ha-{key}.metadata.json",
            "FILE::bad-metadata",
            content=json.dumps(bad_marker).encode(),
        )
    )
    store = make_store(folder)

    assert store.list_backups() == []


def test_unknown_backup_id_raises_not_found():
    store = make_store(owned_folder())

    with pytest.raises(transport.BackupStoreNotFound):
        store.get_backup("missing")
    with pytest.raises(transport.BackupStoreNotFound):
        store.delete_backup("missing")


def test_upload_rejects_size_mismatch_before_remote_changes(tmp_path):
    source = tmp_path / "backup.tar"
    source.write_bytes(b"actual")
    folder = owned_folder()
    store = make_store(folder)

    with pytest.raises(transport.BackupStoreIntegrityError):
        store.upload_backup(backup_metadata(size=99), source, lambda _size: None)

    assert len(folder.get_children()) == 1


def test_download_verifies_hash_and_removes_bad_partial_output(tmp_path):
    payload = b"original archive"
    folder = owned_folder()
    store = make_store(folder)
    source = tmp_path / "source.tar"
    source.write_bytes(payload)
    store.upload_backup(backup_metadata(size=len(payload)), source, lambda _size: None)
    archive = next(node for node in folder.get_children() if node.name.endswith(".tar"))
    archive.content = b"modified archive"
    target = tmp_path / "download.tar"

    with pytest.raises(transport.BackupStoreIntegrityError):
        store.download_backup("backup-1", target)

    assert not target.exists()


def test_cancelled_upload_removes_only_its_partial_archive(tmp_path):
    cancel = threading.Event()
    folder = owned_folder()
    store = make_store(folder, cancel=cancel)

    def cancel_on_progress(_size):
        cancel.set()

    store._drive.on_progress = cancel_on_progress
    source = tmp_path / "backup.tar"
    source.write_bytes(b"payload")

    # Simulate Drive's streaming callback observing cancellation during a read.
    class CancellingFolder(FakeNode):
        def upload(self, reader):
            first = reader.read(4)
            partial = self.add(FakeNode(reader.name, "FILE::partial", content=first))
            cancel.set()
            reader.read(4)
            return partial

    folder.upload = CancellingFolder.upload.__get__(folder, FakeNode)
    with pytest.raises(transport.BackupStoreCancelled):
        store.upload_backup(backup_metadata(size=7), source, cancel_on_progress)

    assert [node.name for node in folder.get_children()] == [transport.MARKER_NAME]
    assert cancel.is_set()


def test_backup_client_refuses_password_and_interactive_2fa():
    service = object.__new__(transport._SessionOnlyService)

    with pytest.raises(transport.BackupStoreAuthenticationError):
        service.authenticate()
    with pytest.raises(transport.BackupStoreAuthenticationError):
        service.request_2fa_code()
    with pytest.raises(transport.BackupStoreError):
        service._request_pcs_for_service("drive")
