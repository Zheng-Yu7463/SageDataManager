from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath
from threading import Condition

from sqlalchemy import func, select
from sqlalchemy.orm import Session

UPLOAD_STAGING_DIRECTORY = ".uploads"
UPLOAD_PARTS_DIRECTORY = ".parts"
UPLOAD_LOCKS_DIRECTORY = ".sage-upload-locks"
MAX_ARCHIVE_RELATIVE_PATH_LENGTH = 1000
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
