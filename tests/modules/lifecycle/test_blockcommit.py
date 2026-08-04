"""Tests for BlockCommitManager -- backing chain lifecycle management.

These tests exercise ``BlockCommitManager`` (implements ``ILifecycleManager``)
in complete isolation.  All ``virsh`` calls go through a mocked ``IShell``.
The manager does NOT inherit from Core (design D1) and takes only ``IShell``
as a constructor dependency.

Test scenarios (per spec: lifecycle-manager/spec.md):

1. **Single snapshot success** -- domblklist resolves target "vda",
   blockcommit returns exit 0, result is ``CommitResult(success=True)``.
2. **Virsh error** -- blockcommit returns non-zero exit code, result is
   ``CommitResult(success=False, error=<stderr>)``.
3. **Empty list no-op** -- empty ``snapshots_to_merge`` produces success
   with ``committed_snapshot=""`` and zero shell calls.
4. **Timeout** -- blockcommit ``ShellResult`` has ``success=False`` with
   error containing "timed out".
5. **Multiple snapshots sequential** (design D4) -- snapshots merged one at
   a time, oldest first.  Short-circuits on first failure: remaining
   snapshots are NOT attempted.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import CommitResult, ShellResult, SnapshotInfo
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from tests.mocks.mock_shell import MockShell

# ── Helpers ────────────────────────────────────────────────────────────────

# Standard domblklist output with a single disk target "vda".
_DOMBLKLIST_OUTPUT = (
    " Target   Source\n"
    "------------------------------------\n"
    " vda      /var/lib/libvirt/images/testvm.qcow2\n"
)


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

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        self.calls.append(list(cmd))
        return self._inner.run_with_stall_detection(
            cmd, output_file=output_file, stall_timeout=stall_timeout, check=check
        )


def _make_snapshot(
    name: str = "testvm.20250101T000000",
    path: str = "/snapshots/testvm.20250101T000000.qcow2",
    timestamp: datetime | None = None,
    allocation: int = 65536,
    disk: str = "vda",
) -> SnapshotInfo:
    """Create a ``SnapshotInfo`` with sensible defaults."""
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=timestamp or datetime(2025, 1, 1, 0, 0, 0),
        allocation=allocation,
        disk=disk,
    )


def _blockcommit_calls(shell: CountingShell) -> list[list[str]]:
    """Extract only the blockcommit commands from recorded calls."""
    return [c for c in shell.calls if "blockcommit" in " ".join(c)]


# ──────────────────────────────────────────────────────────────────────────
# 1. Successful blockcommit of a single snapshot
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_single_snapshot_success(mock_shell: MockShell, make_vm_config):
    """A single snapshot is merged successfully.

    - ``virsh domblklist`` returns output with target "vda".
    - ``virsh blockcommit`` returns exit 0.
    - Result: ``CommitResult(success=True, committed_snapshot=<snap.name>)``.
    - The blockcommit command contains ``--base``, ``--top``, ``--delete``,
      ``--verbose``, ``--wait``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == snap.name
    assert result.error is None

    # Verify exactly one blockcommit call with all required flags.
    bc_calls = _blockcommit_calls(shell)
    assert len(bc_calls) == 1

    cmd = bc_calls[0]
    assert "--base" in cmd
    assert "--top" in cmd
    assert "--delete" in cmd
    assert "--verbose" in cmd
    assert "--wait" in cmd

    # Verify --base points to vm_config.disks[0].base_image and --top to snapshot path.
    base_idx = cmd.index("--base")
    assert cmd[base_idx + 1] == str(vm_config.disks[0].base_image)
    top_idx = cmd.index("--top")
    assert cmd[top_idx + 1] == str(snap.path)


