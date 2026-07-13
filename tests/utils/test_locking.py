"""Unit tests for LockManager and resolve_lockfile_path.

Tests verify lock acquire/release semantics via ``fcntl.flock``, no-op
mode when no lockfile is configured, and lockfile path resolution
precedence.  No source code is modified.
"""

from __future__ import annotations

import fcntl
import os

from qsnap.locking import LockManager, resolve_lockfile_path


def test_acquire_lock_when_free_returns_true(tmp_path):
    """Acquiring a lock on a free file returns True."""
    mgr = LockManager(tmp_path / "lock")

    assert mgr.acquire() is True

    mgr.release()


def test_acquire_lock_when_held_returns_false(tmp_path):
    """Acquiring a lock already held by another fd returns False."""
    lockpath = tmp_path / "lock"
    # Pre-acquire the lock with a raw fd.
    fd = os.open(str(lockpath), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # Now try LockManager — should fail.
    mgr = LockManager(lockpath)
    assert mgr.acquire() is False

    # Cleanup.
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_release_lock_allows_reacquisition(tmp_path):
    """After release, the same lock can be acquired again."""
    mgr = LockManager(tmp_path / "lock")

    assert mgr.acquire() is True
    mgr.release()
    assert mgr.acquire() is True

    mgr.release()


def test_lock_auto_released_on_process_termination(tmp_path):
    """A lock held by a child that exits is released on process death."""
    lockpath = tmp_path / "lock"
    pid = os.fork()
    if pid == 0:
        # Child: acquire and exit immediately.
        mgr = LockManager(lockpath)
        mgr.acquire()
        os._exit(0)
    else:
        os.waitpid(pid, 0)
        mgr = LockManager(lockpath)
        assert mgr.acquire() is True
        mgr.release()


def test_lockfile_path_resolution_cli_overrides_config():
    """CLI lockfile path takes precedence over config path."""
    result = resolve_lockfile_path("/run/qsnap.lock", "/var/lock/qsnap.lock")

    assert result == "/run/qsnap.lock"


def test_none_lockfile_path_means_no_locking(tmp_path):
    """LockManager(None) operates in no-op mode and creates no file."""
    mgr = LockManager(None)

    assert mgr.acquire() is True
    # No lockfile should have been created anywhere.
    assert mgr._lockfile is None

    mgr.release()


def test_lockfile_path_resolution_config_when_no_cli():
    """Config path is used when no CLI path is given."""
    result = resolve_lockfile_path(None, "/var/lock/qsnap.lock")

    assert result == "/var/lock/qsnap.lock"


def test_lockfile_path_resolution_none_when_both_none():
    """Returns None when neither CLI nor config path is provided."""
    result = resolve_lockfile_path(None, None)

    assert result is None
