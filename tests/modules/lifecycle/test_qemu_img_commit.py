"""Tests for QemuImgCommitManager -- backing chain lifecycle via qemu-img commit.

These tests exercise ``QemuImgCommitManager`` (implements ``ILifecycleManager``)
in complete isolation.  All ``qemu-img`` calls go through a mocked ``IShell``.
The manager does NOT inherit from Core (design D1) and takes only ``IShell``
as a constructor dependency.

Test scenarios:

1. **Constructor accepts IShell** -- ``QemuImgCommitManager(shell=MockShell())``
   succeeds and the instance is usable.
2. **Is ILifecycleManager** -- ``issubclass(QemuImgCommitManager, ILifecycleManager)``.
3. **No Core inheritance** (design D1) -- ``not issubclass(QemuImgCommitManager, Core)``.
4. **Constructor requires shell** -- calling with no args raises ``TypeError``.
5. **Success** -- ``qemu-img commit -b <base> -d <top>`` returns exit 0,
   result is ``CommitResult(success=True)``.
6. **Failure** -- ``qemu-img commit`` returns non-zero, result is
   ``CommitResult(success=False, error=<stderr>)``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.interfaces.lifecycle import ILifecycleManager
from qsnap.interfaces.shell import IShell
from qsnap.models.results import CommitResult, ShellResult, SnapshotInfo
from qsnap.modules.lifecycle.qemu_img_commit import QemuImgCommitManager
from tests.mocks.mock_shell import MockShell

# ── Helpers ────────────────────────────────────────────────────────────────


class CountingShell(IShell):
    """Wrapper around an ``IShell`` that records every command passed to ``run()``.

    Delegates actual execution to the inner shell (typically ``MockShell``)
    while capturing the full command list for post-call assertions such as
    call-count verification and flag inspection.
    """

    def __init__(self, inner: IShell) -> None:
        self._inner = inner
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        self.calls.append(list(cmd))
        return self._inner.run(cmd, timeout, check)


def _make_snapshot(
    name: str = "testvm.20250101T000000",
    path: str = "/snapshots/testvm.20250101T000000.qcow2",
    timestamp: datetime | None = None,
    allocation: int = 65536,
) -> SnapshotInfo:
    """Create a ``SnapshotInfo`` with sensible defaults."""
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=timestamp or datetime(2025, 1, 1, 0, 0, 0),
        allocation=allocation,
    )


def _commit_calls(shell: CountingShell) -> list[list[str]]:
    """Extract only the qemu-img commit commands from recorded calls."""
    return [c for c in shell.calls if "qemu-img" in " ".join(c) and "commit" in c]


# ──────────────────────────────────────────────────────────────────────────
# 1. Constructor accepts IShell
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_constructor_accepts_ishell():
    """The constructor accepts an ``IShell`` instance and stores it.

    Passing a ``MockShell`` (which implements ``IShell``) must produce a
    usable ``QemuImgCommitManager`` instance without error.
    """
    shell = MockShell()
    manager = QemuImgCommitManager(shell=shell)
    assert manager is not None
    assert isinstance(manager, QemuImgCommitManager)


# ──────────────────────────────────────────────────────────────────────────
# 2. QemuImgCommitManager is an ILifecycleManager
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_is_ilifecycle_manager():
    """``QemuImgCommitManager`` is a subclass of ``ILifecycleManager``."""
    assert issubclass(QemuImgCommitManager, ILifecycleManager)


# ──────────────────────────────────────────────────────────────────────────
# 3. QemuImgCommitManager does NOT inherit from Core (design D1)
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_no_core_inheritance():
    """``QemuImgCommitManager`` does NOT inherit from ``Core`` (design D1).

    Modules are stateless workers that implement their ABC directly.
    """
    assert not issubclass(QemuImgCommitManager, Core)


# ──────────────────────────────────────────────────────────────────────────
# 4. Constructor requires an IShell argument
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_requires_shell():
    """Calling the constructor with no arguments raises ``TypeError``.

    The ``shell`` parameter is mandatory — there is no default.
    """
    with pytest.raises(TypeError):
        QemuImgCommitManager()  # type: ignore[call-arg]


# ──────────────────────────────────────────────────────────────────────────
# 5. Successful qemu-img commit
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_success(mock_shell: MockShell, make_vm_config):
    """A successful ``qemu-img commit`` yields ``CommitResult(success=True)``.

    - ``qemu-img commit -b <base> -d <top>`` returns exit 0.
    - Result: ``CommitResult(success=True, committed_snapshot=<snap.name>)``.
    - The command contains ``-b`` (base image) and ``-d`` (top snapshot).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap])

    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == snap.name
    assert result.error is None

    # Verify exactly one commit call with -b and -d flags.
    commit_calls = _commit_calls(shell)
    assert len(commit_calls) == 1

    cmd = commit_calls[0]
    assert "qemu-img" in cmd
    assert "commit" in cmd
    assert "-b" in cmd
    assert "-d" in cmd

    # Verify -b points to vm_config.base_image and -d to snapshot path.
    base_idx = cmd.index("-b")
    assert cmd[base_idx + 1] == str(vm_config.base_image)
    top_idx = cmd.index("-d")
    assert cmd[top_idx + 1] == str(snap.path)


# ──────────────────────────────────────────────────────────────────────────
# 6. qemu-img commit fails
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_fails(mock_shell: MockShell, make_vm_config):
    """A failed ``qemu-img commit`` yields ``CommitResult(success=False)``.

    - ``qemu-img commit`` returns non-zero exit code with an error message.
    - Result: ``CommitResult(success=False, error=<virsh error>)``.
    - ``committed_snapshot`` reflects the snapshot that failed.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    error_msg = "qemu-img: No space left on device"
    mock_shell.expect("qemu-img commit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap])

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.error == error_msg
    assert result.committed_snapshot == snap.name
