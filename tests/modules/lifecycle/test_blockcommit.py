"""Tests for BlockCommitManager -- backing chain lifecycle management.

These tests exercise ``BlockCommitManager`` (implements ``ILifecycleManager``)
in complete isolation.  All ``virsh`` calls go through a mocked ``IShell``.
The manager does NOT inherit from Core (design D1) and takes only ``IShell``
as a constructor dependency.

Since the multi-disk refactor the manager no longer calls ``virsh domblklist``
to resolve the disk target — ``disk`` is a keyword-only parameter.  The commit
command is executed via ``IShell.run_with_heartbeat`` with a configurable
``timeout`` (default 1800) and a periodic heartbeat callback that logs
progress for long-running merges (harden-blockcommit-races).

Test scenarios (per lifecycle-manager/spec.md + harden-blockcommit-races):

1. **Single snapshot success** -- blockcommit returns exit 0, result is
   ``CommitResult(success=True, outcome="success")`` and the default
   ``timeout``/``heartbeat_seconds`` kwargs reach ``run_with_heartbeat``.
2. **Virsh error** -- blockcommit returns non-zero exit code, result is
   ``CommitResult(success=False, outcome="failure")``.
3. **Empty list no-op** -- empty ``snapshots_to_merge`` produces success
   with ``committed_snapshot=""`` and zero shell calls.
4. **Timeout -> unknown** -- a "timed out" result from ``run_with_heartbeat``
   is classified ``outcome="unknown"`` (never ``"failure"``).
5. **Injected timeout** -- the ``timeout=`` kwarg is forwarded to
   ``run_with_heartbeat``.
6. **Heartbeat callback** -- ``on_heartbeat`` is invoked during the wait;
   heartbeat log lines appear during a long commit; a fast commit produces
   no heartbeat lines.
7. **Bulk segment command** (design D1) -- the ENTIRE merge set is merged
   with ONE ``virsh blockcommit`` segment command; ``--top`` is the newest
   snapshot in the set (``snapshots_to_merge[-1]``) and the command carries
   ``--delete --verbose --wait``.  A single-snapshot merge set degenerates
   to the same segment command.  The manager forwards its ``timeout``
   argument verbatim into ``run_with_heartbeat`` (the ``x len(merge set)``
   scaling happens in Core).
8. **MAC denial** -- AppArmor/SELinux denials are definitive failures with
   ``outcome="failure"``.
9. **Deep verify** -- ``qemu-img check`` targets the disk's ``base_image``
   and the helper's internal ``shell.run()`` call does NOT pass
   ``check=True``.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import CommitResult, ShellResult, SnapshotInfo
from qsnap.modules.lifecycle.blockcommit_manager import BlockCommitManager
from tests.mocks.mock_shell import MockShell

# ── Helpers ────────────────────────────────────────────────────────────────


class CountingShell(IShell):
    """Wrapper around an ``IShell`` that records every command passed to it.

    Delegates actual execution to the inner shell (typically ``MockShell``)
    while capturing the full command list plus per-call kwargs for
    post-call assertions such as call-count verification, flag inspection,
    and timeout/heartbeat kwarg checks.

    The manager executes the live commit via ``run_with_heartbeat``, so this
    double records those calls separately from plain ``run()`` calls
    (``heartbeat_calls`` vs ``run_calls``).  Heartbeat callbacks are
    delegated to the inner shell, which scripts them via
    ``expect(...).returns(result, heartbeats=N)``.
    """

    def __init__(self, inner: IShell) -> None:
        self._inner = inner
        self.calls: list[list[str]] = []
        # (cmd, timeout, check) for every run() invocation.
        self.run_calls: list[tuple[list[str], int, bool]] = []
        # (cmd, timeout, heartbeat_seconds) for every run_with_heartbeat() call.
        self.heartbeat_calls: list[tuple[list[str], int, int]] = []

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        self.calls.append(list(cmd))
        self.run_calls.append((list(cmd), timeout, check))
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

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat,
        check: bool = False,
    ) -> ShellResult:
        self.calls.append(list(cmd))
        self.heartbeat_calls.append((list(cmd), timeout, heartbeat_seconds))
        return self._inner.run_with_heartbeat(
            cmd,
            timeout=timeout,
            heartbeat_seconds=heartbeat_seconds,
            on_heartbeat=on_heartbeat,
            check=check,
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


def _make_snapshot_set(n: int = 49) -> list[SnapshotInfo]:
    """Build an oldest-first merge set of *n* snapshots (default 49).

    Names/paths encode the 0-based index (``testvm.20250101T{i:02d}0000``),
    one minute apart, so assertions can pin the newest snapshot
    (``snaps[-1]``) and the per-snapshot processing order precisely.
    """
    snaps: list[SnapshotInfo] = []
    for i in range(n):
        name = f"testvm.20250101T{i:02d}0000"
        snaps.append(
            _make_snapshot(
                name=name,
                path=f"/snapshots/{name}.qcow2",
                timestamp=datetime(2025, 1, 1, 0, i, 0),
            )
        )
    return snaps


def _blockcommit_calls(shell: CountingShell) -> list[list[str]]:
    """Extract only the blockcommit commands from recorded calls."""
    return [c for c in shell.calls if "blockcommit" in " ".join(c)]


def _qemu_img_check_runs(
    shell: CountingShell,
) -> list[tuple[list[str], int, bool]]:
    """Extract the recorded ``qemu-img check`` run() calls (cmd, timeout, check)."""
    return [c for c in shell.run_calls if "qemu-img check" in " ".join(c[0])]


def _success() -> ShellResult:
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


# ──────────────────────────────────────────────────────────────────────────
# 1. Successful blockcommit of a single snapshot
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_single_snapshot_success(mock_shell: MockShell, make_vm_config):
    """A single snapshot is merged successfully.

    - ``virsh blockcommit`` returns exit 0.
    - Result: ``CommitResult(success=True, outcome="success")``.
    - The blockcommit command contains ``--base``, ``--top``, ``--delete``,
      ``--verbose``, ``--wait``.
    - Degenerate case (lifecycle-manager spec): a one-snapshot merge set
      collapses to the SAME segment command as a bulk set — the executed
      command's ``--top`` is the snapshot's own path.
    - The commit is executed via ``run_with_heartbeat`` with the default
      ``timeout=1800`` and ``heartbeat_seconds=60``.
    - No ``virsh domblklist`` call is made (disk is keyword-only).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == snap.name
    assert result.error is None
    assert result.outcome == "success"

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
    # Degenerate single-snapshot merge set: --top == the snapshot's own path.
    assert cmd[top_idx + 1] == str(snap.path)

    # The command went through run_with_heartbeat with the default timeout.
    assert len(shell.heartbeat_calls) == 1
    _, hb_timeout, hb_seconds = shell.heartbeat_calls[0]
    assert hb_timeout == 1800
    assert hb_seconds == 60

    # domblklist is never called by the manager.
    assert not any("domblklist" in " ".join(c) for c in shell.calls)