# ──────────────────────────────────────────────────────────────────────────
# 2. Blockcommit fails -- virsh returns non-zero exit code
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_virsh_error(mock_shell: MockShell, make_vm_config):
    """A non-zero exit code from virsh blockcommit yields a failure result.

    - ``domblklist`` returns success.
    - ``blockcommit`` returns exit 1 with an error message.
    - Result: ``CommitResult(success=False, error=<error from virsh>)``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    error_msg = "error: operation failed: blockcommit"
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=error_msg,
            returncode=1,
            error=error_msg,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is False
    assert result.error == error_msg
    assert result.committed_snapshot == snap.name


# ──────────────────────────────────────────────────────────────────────────
# 3. Empty snapshot list -- nothing to merge
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_empty_list_no_op(mock_shell: MockShell, make_vm_config):
    """An empty snapshot list is a no-op.

    - No ``domblklist`` call, no ``blockcommit`` call.
    - Result: ``CommitResult(success=True, committed_snapshot="")``.
    """
    vm_config = make_vm_config()

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is True
    assert result.committed_snapshot == ""
    assert result.error is None

    # No shell commands should have been executed at all.
    assert len(shell.calls) == 0


# ──────────────────────────────────────────────────────────────────────────
# 4. Blockcommit times out
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_timeout(mock_shell: MockShell, make_vm_config):
    """A blockcommit timeout yields ``CommitResult(success=False)`` with "timed out".

    - ``domblklist`` returns success.
    - ``blockcommit`` returns ``ShellResult(success=False)`` with error
      containing "timed out".
    - Result: ``CommitResult(success=False)`` with error containing "timed out".
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="virsh blockcommit timed out after 3600 seconds",
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is False
    assert "timed out" in result.error


