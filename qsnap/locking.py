"""LockManager — non-blocking file lock via ``fcntl.flock``.

Provides a simple advisory lock mechanism so that only one ``qsnap``
pipeline runs at a time.  Uses ``fcntl.flock`` with ``LOCK_EX | LOCK_NB``
(non-blocking) — ``acquire()`` returns ``False`` immediately if the lock
is already held by another process.

Default lockfile is ``/var/lib/qsnap/qsnap.lock`` when not explicitly
configured.  The sentinel value ``"off"`` disables locking entirely.
"""

from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

_DEFAULT_LOCKFILE = "/var/lib/qsnap/qsnap.lock"


def resolve_lockfile_path(
    cli_path: str | None,
    config_path: str | None,
) -> str | None:
    """Resolve the effective lockfile path.

    Precedence: CLI ``--lockfile`` → ``GlobalConfig.lockfile`` →
    default ``/var/lib/qsnap/qsnap.lock``.  The sentinel ``"off"``
    (evaluated at each level) disables locking explicitly and returns
    ``None``.
    """
    # CLI override
    if cli_path is not None:
        if cli_path == "off":
            return None
        return cli_path
    # Config value
    if config_path is not None:
        if config_path == "off":
            return None
        return config_path
    # Default
    return _DEFAULT_LOCKFILE


class LockManager:
    """Manage a non-blocking advisory file lock.

    The lockfile path is resolved by the caller (CLI layer).  If the
    directory does not exist, it is created.  The lock is released on
    ``release()`` or when the file descriptor is closed (process exit).

    If ``None`` is passed as the lockfile path, the manager operates in
    no-op mode: ``acquire()`` always returns ``True`` and ``release()``
    is a no-op.
    """

    def __init__(self, lockfile: str | Path | None) -> None:
        self._lockfile: Path | None = Path(lockfile) if lockfile is not None else None
        self._fh: IO[str] | None = None
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to acquire an exclusive, non-blocking lock.

        Returns ``True`` if the lock was acquired (or if no lockfile is
        configured — no-op mode), ``False`` if it is already held by
        another process.
        """
        if self._lockfile is None:
            return True

        # Ensure the parent directory exists.
        self._lockfile.parent.mkdir(parents=True, exist_ok=True)

        # Open in read/write mode; create if it doesn't exist.
        # Keep a reference to the file handle so it is not garbage-collected
        # (which would close the underlying fd).
        self._fh = self._lockfile.open("a+")
        self._fd = self._fh.fileno()
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.debug("Acquired lock: %s", self._lockfile)
            return True
        except (BlockingIOError, OSError):
            logger.warning("Lock is held by another process: %s", self._lockfile)
            self._fh.close()
            self._fh = None
            self._fd = None
            return False

    def release(self) -> None:
        """Release the lock and close the file descriptor."""
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
            self._fd = None
            logger.debug("Released lock: %s", self._lockfile)