# ──────────────────────────────────────────────────────────────────────────
# 2. Blockcommit fails -- virsh returns non-zero exit code
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_virsh_error(mock_shell: MockShell, make_vm_config):
    """A non-zero exit code from virsh blockcommit yields a failure result.

    - ``blockcommit`` returns exit 1 with an error message.
    - Result: ``CommitResult(success=False, outcome="failure")``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

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
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 3. Empty snapshot list -- nothing to merge
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_empty_list_no_op(mock_shell: MockShell, make_vm_config):
    """An empty snapshot list is a no-op.

    - No shell call at all (no domblklist, no blockcommit).
    - Result: ``CommitResult(success=True, committed_snapshot="",
      outcome="success")`` (lifecycle-manager spec "Empty snapshot list":
      ``success=True`` SHALL imply ``outcome="success"``).
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
    assert result.outcome == "success"

    # No shell commands should have been executed at all.
    assert len(shell.calls) == 0


# ──────────────────────────────────────────────────────────────────────────
# 4. Blockcommit times out -- outcome is "unknown", never "failure"
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_timeout_returns_unknown(mock_shell: MockShell, make_vm_config):
    """A blockcommit timeout yields ``outcome="unknown"`` (never "failure").

    The job may have completed on the hypervisor after the client was
    killed, so the manager must classify timeouts as indeterminate
    (``outcome="unknown"``) so the caller can reconcile instead of
    treating the commit as a definitive failure.

    - ``run_with_heartbeat`` returns
      ``ShellResult(success=False, error="Command timed out after 1800s")``.
    - Result: ``CommitResult(success=False, outcome="unknown")``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="Command timed out after 1800s",
        )
    )

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is False
    assert "timed out" in result.error
    assert result.error == "Command timed out after 1800s"
    assert result.outcome == "unknown"
    assert result.outcome != "failure"
    assert result.committed_snapshot == ""


# ──────────────────────────────────────────────────────────────────────────
# 5. Injected timeout is honored
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_injected_timeout_honored(mock_shell: MockShell, make_vm_config):
    """The ``timeout=`` kwarg reaches ``run_with_heartbeat`` unchanged.

    The caller (Core) passes ``GlobalConfig.blockcommit_timeout`` through
    to the manager; the manager must forward it to the shell call.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config,
        [snap],
        disk="vda",
        base_image=vm_config.disks[0].base_image,
        timeout=900,
    )

    assert result.success is True
    assert len(shell.heartbeat_calls) == 1
    _, timeout_kwarg, hb_seconds = shell.heartbeat_calls[0]
    assert timeout_kwarg == 900
    assert hb_seconds == 60


