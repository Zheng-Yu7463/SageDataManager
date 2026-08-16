from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from os import stat_result
from pathlib import Path, PurePath, PurePosixPath
from threading import Condition

from sqlalchemy import func, select
from sqlalchemy.orm import Session

UPLOAD_STAGING_DIRECTORY = ".uploads"
UPLOAD_PARTS_DIRECTORY = ".parts"
UPLOAD_LOCKS_DIRECTORY = ".sage-upload-locks"
MAX_ARCHIVE_RELATIVE_PATH_LENGTH = 1000
MAX_ARCHIVE_RELATIVE_PATH_BYTES = 1000
MAX_ARCHIVE_PATH_COMPONENT_LENGTH = 255
MAX_ARCHIVE_PATH_COMPONENT_BYTES = 255
_STORAGE_INDEX_LOCK_ID = 0x534147455343414E


class StorageIndexBusyError(Exception):
    pass


class _LocalStorageIndexLock:
    def __init__(self) -> None:
        self._condition = Condition()
        self._readers = 0
        self._writer = False

    @contextmanager
    def acquire(self, *, shared: bool) -> Iterator[None]:
        with self._condition:
            if self._writer or (not shared and self._readers > 0):
                raise StorageIndexBusyError
            if shared:
                self._readers += 1
            else:
                self._writer = True
        try:
            yield
        finally:
            with self._condition:
                if shared:
                    self._readers -= 1
                else:
                    self._writer = False
                self._condition.notify_all()


_local_storage_index_lock = _LocalStorageIndexLock()


def storage_index_lock_statement(*, shared: bool):
    lock_function = (
        func.pg_try_advisory_xact_lock_shared
        if shared
        else func.pg_try_advisory_xact_lock
    )
    return select(lock_function(_STORAGE_INDEX_LOCK_ID))


@contextmanager
def storage_index_guard(session: Session, *, shared: bool) -> Iterator[None]:
    with _local_storage_index_lock.acquire(shared=shared):
        if session.get_bind().dialect.name == "postgresql":
            acquired = session.scalar(storage_index_lock_statement(shared=shared))
            if not acquired:
                raise StorageIndexBusyError
        yield


def is_internal_storage_path(path: PurePath) -> bool:
    return bool(path.parts) and path.parts[0] in {
        UPLOAD_STAGING_DIRECTORY,
        UPLOAD_LOCKS_DIRECTORY,
    }


class StorageRootUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class StorageFileEntry:
    relative_path: PurePosixPath
    metadata: stat_result


def _stat_storage_entry(name: str, directory_descriptor: int) -> stat_result:
    return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)


def iter_storage_file_entries(
    storage_root: Path,
    *,
    on_skip: Callable[[], None] | None = None,
) -> Iterator[StorageFileEntry]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    root_descriptor = -1
    try:
        root = storage_root.resolve(strict=True)
        root_descriptor = os.open(root, directory_flags)
    except OSError:
        raise StorageRootUnavailableError from None

    def report_skip() -> None:
        if on_skip:
            on_skip()

    try:
        for directory_path, directory_names, file_names, directory_descriptor in os.fwalk(
            ".",
            topdown=True,
            onerror=lambda _error: report_skip(),
            follow_symlinks=False,
            dir_fd=root_descriptor,
        ):
            relative_directory = PurePosixPath(directory_path)
            for directory_name in tuple(directory_names):
                relative = relative_directory / directory_name
                if is_internal_storage_path(relative):
                    directory_names.remove(directory_name)
                    continue
                try:
                    metadata = _stat_storage_entry(directory_name, directory_descriptor)
                except OSError:
                    directory_names.remove(directory_name)
                    report_skip()
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    directory_names.remove(directory_name)
                    if stat.S_ISLNK(metadata.st_mode):
                        report_skip()
            for file_name in file_names:
                relative = relative_directory / file_name
                if is_internal_storage_path(relative):
                    continue
                try:
                    metadata = _stat_storage_entry(file_name, directory_descriptor)
                except OSError:
                    report_skip()
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    if stat.S_ISLNK(metadata.st_mode):
                        report_skip()
                    continue
                yield StorageFileEntry(relative_path=relative, metadata=metadata)
    finally:
        if root_descriptor >= 0:
            with suppress(OSError):
                os.close(root_descriptor)


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".doc", ".docx", ".md", ".txt", ".tex"}:
        return "document"
    if suffix in {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".nc"}:
        return "data"
    if suffix in {".pt", ".pth", ".bin", ".safetensors", ".ckpt"}:
        return "model-weight"
    if suffix in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
        return "image"
    return "other"
