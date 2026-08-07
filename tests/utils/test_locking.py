"""Unit tests for LockManager and resolve_lockfile_path.

Tests verify lock acquire/release semantics via ``fcntl.flock``, no-op
mode for the ``"off"`` sentinel, lockfile path resolution precedence
(default ``/var/lib/qsnap/qsnap.lock`` when unconfigured), and
automatic creation of the lockfile's parent directory.  No source code
is modified.
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


def test_lockfile_path_resolution_config_when_no_cli():
    """Config path is used when no CLI path is given."""
    result = resolve_lockfile_path(None, "/var/lock/qsnap.lock")

    assert result == "/var/lock/qsnap.lock"


def test_default_lockfile_used_when_unconfigured():
    """resolve_lockfile_path(None, None) returns the default lockfile path.

    Locking must never silently disappear by omission — disabling
    requires the explicit ``"off"`` sentinel.
    """
    result = resolve_lockfile_path(None, None)

    assert result == "/var/lib/qsnap/qsnap.lock"


def test_lockfile_parent_dir_auto_created(tmp_path):
    """LockManager creates the lockfile's parent directory on acquire.

    The default lockfile lives under ``/var/lib/qsnap/``; the parent
    directory SHALL be created when missing.
    """
    lockpath = tmp_path / "nested" / "qsnap" / "qsnap.lock"

    mgr = LockManager(lockpath)
    assert mgr.acquire() is True
    assert lockpath.parent.is_dir()

    mgr.release()


def test_off_sentinel_disables_locking(tmp_path):
    """The ``"off"`` sentinel (CLI or config level) disables locking."""
    # Sentinel at the CLI level.
    assert resolve_lockfile_path("off", None) is None
    # Sentinel at the config level.
    assert resolve_lockfile_path(None, "off") is None
    # CLI "off" wins over a configured path.
    assert resolve_lockfile_path("off", "/var/lock/qsnap.lock") is None
    # LockManager(None) operates in no-op mode and creates no file.
    mgr = LockManager(None)

    assert mgr.acquire() is True
    assert mgr._lockfile is None

    mgr.release()
    assert not list(tmp_path.iterdir())