# ──────────────────────────────────────────────────────────────────────────
# 6. Heartbeat callback + heartbeat log lines
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_heartbeat_callback_elapsed(mock_shell: MockShell, make_vm_config, caplog):
    """The heartbeat callback fires during the wait with the elapsed time.

    The mock shell scripts a single heartbeat (``heartbeats=1``); the
    manager's callback must log a progress line carrying the elapsed
    seconds.  With a one-snapshot merge set the line names 1 layer
    (the callback logs the merge-set size, not per-snapshot progress).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success(), heartbeats=1)

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    with caplog.at_level(logging.INFO, logger="qsnap.modules.lifecycle.blockcommit_manager"):
        result = manager.blockcommit(
            vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
        )

    assert result.success is True
    expected_line = "[blockcommit] testvm/vda: still collapsing 1 layer into base (60s elapsed)"
    assert any(expected_line in rec.message for rec in caplog.records)


def test_heartbeat_lines_during_long_commit(mock_shell: MockShell, make_vm_config, caplog):
    """Heartbeat log lines appear during a long bulk collapse.

    A 49-layer merge set scripted with two heartbeats (at 60s and 120s)
    produces exactly two ``[blockcommit] ... still collapsing ...`` INFO
    lines, each naming the VM, disk, and the layer count of the merge set
    (commit-observability spec).
    """
    vm_config = make_vm_config()
    snaps = _make_snapshot_set(49)

    mock_shell.expect("virsh blockcommit").returns(_success(), heartbeats=2)

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    with caplog.at_level(logging.INFO, logger="qsnap.modules.lifecycle.blockcommit_manager"):
        result = manager.blockcommit(
            vm_config, snaps, disk="vda", base_image=vm_config.disks[0].base_image
        )

    assert result.success is True
    heartbeat_lines = [
        rec.message
        for rec in caplog.records
        if "[blockcommit]" in rec.message and "still collapsing" in rec.message
    ]
    assert len(heartbeat_lines) == 2
    assert heartbeat_lines[0] == (
        "[blockcommit] testvm/vda: still collapsing 49 layers into base (60s elapsed)"
    )
    assert heartbeat_lines[1] == (
        "[blockcommit] testvm/vda: still collapsing 49 layers into base (120s elapsed)"
    )


def test_fast_commit_no_heartbeat_lines(mock_shell: MockShell, make_vm_config, caplog):
    """A fast collapse produces no heartbeat lines.

    With ``heartbeats=0`` (the default) the mock shell never invokes the
    callback, so no ``[blockcommit] ... still collapsing ...`` progress
    lines are logged — a sub-60s job must not emit heartbeats and the
    result is logged normally (commit-observability spec).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    with caplog.at_level(logging.INFO, logger="qsnap.modules.lifecycle.blockcommit_manager"):
        result = manager.blockcommit(
            vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
        )

    assert result.success is True
    heartbeat_lines = [
        rec.message
        for rec in caplog.records
        if "[blockcommit]" in rec.message and "still collapsing" in rec.message
    ]
    assert heartbeat_lines == []