# ──────────────────────────────────────────────────────────────────────────
# 5. Multiple snapshots merged sequentially (design D4)
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_multiple_snapshots_sequential(mock_shell: MockShell, make_vm_config):
    """Multiple snapshots are merged one at a time (design D4).

    Success path:
    - ``[snap1, snap2]`` -> blockcommit called for snap1, then snap2.
    - Result: ``CommitResult(success=True, committed_snapshot=snap2.name)``.
    - blockcommit is called exactly twice (oldest first).

    Failure path (short-circuit, design D4):
    - If blockcommit for snap1 fails, snap2 is NOT attempted.
    - Result: ``CommitResult(success=False, committed_snapshot=snap1.name)``.
    - blockcommit is called exactly once (for snap1 only).
    """
    vm_config = make_vm_config()
    snap1 = _make_snapshot(
        name="testvm.20250101T000000",
        path="/snapshots/testvm.20250101T000000.qcow2",
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
    )
    snap2 = _make_snapshot(
        name="testvm.20250102T000000",
        path="/snapshots/testvm.20250102T000000.qcow2",
        timestamp=datetime(2025, 1, 2, 0, 0, 0),
        allocation=131072,
    )

    # ── Success path: both snapshots merged ───────────────────────────

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap1, snap2], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is True
    assert result.committed_snapshot == snap2.name  # last merged
    assert result.error is None

    # blockcommit called exactly twice (once per snapshot, oldest first).
    bc_calls = _blockcommit_calls(shell)
    assert len(bc_calls) == 2

    # First call targets snap1 (oldest), second targets snap2.
    assert str(snap1.path) in bc_calls[0]
    assert str(snap2.path) in bc_calls[1]

    # ── Failure path: short-circuit on first failure (design D4) ──────
    # If the first blockcommit fails, the second is NOT executed.

    fail_shell = MockShell()
    fail_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    fail_error = "error: blockcommit failed for snap1"
    fail_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=fail_error,
            returncode=1,
            error=fail_error,
        )
    )

    fail_counting = CountingShell(fail_shell)
    fail_manager = BlockCommitManager(shell=fail_counting)

    fail_result = fail_manager.blockcommit(
        vm_config, [snap1, snap2], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert fail_result.success is False
    assert fail_result.committed_snapshot == snap1.name  # the one that failed
    assert fail_result.error == fail_error

    # Only one blockcommit call (for snap1); snap2 was NOT attempted.
    fail_bc_calls = _blockcommit_calls(fail_counting)
    assert len(fail_bc_calls) == 1
    assert str(snap1.path) in fail_bc_calls[0]


# ──────────────────────────────────────────────────────────────────────────
# 6. MAC denial — AppArmor blocks blockcommit
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_blocked_by_apparmor(mock_shell: MockShell, make_vm_config):
    """An AppArmor denial is detected and reported as a MAC failure.

    - ``domblklist`` returns success.
    - ``blockcommit`` returns non-zero with stderr containing
      "Permission denied" and "apparmor".
    - Result: ``CommitResult(success=False, error="blocked by apparmor")``.
    - ``committed_snapshot`` is empty (no snapshot was merged).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    apparmor_stderr = (
        "error: Failed to pivot snapshot: Permission denied\n"
        "libvirt: AppArmor denial: cannot access /var/lib/libvirt/images"
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=apparmor_stderr,
            returncode=1,
            error=apparmor_stderr,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is False
    assert result.error == "blocked by apparmor"
    assert result.committed_snapshot == ""


# ──────────────────────────────────────────────────────────────────────────
# 7. MAC denial — SELinux blocks blockcommit
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_blocked_by_selinux(mock_shell: MockShell, make_vm_config):
    """An SELinux denial is detected and reported as a MAC failure.

    - ``domblklist`` returns success.
    - ``blockcommit`` returns non-zero with stderr containing
      "Operation not permitted" and "AVC".
    - Result: ``CommitResult(success=False, error="blocked by selinux")``.
    - ``committed_snapshot`` is empty (no snapshot was merged).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    selinux_stderr = (
        "error: internal error: Operation not permitted\nSELinux: AVC denied: { read } for qemu"
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=selinux_stderr,
            returncode=1,
            error=selinux_stderr,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is False
    assert result.error == "blocked by selinux"
    assert result.committed_snapshot == ""


# ──────────────────────────────────────────────────────────────────────────
# 8. AppArmor denial error string enables Core deferral
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_blocked_by_apparmor_returns_deferred(mock_shell: MockShell, make_vm_config):
    """The AppArmor error string enables Core's deferral logic.

    Core defers a blockcommit when ``result.error`` contains "apparmor" or
    "selinux" (see ``Core._execute_blockcommit_steps``).  This test
    verifies that the error string produced by ``BlockCommitManager``
    on an AppArmor denial contains the "apparmor" keyword so that Core
    can detect it and queue a ``DeferredBlockcommit``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    apparmor_stderr = "Permission denied: apparmor profile violation"
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=apparmor_stderr,
            returncode=1,
            error=apparmor_stderr,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    # Core's deferral condition: not success AND error contains "apparmor".
    assert result.success is False
    assert result.error is not None
    assert "apparmor" in result.error


# ──────────────────────────────────────────────────────────────────────────
# 9. SELinux denial error string enables Core deferral
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_blocked_by_selinux_returns_deferred(mock_shell: MockShell, make_vm_config):
    """The SELinux error string enables Core's deferral logic.

    Core defers a blockcommit when ``result.error`` contains "apparmor" or
    "selinux" (see ``Core._execute_blockcommit_steps``).  This test
    verifies that the error string produced by ``BlockCommitManager``
    on an SELinux denial contains the "selinux" keyword so that Core
    can detect it and queue a ``DeferredBlockcommit``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    selinux_stderr = "Operation not permitted: AVC denied"
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=selinux_stderr,
            returncode=1,
            error=selinux_stderr,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    # Core's deferral condition: not success AND error contains "selinux".
    assert result.success is False
    assert result.error is not None
    assert "selinux" in result.error


# ──────────────────────────────────────────────────────────────────────────
# 10. Normal failure does NOT trigger MAC deferral
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_normal_failure_no_deferral(mock_shell: MockShell, make_vm_config):
    """A non-MAC failure does not produce a deferral error string.

    - ``domblklist`` returns success.
    - ``blockcommit`` returns non-zero with stderr "No such file or
      directory" — a normal I/O error, not an AppArmor/SELinux denial.
    - Result: ``CommitResult(success=False)`` with the original error
      propagated (not "blocked by ...").
    - The error string does NOT contain "apparmor" or "selinux", so
      Core will not defer.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    io_error = "error: No such file or directory"
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=io_error,
            returncode=1,
            error=io_error,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is False
    # The original error is propagated, not a MAC-specific message.
    assert result.error == io_error
    assert "apparmor" not in result.error
    assert "selinux" not in result.error
    assert "blocked by" not in result.error
    # committed_snapshot reflects the snapshot that failed (normal path).
    assert result.committed_snapshot == snap.name


# ──────────────────────────────────────────────────────────────────────────
# 11. Deep verify — qemu-img check on base image after commit
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_deep_verify_passes(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with clean qemu-img check returns success.

    - domblklist and blockcommit both succeed.
    - qemu-img check returns JSON with ``{"corruptions": 0}``.
    - Result: ``CommitResult(success=True, error=None)``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":0, "errors":0, "leaks":0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    manager = BlockCommitManager(shell=mock_shell)
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert result.success is True
    assert result.error is None
    assert result.committed_snapshot == snap.name


def test_blockcommit_deep_verify_fails_corruptions(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with corruptions > 0 returns failure.

    - domblklist and blockcommit both succeed.
    - qemu-img check returns JSON with ``{"corruptions": 5}``.
    - Result: ``CommitResult(success=False)`` with "deep verify" and
      "5" in the error message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":5, "errors":0, "leaks":0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    manager = BlockCommitManager(shell=mock_shell)
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert result.success is False
    assert "deep verify" in result.error
    assert "5" in result.error
    assert "corruptions" in result.error


def test_blockcommit_deep_verify_fails_errors(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with errors > 0 returns failure.

    - domblklist and blockcommit both succeed.
    - qemu-img check returns JSON with ``{"corruptions":0, "errors":2, "leaks":0}``.
    - Result: ``CommitResult(success=False)`` with "deep verify" and
      "2 errors" in the error message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":0, "errors":2, "leaks":0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    manager = BlockCommitManager(shell=mock_shell)
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert result.success is False
    assert "deep verify" in result.error
    assert "2" in result.error
    assert "errors" in result.error


def test_blockcommit_deep_verify_fails_leaks(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with leaks > 0 returns failure.

    - domblklist and blockcommit both succeed.
    - qemu-img check returns JSON with ``{"corruptions":0, "errors":0, "leaks":3}``.
    - Result: ``CommitResult(success=False)`` with "deep verify" and
      "3 leaks" in the error message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":0, "errors":0, "leaks":3}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    manager = BlockCommitManager(shell=mock_shell)
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert result.success is False
    assert "deep verify" in result.error
    assert "3" in result.error
    assert "leaks" in result.error


def test_blockcommit_deep_verify_false_no_check(mock_shell: MockShell, make_vm_config):
    """deep_verify=False does NOT call qemu-img check.

    - domblklist and blockcommit both succeed.
    - No ``qemu-img check`` expectation is configured.
    - If qemu-img check were called, ``MockShell`` would return
      ``"No mock configured"`` and cause a failure.
    - Result: ``CommitResult(success=True)``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Intentionally NO qemu-img check expectation set.

    manager = BlockCommitManager(shell=mock_shell)
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=False
    )

    assert result.success is True
    assert result.error is None
    assert result.committed_snapshot == snap.name


# ──────────────────────────────────────────────────────────────────────────
# 12. Blockcommit does NOT use --force-share (design D5)
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_no_force_share(mock_shell: MockShell, make_vm_config):
    """Lifecycle commit operations are intentionally offline-only (design D5).

    ``virsh blockcommit`` and ``qemu-img check`` (deep_verify) must NOT
    include ``--force-share``.  These operations are not safe to run
    while the VM is holding an exclusive write lock on the image.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":0, "errors":0, "leaks":0}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert result.success is True
    assert result.committed_snapshot == snap.name

    # Verify qemu-img check does NOT include --force-share
    qemu_calls = [c for c in shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1, f"Expected exactly 1 qemu-img check call, got {len(qemu_calls)}"
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert "--force-share" not in qemu_cmd_str, (
        f"qemu-img check must NOT include --force-share (lifecycle ops are offline-only), "
        f"got: {qemu_cmd_str}"
    )
    assert "check" in qemu_cmd_str

    # Verify virsh blockcommit calls do not include --force-share
    bc_calls = _blockcommit_calls(shell)
    for cmd in bc_calls:
        cmd_str = " ".join(cmd)
        assert "--force-share" not in cmd_str, (
            f"virsh blockcommit must NOT include --force-share, got: {cmd_str}"
        )


# ──────────────────────────────────────────────────────────────────────────
# 12a. Multi-disk — blockcommit uses the correct disk's base
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_multi_disk_uses_correct_disk_base(mock_shell: MockShell, make_vm_config):
    """With a multi-disk VM, blockcommit for vdb uses vdb's base_image.

    - VM config has two disks: vda (base /tmp/vda.qcow2) and vdb
      (base /tmp/vdb.qcow2).
    - blockcommit called with ``disk="vdb"`` and ``base_image`` set to
      vdb's base.
    - The virsh blockcommit command must contain ``--path vdb`` and
      ``--base <vdb base path>`` (NOT vda's base).
    """
    from pathlib import Path

    from qsnap.models.config import DiskConfig

    vda_disk = DiskConfig(target="vda", base_image=Path("/tmp/vda.qcow2"))
    vdb_disk = DiskConfig(target="vdb", base_image=Path("/tmp/vdb.qcow2"))
    vm_config = make_vm_config(disks=[vda_disk, vdb_disk])

    snap = _make_snapshot(
        name="testvm.20250101T000000_vdb",
        path="/snapshots/testvm.20250101T000000_vdb.qcow2",
        disk="vdb",
    )

    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap], disk="vdb", base_image=vdb_disk.base_image)

    assert result.success is True
    assert result.committed_snapshot == snap.name

    # Inspect the recorded blockcommit command.
    bc_calls = _blockcommit_calls(shell)
    assert len(bc_calls) == 1
    cmd = bc_calls[0]

    # Must contain --path with the domain name and disk target.
    assert "--domain" in cmd
    domain_idx = cmd.index("--domain")
    assert domain_idx + 1 < len(cmd)

    # --path must reference vdb.
    path_idx = cmd.index("--path")
    assert "vdb" in cmd[path_idx + 1]

    # --base must point to vdb's base_image, NOT vda's.
    base_idx = cmd.index("--base")
    assert cmd[base_idx + 1] == str(vdb_disk.base_image)
    assert str(vda_disk.base_image) not in " ".join(cmd)

    # --top must point to the snapshot path.
    top_idx = cmd.index("--top")
    assert cmd[top_idx + 1] == str(snap.path)


# ──────────────────────────────────────────────────────────────────────────
# 13. Architecture — no cross-domain imports
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_deep_verify_graceful_qemu_img_failure(
    clean_shell, make_vm_config, success_result, failure_result
):
    """When qemu-img check command itself fails (non-zero exit),
    BlockCommitManager returns a graceful CommitResult(success=False) without crashing.

    - domblklist and blockcommit both succeed.
    - qemu-img check returns ShellResult(success=False, error=<crash error>).
    - Result: ``CommitResult(success=False, error="deep verify: qemu-img check failed: ...")``.
    - No exception propagates from the manager.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    clean_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=_DOMBLKLIST_OUTPUT,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    clean_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # qemu-img check command itself fails (non-zero exit code)
    clean_shell.expect("qemu-img check").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: Could not open base.qcow2",
            returncode=1,
            error="qemu-img: Could not open base.qcow2",
        )
    )

    manager = BlockCommitManager(shell=clean_shell)
    # This must NOT raise — graceful failure via CommitResult
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert "deep verify" in result.error
    assert "qemu-img check failed" in result.error
    assert "Could not open base.qcow2" in result.error


def test_blockcommit_no_cross_domain_imports():
    """``qsnap.modules.lifecycle.blockcommit_manager`` does NOT import
    anything from ``qsnap.modules.backup`` (cross-domain import violation
    per AGENTS.md).

    The BlockCommitManager is a lifecycle module — it must not depend on
    backup-domain code.  Shared utilities live in ``qsnap.utils.*``.
    """
    import qsnap.modules.lifecycle.blockcommit_manager as bc_mod

    source = inspect.getsource(bc_mod)
    assert "qsnap.modules.backup" not in source, (
        "blockcommit_manager.py must not import from qsnap.modules.backup "
        "(shared utilities live in qsnap.utils.*)"
    )
