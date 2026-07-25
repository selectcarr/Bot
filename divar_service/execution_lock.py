from __future__ import annotations

import os
import time
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    """Raised when another Divar execution is active."""


class ExecutionLock:
    def __init__(
        self,
        lock_path: Path | str,
        stale_after_seconds: int = 7200,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.stale_after_seconds = (
            stale_after_seconds
        )
        self._acquired = False

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._remove_stale_lock()

        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
        )

        try:
            file_descriptor = os.open(
                self.lock_path,
                flags,
            )

        except FileExistsError as exc:
            raise AlreadyRunningError(
                "Another Divar execution is active."
            ) from exc

        lock_content = (
            f"pid={os.getpid()}\n"
            f"created_at={time.time()}\n"
        )

        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as lock_file:
            lock_file.write(
                lock_content
            )

        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return

        try:
            self.lock_path.unlink(
                missing_ok=True
            )
        finally:
            self._acquired = False

    def _remove_stale_lock(self) -> None:
        if not self.lock_path.exists():
            return

        try:
            lock_age = (
                time.time()
                - self.lock_path.stat().st_mtime
            )

        except OSError:
            return

        if lock_age <= self.stale_after_seconds:
            return

        self.lock_path.unlink(
            missing_ok=True
        )

    def __enter__(self) -> ExecutionLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.release()