# ──────────────────────────────────────────────────────────────────────────
# 7. Bulk segment commit — the ENTIRE merge set in ONE virsh blockcommit
# ──────────────────────────────────────────────────────────────────────────


def test_bulk_blockcommit_single_segment_command(mock_shell: MockShell, make_vm_config):
    """A 49-snapshot merge set collapses with exactly ONE segment command.

    - ``snapshots_to_merge`` contains 49 snapshots (oldest first).
    - Exactly ONE ``virsh blockcommit`` process is spawned (no per-snapshot
      loop) via ``run_with_heartbeat``.
    - ``--top`` is the 49th/newest snapshot's path and ``--delete
      --verbose --wait`` are present.
    - Result: ``CommitResult(success=True, committed_snapshot=<newest name>,
      outcome="success")``.
    """
    vm_config = make_vm_config()
    snaps = _make_snapshot_set(49)

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, snaps, disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == snaps[-1].name
    assert result.error is None
    assert result.outcome == "success"

    # Exactly ONE virsh blockcommit process for the whole segment.
    bc_calls = _blockcommit_calls(shell)
    assert len(bc_calls) == 1, f"expected 1 blockcommit, got {len(bc_calls)}"

    cmd = bc_calls[0]
    assert "--domain" in cmd
    assert "--path" in cmd
    assert "--base" in cmd
    assert "--top" in cmd
    assert "--delete" in cmd
    assert "--verbose" in cmd
    assert "--wait" in cmd

    # --top is the NEWEST merged snapshot (snapshots_to_merge[-1]).
    top_idx = cmd.index("--top")
    assert cmd[top_idx + 1] == str(snaps[-1].path)

    # The command went through run_with_heartbeat exactly once.
    assert len(shell.heartbeat_calls) == 1


def test_bulk_segment_command_top_is_newest(mock_shell: MockShell, make_vm_config):
    """argv-exact contract for the multi-snapshot segment command.

    The single executed command MUST be exactly:
    ``virsh blockcommit --domain testvm --path vda --base <base> --top
    <snap49.path> --delete --verbose --wait`` — no extra flags, no
    per-snapshot loop (test-plan Notes #2).
    """
    vm_config = make_vm_config()
    snaps = _make_snapshot_set(49)
    base = vm_config.disks[0].base_image

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, snaps, disk="vda", base_image=base)

    assert result.success is True
    assert result.committed_snapshot == snaps[-1].name

    bc_calls = _blockcommit_calls(shell)
    assert len(bc_calls) == 1

    # argv-exact: the entire command is pinned, --top is snap49 (the newest).
    assert bc_calls[0] == [
        "virsh",
        "blockcommit",
        "--domain",
        vm_config.name,
        "--path",
        "vda",
        "--base",
        str(base),
        "--top",
        str(snaps[-1].path),
        "--delete",
        "--verbose",
        "--wait",
    ]
    assert len(shell.heartbeat_calls) == 1


def test_bulk_blockcommit_scaled_timeout_forwarded(mock_shell: MockShell, make_vm_config):
    """The manager forwards the timeout it was GIVEN, verbatim.

    The ``timeout x len(merge set)`` scaling happens in Core (design D4):
    for a 49-layer set with ``blockcommit_timeout = 1800`` Core passes
    ``88200`` (= 1800 x 49) to the manager.  The manager must hand that
    value through to ``run_with_heartbeat`` unchanged — it must NOT
    re-scale internally (which would yield 1800 x 49 x 49).
    """
    vm_config = make_vm_config()
    snaps = _make_snapshot_set(49)

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    # Simulate Core's scaled budget: 1800 (per layer) x 49 (merge set).
    result = manager.blockcommit(
        vm_config,
        snaps,
        disk="vda",
        base_image=vm_config.disks[0].base_image,
        timeout=88200,
    )

    assert result.success is True
    assert len(shell.heartbeat_calls) == 1
    _, timeout_kwarg, hb_seconds = shell.heartbeat_calls[0]
    assert timeout_kwarg == 88200
    assert hb_seconds == 60


# ──────────────────────────────────────────────────────────────────────────
# 8. MAC denial — AppArmor blocks blockcommit
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_blocked_by_apparmor(mock_shell: MockShell, make_vm_config):
    """An AppArmor denial is detected and reported as a MAC failure.

    - ``blockcommit`` returns non-zero with stderr containing
      "Permission denied" and "apparmor".
    - Result: ``CommitResult(success=False, error="blocked by apparmor",
      outcome="failure")``.
    - ``committed_snapshot`` is empty (no snapshot was merged).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

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
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 9. MAC denial — SELinux blocks blockcommit
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_blocked_by_selinux(mock_shell: MockShell, make_vm_config):
    """An SELinux denial is detected and reported as a MAC failure.

    - ``blockcommit`` returns non-zero with stderr containing
      "Operation not permitted" and "AVC".
    - Result: ``CommitResult(success=False, error="blocked by selinux",
      outcome="failure")``.
    - ``committed_snapshot`` is empty (no snapshot was merged).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

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
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 10. AppArmor denial error string enables Core deferral
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
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 11. SELinux denial error string enables Core deferral
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
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 12. Normal failure does NOT trigger MAC deferral
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_normal_failure_no_deferral(mock_shell: MockShell, make_vm_config):
    """A non-MAC failure does not produce a deferral error string.

    - ``blockcommit`` returns non-zero with stderr "No such file or
      directory" — a normal I/O error, not an AppArmor/SELinux denial.
    - Result: ``CommitResult(success=False, outcome="failure")`` with the
      original error propagated (not "blocked by ...").
    - The error string does NOT contain "apparmor" or "selinux", so
      Core will not defer.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

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
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 13. Deep verify — qemu-img check on the disk's base image after commit
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_deep_verify_passes(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with clean qemu-img check returns success.

    - blockcommit succeeds.
    - qemu-img check returns JSON with ``{"corruptions": 0}``.
    - ``qemu-img check`` targets the disk's ``base_image``.
    - The helper's internal ``shell.run()`` does NOT pass ``check=True``.
    - Result: ``CommitResult(success=True, outcome="success")``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
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
    assert result.error is None
    assert result.committed_snapshot == snap.name
    assert result.outcome == "success"

    # qemu-img check targets the disk's base image, via run() without check=True.
    check_runs = _qemu_img_check_runs(shell)
    assert len(check_runs) == 1
    check_cmd, check_timeout, check_flag = check_runs[0]
    assert str(vm_config.disks[0].base_image) in " ".join(check_cmd)
    assert check_flag is False


def test_blockcommit_deep_verify_injected_timeout_honored(mock_shell: MockShell, make_vm_config):
    """deep_verify passes the injected timeout to ``qemu-img check``.

    No hard-coded ceiling may remain in the commit path (lifecycle-manager
    spec): the deep-verify helper receives the manager's ``timeout`` kwarg.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
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
        vm_config,
        [snap],
        disk="vda",
        base_image=vm_config.disks[0].base_image,
        deep_verify=True,
        timeout=900,
    )

    assert result.success is True
    check_runs = _qemu_img_check_runs(shell)
    assert len(check_runs) == 1
    assert check_runs[0][1] == 900, "qemu-img check must receive the injected timeout"


def test_blockcommit_deep_verify_fails_corruptions(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with corruptions > 0 returns failure.

    - blockcommit succeeds.
    - qemu-img check returns JSON with ``{"corruptions": 5}``.
    - Result: ``CommitResult(success=False)`` with "deep verify" and
      "5" in the error message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":5, "errors":0, "leaks":0}',
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

    assert result.success is False
    assert "deep verify" in result.error
    assert "5" in result.error
    assert "corruptions" in result.error

    # The check still targeted the disk's base image without check=True.
    check_runs = _qemu_img_check_runs(shell)
    assert len(check_runs) == 1
    assert str(vm_config.disks[0].base_image) in " ".join(check_runs[0][0])
    assert check_runs[0][2] is False


def test_blockcommit_deep_verify_fails_errors(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with errors > 0 returns failure.

    - blockcommit succeeds.
    - qemu-img check returns JSON with ``{"corruptions":0, "errors":2, "leaks":0}``.
    - Result: ``CommitResult(success=False)`` with "deep verify" and
      "2 errors" in the error message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":0, "errors":2, "leaks":0}',
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

    assert result.success is False
    assert "deep verify" in result.error
    assert "2" in result.error
    assert "errors" in result.error


def test_blockcommit_deep_verify_fails_leaks(mock_shell: MockShell, make_vm_config):
    """deep_verify=True with leaks > 0 returns failure.

    - blockcommit succeeds.
    - qemu-img check returns JSON with ``{"corruptions":0, "errors":0, "leaks":3}``.
    - Result: ``CommitResult(success=False)`` with "deep verify" and
      "3 leaks" in the error message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout='{"corruptions":0, "errors":0, "leaks":3}',
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

    assert result.success is False
    assert "deep verify" in result.error
    assert "3" in result.error
    assert "leaks" in result.error


def test_blockcommit_deep_verify_false_no_check(mock_shell: MockShell, make_vm_config):
    """deep_verify=False does NOT call qemu-img check.

    - blockcommit succeeds.
    - No ``qemu-img check`` expectation is configured.
    - If qemu-img check were called, ``MockShell`` would return
      ``"No mock configured"`` and cause a failure.
    - Result: ``CommitResult(success=True)``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
    # Intentionally NO qemu-img check expectation set.

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=False
    )

    assert result.success is True
    assert result.error is None
    assert result.committed_snapshot == snap.name

    # No qemu-img check run() call was recorded.
    assert _qemu_img_check_runs(shell) == []


# ──────────────────────────────────────────────────────────────────────────
# 14. Blockcommit does NOT use --force-share (design D5)
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_no_force_share(mock_shell: MockShell, make_vm_config):
    """Lifecycle commit operations are intentionally offline-only (design D5).

    ``virsh blockcommit`` and ``qemu-img check`` (deep_verify) must NOT
    include ``--force-share``.  These operations are not safe to run
    while the VM is holding an exclusive write lock on the image.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("virsh blockcommit").returns(_success())
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
# 15. Multi-disk — blockcommit uses the correct disk's base
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
    from qsnap.models.config import DiskConfig

    vda_disk = DiskConfig(target="vda", base_image=Path("/tmp/vda.qcow2"))
    vdb_disk = DiskConfig(target="vdb", base_image=Path("/tmp/vdb.qcow2"))
    vm_config = make_vm_config(disks=[vda_disk, vdb_disk])

    snap = _make_snapshot(
        name="testvm.20250101T000000_vdb",
        path="/snapshots/testvm.20250101T000000_vdb.qcow2",
        disk="vdb",
    )

    mock_shell.expect("virsh blockcommit").returns(_success())

    shell = CountingShell(mock_shell)
    manager = BlockCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap], disk="vdb", base_image=vdb_disk.base_image)

    assert result.success is True
    assert result.committed_snapshot == snap.name

    # Inspect the recorded blockcommit command.
    bc_calls = _blockcommit_calls(shell)
    assert len(bc_calls) == 1
    cmd = bc_calls[0]

    # Must contain --domain with the domain name and disk target.
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
# 16. Deep verify survives a failing qemu-img check gracefully
# ──────────────────────────────────────────────────────────────────────────


def test_blockcommit_deep_verify_graceful_qemu_img_failure(clean_shell, make_vm_config):
    """When qemu-img check command itself fails (non-zero exit),
    BlockCommitManager returns a graceful CommitResult(success=False) without crashing.

    - blockcommit succeeds.
    - qemu-img check returns ShellResult(success=False, error=<crash error>).
    - Result: ``CommitResult(success=False, error="deep verify: qemu-img check failed: ...")``.
    - No exception propagates from the manager.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    clean_shell.expect("virsh blockcommit").returns(_success())
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

    shell = CountingShell(clean_shell)
    manager = BlockCommitManager(shell=shell)
    # This must NOT raise — graceful failure via CommitResult
    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert "deep verify" in result.error
    assert "qemu-img check failed" in result.error
    assert "Could not open base.qcow2" in result.error


# ──────────────────────────────────────────────────────────────────────────
# 17. Architecture — no cross-domain imports
# ──────────────────────────────────────────────────────────────────────────


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
