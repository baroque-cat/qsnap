"""Tests for QemuImgCommitManager -- backing chain lifecycle via qemu-img commit.

These tests exercise ``QemuImgCommitManager`` (implements ``ILifecycleManager``)
in complete isolation.  All ``qemu-img`` calls go through a mocked ``IShell``.
The manager does NOT inherit from Core (design D1) and takes only ``IShell``
as a constructor dependency.

Rewired for design D4 (no ``-d`` flag) — per-snapshot algorithm (oldest first):
  1. ``qemu-img commit -b <base> <snap>`` (with the injected ``timeout``,
     default 1800 — harden-blockcommit-races)
  2. child discovery (find + qemu-img info scan)
  3. ``qemu-img rebase -u -F qcow2 -b <base> <child>`` (if child exists)
  4. ``rm -f <snap>`` (only after successful rebase, or when no child)
Short-circuit on ANY step failure.  ``deep_verify`` runs ``qemu-img check``
on the disk's ``base_image`` (not a VM-level base).
"""

from __future__ import annotations

import json
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
    """Wrapper around an ``IShell`` that records every command passed to it.

    Delegates actual execution to the inner shell (typically ``MockShell``)
    while capturing the full command list plus per-call kwargs for
    post-call assertions such as call-count verification and timeout
    inspection.

    ``run_with_heartbeat`` is implemented for interface conformance
    (``IShell`` gained the abstract method) and delegates to the inner
    shell; the qemu-img offline executor itself only uses ``run()``.
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


def _cmd_strings(calls: list[list[str]]) -> list[str]:
    """Return list of command strings from recorded calls."""
    return [" ".join(c) for c in calls]


def _assert_call_order(
    cmds: list[str], wanted: list[str], msg: str = "command order mismatch"
) -> None:
    """Assert that *wanted* substrings appear in *cmds* in the given order.

    Each element of *wanted* is a substring checked against each command
    string in *cmds*.  If a substring is not found, the assertion fails.
    """
    wanted_idx = 0
    found_positions: list[int] = []
    for i, cmd in enumerate(cmds):
        if wanted_idx < len(wanted) and wanted[wanted_idx] in cmd:
            found_positions.append(i)
            wanted_idx += 1
    missing = wanted[wanted_idx:]
    assert not missing, f"{msg}: missing {missing} in calls: {cmds}"
    assert found_positions == sorted(found_positions), (
        f"{msg}: found at {found_positions}, expected increasing order"
    )


def _commit_runs(
    shell: CountingShell,
) -> list[tuple[list[str], int, bool]]:
    """Extract the recorded ``qemu-img commit`` run() calls (cmd, timeout, check)."""
    return [c for c in shell.run_calls if "qemu-img commit" in " ".join(c[0])]


def _success() -> ShellResult:
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


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
# 5. Successful qemu-img commit (D4: commit + find + rm, no child)
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_success(mock_shell: MockShell, make_vm_config):
    """A successful commit with no child overlay (D4 algorithm).

    - ``qemu-img commit -b <base> <snap>`` returns exit 0 with the default
      ``timeout=1800``.
    - find returns empty (no child overlay — conftest fixture default).
    - No rebase needed.
    - ``rm -f <snap>`` deletes the committed file.
    - Result: ``CommitResult(success=True, committed_snapshot=<snap.name>,
      outcome="success")``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(_success())
    mock_shell.expect("rm -f").returns(_success())

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == snap.name
    assert result.error is None
    assert result.outcome == "success"

    # Verify command sequence: commit → find → rm (no rebase).
    cmds = _cmd_strings(shell.calls)
    _assert_call_order(
        cmds,
        ["qemu-img commit", "find", "rm -f"],
        "D4 commit sequence",
    )
    assert not any("rebase" in c for c in cmds), "rebase was called unexpectedly"

    # The qemu-img commit call carries the default 1800 s timeout.
    commit_runs = _commit_runs(shell)
    assert len(commit_runs) == 1
    _, commit_timeout, _check = commit_runs[0]
    assert commit_timeout == 1800


# ──────────────────────────────────────────────────────────────────────────
# 6. qemu-img commit fails
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_fails(mock_shell: MockShell, make_vm_config):
    """A failed ``qemu-img commit`` yields ``CommitResult(success=False)``.

    - ``qemu-img commit`` returns non-zero exit code with an error message.
    - Result: ``CommitResult(success=False, outcome="failure")``.
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

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.error == error_msg
    assert result.committed_snapshot == snap.name
    assert result.outcome == "failure"


# ──────────────────────────────────────────────────────────────────────────
# 6a. Injected timeout is honored for qemu-img commit
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_injected_timeout_honored(mock_shell: MockShell, make_vm_config):
    """The ``timeout=`` kwarg reaches the ``qemu-img commit`` call.

    The caller (Core) passes ``GlobalConfig.blockcommit_timeout`` through
    to the manager; the offline commit must use that value (was
    hard-coded 3600 before harden-blockcommit-races).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(_success())
    mock_shell.expect("rm -f").returns(_success())

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, timeout=900
    )

    assert result.success is True

    commit_runs = _commit_runs(shell)
    assert len(commit_runs) == 1
    _, commit_timeout, _check = commit_runs[0]
    assert commit_timeout == 900


# ──────────────────────────────────────────────────────────────────────────
# 6b. qemu-img commit timeout maps to outcome="unknown"
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_timeout_returns_unknown(mock_shell: MockShell, make_vm_config):
    """A timed-out ``qemu-img commit`` is classified ``outcome="unknown"``.

    The merge may have completed on disk after the client was killed, so
    the outcome is indeterminate (never a definitive ``"failure"``).
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="Command timed out after 1800s",
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.error == "Command timed out after 1800s"
    assert "timed out" in result.error
    assert result.outcome == "unknown"
    assert result.outcome != "failure"
    assert result.committed_snapshot == ""

    # Short-circuit: no find/rm follows the failed commit.
    assert not any("rm -f" in c for c in _cmd_strings(shell.calls))


# ══════════════════════════════════════════════════════════════════════════
# NEW D4 tests
# ══════════════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────────────────────────────────
# A.1: Pivot child overlay and delete
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_pivots_child_and_deletes(mock_shell: MockShell, make_vm_config):
    """Commit s1 with child s2: rebase s2 onto base, then delete s1.

    Uses ``full-backing-filename`` in the qemu-img info payload to cover
    one of the two backing-file resolution branches (the other — relative
    ``backing-filename`` — is covered by
    ``test_qemu_img_rebase_failure_keeps_file``).

    Exact command order verified: commit(s1) → find → info(s2) → rebase(s2→base) → rm(s1).
    """
    vm_config = make_vm_config()
    s1 = _make_snapshot(
        name="s1.20250101T000000",
        path="/var/lib/libvirt/snapshots/testvm/s1.20250101T000000.qcow2",
    )
    s2_path = "/var/lib/libvirt/snapshots/testvm/s2.20250101T010000.qcow2"

    # --- Set up mocks ---

    # Step a: commit s1 into base
    mock_shell.expect("qemu-img commit").returns(_success())

    # Step b: child discovery — find returns s2 path
    mock_shell.expect_first(r"find.*maxdepth.*1.*-name.*\.qcow2").returns(
        ShellResult(success=True, stdout=s2_path + "\n", stderr="", returncode=0, error=None)
    )

    # qemu-img info for s2 reports full-backing-filename = s1 path
    info_json = json.dumps(
        {
            "full-backing-filename": str(s1.path),
            "format": "qcow2",
            "virtual-size": 10737418240,
        }
    )
    mock_shell.expect_first(r"qemu-img info.*" + s2_path.replace("/", r"\/")).returns(
        ShellResult(success=True, stdout=info_json, stderr="", returncode=0, error=None)
    )

    # Step c: rebase s2 onto base
    mock_shell.expect("qemu-img rebase").returns(_success())

    # Step d: delete s1
    mock_shell.expect("rm -f").returns(_success())

    # --- Execute ---
    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)
    result = manager.blockcommit(
        vm_config, [s1], disk="vda", base_image=vm_config.disks[0].base_image
    )

    # --- Assertions ---
    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == s1.name
    assert result.error is None
    assert result.outcome == "success"

    cmds = _cmd_strings(shell.calls)
    _assert_call_order(
        cmds,
        [
            "qemu-img commit",
            "find",
            "qemu-img info",
            "qemu-img rebase",
            "rm -f",
        ],
        "commit(s1) → find → info(s2) → rebase(s2→base) → rm(s1)",
    )

    # Verify commit points to base and s1
    commit_cmd = next(c for c in cmds if "qemu-img commit" in c)
    assert "-b" in commit_cmd.split()
    assert str(s1.path) in commit_cmd

    # Verify rebase points to base and s2
    rebase_cmd = next(c for c in cmds if "qemu-img rebase" in c)
    assert "-b" in rebase_cmd.split()
    assert s2_path in rebase_cmd

    # Verify rm deletes s1
    rm_cmd = next(c for c in cmds if "rm -f" in c)
    assert str(s1.path) in rm_cmd


# ──────────────────────────────────────────────────────────────────────────
# A.2: No child → skip rebase
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_no_child_skips_rebase(mock_shell: MockShell, make_vm_config):
    """When no candidate's backing file matches the snapshot, rebase is not invoked.

    find returns empty (from conftest), so no qemu-img info/scan occurs.
    Only commit → find → rm commands appear; rebase is absent.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(_success())
    mock_shell.expect("rm -f").returns(_success())

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is True
    assert result.committed_snapshot == snap.name

    cmds = _cmd_strings(shell.calls)
    _assert_call_order(
        cmds,
        ["qemu-img commit", "find", "rm -f"],
        "commit → find → rm (no rebase)",
    )
    assert not any("rebase" in c for c in cmds), "rebase was called unexpectedly"
    assert not any("qemu-img info" in c for c in cmds), (
        "qemu-img info called when find returned no candidates"
    )


# ──────────────────────────────────────────────────────────────────────────
# A.3: Commit failure short-circuits (no delete, no further snapshots)
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_failure_no_delete_short_circuit(mock_shell: MockShell, make_vm_config):
    """First snapshot commit failure: rm NOT invoked; second snapshot NOT attempted.

    With [s1, s2], if qemu-img commit for s1 fails:
    - rm -f is NOT called for s1.
    - commit is NOT called for s2.
    - result.committed_snapshot == s1.name, success False.
    """
    vm_config = make_vm_config()
    s1 = _make_snapshot(
        name="s1.20250101T000000",
        path="/var/lib/libvirt/snapshots/testvm/s1.20250101T000000.qcow2",
    )
    s2 = _make_snapshot(
        name="s2.20250101T010000",
        path="/var/lib/libvirt/snapshots/testvm/s2.20250101T010000.qcow2",
    )

    error_msg = "qemu-img: Could not open"
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

    result = manager.blockcommit(
        vm_config, [s1, s2], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.committed_snapshot == s1.name
    assert result.error == error_msg
    assert result.outcome == "failure"

    cmds = _cmd_strings(shell.calls)

    # Only one commit attempt (for s1) — no command for s2.
    commit_count = sum(1 for c in cmds if "qemu-img commit" in c)
    assert commit_count == 1, f"expected 1 commit, got {commit_count}: {cmds}"

    # rm -f must NOT appear.
    assert not any("rm -f" in c for c in cmds), f"rm was called: {cmds}"

    # The commit for s1 must reference s1.path, not s2.path.
    commit_cmd = next(c for c in cmds if "qemu-img commit" in c)
    assert str(s1.path) in commit_cmd
    assert str(s2.path) not in commit_cmd


# ──────────────────────────────────────────────────────────────────────────
# A.4: Rebase failure keeps the committed file
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_rebase_failure_keeps_file(mock_shell: MockShell, make_vm_config):
    """When rebase fails after a successful commit, the file is NOT deleted.

    Uses relative ``backing-filename`` in the qemu-img info payload to cover
    the second backing-file resolution branch.
    """
    vm_config = make_vm_config()
    s1 = _make_snapshot(
        name="s1.20250101T000000",
        path="/var/lib/libvirt/snapshots/testvm/s1.20250101T000000.qcow2",
    )
    s2_path = "/var/lib/libvirt/snapshots/testvm/s2.20250101T010000.qcow2"

    # Commit succeeds
    mock_shell.expect("qemu-img commit").returns(_success())

    # Child discovery: find returns s2
    mock_shell.expect_first(r"find.*maxdepth.*1.*-name.*\.qcow2").returns(
        ShellResult(success=True, stdout=s2_path + "\n", stderr="", returncode=0, error=None)
    )

    # qemu-img info for s2 reports relative backing-filename = s1 basename
    # The code resolves relative paths against the candidate's dir.
    info_json = json.dumps(
        {
            "backing-filename": Path(s1.path).name,
            "format": "qcow2",
            "virtual-size": 10737418240,
        }
    )
    mock_shell.expect("qemu-img info").returns(
        ShellResult(success=True, stdout=info_json, stderr="", returncode=0, error=None)
    )

    # Rebase FAILS
    rebase_error = "qemu-img: Could not rebase: No space left on device"
    mock_shell.expect("qemu-img rebase").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=rebase_error,
            returncode=1,
            error=rebase_error,
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [s1], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.committed_snapshot == s1.name
    assert result.error == rebase_error

    cmds = _cmd_strings(shell.calls)

    # Commit happened
    assert any("qemu-img commit" in c for c in cmds), f"commit not in {cmds}"
    # Rebase happened (and failed)
    assert any("qemu-img rebase" in c for c in cmds), f"rebase not in {cmds}"
    # rm -f must NOT appear — file is kept on rebase failure
    assert not any("rm -f" in c for c in cmds), f"rm was called: {cmds}"


# ──────────────────────────────────────────────────────────────────────────
# A.5: MAC (AppArmor) denial
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_blocked_by_apparmor(mock_shell: MockShell, make_vm_config):
    """A step failing with AppArmor stderr yields committed_snapshot="" and
    error="blocked by apparmor"."""
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="error: permission denied",
            returncode=1,
            error="qemu-img: permission denied",
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.committed_snapshot == ""
    assert result.error == "blocked by apparmor"


# ──────────────────────────────────────────────────────────────────────────
# A.6: MAC (SELinux) denial
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_blocked_by_selinux(mock_shell: MockShell, make_vm_config):
    """A step failing with SELinux stderr yields committed_snapshot="" and
    error="blocked by selinux"."""
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="avc: denied { read } for pid=1234",
            returncode=1,
            error="qemu-img: operation not permitted",
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.committed_snapshot == ""
    assert result.error == "blocked by selinux"


# ──────────────────────────────────────────────────────────────────────────
# A.7: deep_verify after successful commit
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "check_stdout,expected_success,expected_error",
    [
        # corruptions=0, errors=0, leaks=0 → success
        (
            json.dumps({"corruptions": 0, "errors": 0, "leaks": 0}),
            True,
            None,
        ),
        # corruptions>0 → failure
        (
            json.dumps({"corruptions": 3, "errors": 0, "leaks": 0}),
            False,
            "deep verify: 3 corruptions in base image",
        ),
        # errors>0 (corruptions=0) → failure
        (
            json.dumps({"corruptions": 0, "errors": 2, "leaks": 0}),
            False,
            "deep verify: 2 errors in base image",
        ),
        # leaks>0 (corruptions=0, errors=0) → failure
        (
            json.dumps({"corruptions": 0, "errors": 0, "leaks": 4}),
            False,
            "deep verify: 4 leaks in base image",
        ),
    ],
)
def test_qemu_img_commit_deep_verify(
    mock_shell: MockShell,
    make_vm_config,
    check_stdout: str,
    expected_success: bool,
    expected_error: str | None,
):
    """With deep_verify=True, qemu-img check runs after successful commits.

    - corruptions=0, errors=0, leaks=0 → success.
    - corruptions>0 → failure with detail message.
    - errors>0 → failure with detail message.
    - leaks>0 → failure with detail message.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    # Commit succeeds
    mock_shell.expect("qemu-img commit").returns(_success())
    # find returns empty (no child) — conftest default
    # rm succeeds
    mock_shell.expect("rm -f").returns(_success())
    # qemu-img check output
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout=check_stdout,
            stderr="",
            returncode=0 if expected_success else 3,
            error=None,
        )
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vda", base_image=vm_config.disks[0].base_image, deep_verify=True
    )

    assert result.success is expected_success
    assert result.error == expected_error

    cmds = _cmd_strings(shell.calls)
    # qemu-img check must appear after commit/find/rm
    _assert_call_order(
        cmds,
        ["qemu-img commit", "find", "rm -f", "qemu-img check"],
        "commit → find → rm → check",
    )
    check_cmd = next(c for c in cmds if "qemu-img check" in c)
    assert "--output=json" in check_cmd
    assert str(vm_config.disks[0].base_image) in check_cmd


# ──────────────────────────────────────────────────────────────────────────
# A.7b: Deep verify targets the disk's base image (not a VM-level base)
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_deep_verify_targets_disk_base(mock_shell: MockShell, make_vm_config):
    """With a multi-disk VM, deep verify checks the disk's own base image.

    Committing a ``vdb`` snapshot with ``base_image=vdb.qcow2`` must run
    ``qemu-img check`` on vdb's base — never vda's.
    """
    from qsnap.models.config import DiskConfig

    vda_disk = DiskConfig(target="vda", base_image=Path("/tmp/vda.qcow2"))
    vdb_disk = DiskConfig(
        target="vdb",
        base_image=Path("/tmp/vdb.qcow2"),
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )
    vm_config = make_vm_config(disks=[vda_disk, vdb_disk])

    snap = _make_snapshot(
        name="testvm.20250101T000000_vdb",
        path="/var/lib/libvirt/snapshots/testvm/testvm.20250101T000000_vdb.qcow2",
        disk="vdb",
    )

    # Commit succeeds; find returns empty (conftest default) → no rebase.
    mock_shell.expect("qemu-img commit").returns(_success())
    mock_shell.expect("rm -f").returns(_success())
    # qemu-img check returns clean.
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
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [snap], disk="vdb", base_image=vdb_disk.base_image, deep_verify=True
    )

    assert result.success is True
    assert result.committed_snapshot == snap.name
    assert result.outcome == "success"

    check_cmd = next(" ".join(c[0]) for c in shell.run_calls if "qemu-img check" in " ".join(c[0]))
    assert str(vdb_disk.base_image) in check_cmd
    assert str(vda_disk.base_image) not in check_cmd


# ──────────────────────────────────────────────────────────────────────────
# Phase-5: _find_child disk pre-filter (multi-disk isolation)
# ──────────────────────────────────────────────────────────────────────────


def test_find_child_skips_other_disk_candidates(mock_shell: MockShell, make_vm_config):
    """_find_child skips candidates whose filename encodes a different disk.

    Scan dir contains:
      - vm.20250101T000000_vda_aaa111.qcow2 (vda child, backing=snap)
      - vm.20250101T000000_vdb_bbb222.qcow2 (vdb file, NOT inspected)

    Committing a vda snapshot: the vdb candidate must be skipped before
    qemu-img info is called.  Only the vda candidate should be inspected,
    and since its backing file matches, it is returned as the child.
    """
    from qsnap.models.config import DiskConfig

    snap_dir = "/var/lib/libvirt/snapshots/testvm"
    vda_disk = DiskConfig(target="vda", base_image=Path("/tmp/vda.qcow2"))
    vdb_disk = DiskConfig(
        target="vdb",
        base_image=Path("/tmp/vdb.qcow2"),
        snapshot_dir=Path(snap_dir),  # same dir for the purpose of the test
    )
    vm_config = make_vm_config(disks=[vda_disk, vdb_disk])

    snap = _make_snapshot(
        name="vm.20250101T000000_vda_fff000",
        path=f"{snap_dir}/vm.20250101T000000_vda_fff000.qcow2",
        disk="vda",
    )
    vda_child = f"{snap_dir}/vm.20250101T010000_vda_aaa111.qcow2"
    vdb_candidate = f"{snap_dir}/vm.20250101T010000_vdb_bbb222.qcow2"

    # Commit succeeds.
    mock_shell.expect("qemu-img commit").returns(_success())

    # find returns both files intermixed.
    find_out = vda_child + "\n" + vdb_candidate + "\n"
    mock_shell.expect_first(r"find.*maxdepth.*1.*-name.*\.qcow2").returns(
        ShellResult(success=True, stdout=find_out, stderr="", returncode=0, error=None)
    )

    # qemu-img info for vda_child: backing matches snap.
    info_json = json.dumps(
        {
            "full-backing-filename": str(snap.path),
            "format": "qcow2",
            "virtual-size": 10737418240,
        }
    )
    # Use a specific regex so we can track which file gets inspected.
    mock_shell.expect_first(r"qemu-img info.*" + vda_child.replace("/", r"\/")).returns(
        ShellResult(success=True, stdout=info_json, stderr="", returncode=0, error=None)
    )

    # rebase vda_child
    mock_shell.expect("qemu-img rebase").returns(_success())

    # rm
    mock_shell.expect("rm -f").returns(_success())

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap], disk="vda", base_image=vda_disk.base_image)

    assert result.success is True
    assert result.committed_snapshot == snap.name

    # Verify qemu-img info was called exactly once (for vda_child, not vdb_candidate).
    info_calls = [c for c in _cmd_strings(shell.calls) if "qemu-img info" in c]
    assert len(info_calls) == 1, (
        f"Expected 1 qemu-img info call, got {len(info_calls)}: {info_calls}"
    )
    assert vda_child in info_calls[0], (
        f"qemu-img info should inspect vda child, got: {info_calls[0]}"
    )
    assert vdb_candidate not in info_calls[0], (
        f"qemu-img info should NOT inspect vdb candidate, got: {info_calls[0]}"
    )


def test_find_child_still_inspects_unparseable_names(mock_shell: MockShell, make_vm_config):
    """Candidates whose names do not encode a disk are still inspected.

    Files like ``random.qcow2`` or ``base.qcow2`` do not match the
    ``{vm}.{ts}_{disk}_{hex}.qcow2`` pattern.  The pre-filter must NOT
    skip them — they are still inspected via qemu-img info because they
    may be the child overlay we are looking for (e.g. if the child was
    created or renamed outside of qsnap).
    """
    from qsnap.models.config import DiskConfig

    snap_dir = "/var/lib/libvirt/snapshots/testvm"
    vda_disk = DiskConfig(target="vda", base_image=Path("/tmp/vda.qcow2"))
    vm_config = make_vm_config(disks=[vda_disk])

    snap = _make_snapshot(
        name="vm.20250101T000000_vda_fff000",
        path=f"{snap_dir}/vm.20250101T000000_vda_fff000.qcow2",
        disk="vda",
    )
    unparseable = f"{snap_dir}/random.qcow2"

    # Commit succeeds.
    mock_shell.expect("qemu-img commit").returns(_success())

    # find returns the unparseable file.
    mock_shell.expect_first(r"find.*maxdepth.*1.*-name.*\.qcow2").returns(
        ShellResult(success=True, stdout=unparseable + "\n", stderr="", returncode=0, error=None)
    )

    # qemu-img info for unparseable: backing does NOT match snap.
    info_json = json.dumps(
        {
            "backing-filename": "other.qcow2",
            "format": "qcow2",
            "virtual-size": 10737418240,
        }
    )
    mock_shell.expect("qemu-img info").returns(
        ShellResult(success=True, stdout=info_json, stderr="", returncode=0, error=None)
    )

    # No child found → no rebase, just rm.
    mock_shell.expect("rm -f").returns(_success())

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap], disk="vda", base_image=vda_disk.base_image)

    assert result.success is True
    assert result.committed_snapshot == snap.name

    # Verify qemu-img info WAS called for the unparseable file.
    info_calls = [c for c in _cmd_strings(shell.calls) if "qemu-img info" in c]
    assert len(info_calls) == 1, (
        f"Expected qemu-img info to inspect unparseable file, got {len(info_calls)} calls"
    )
    assert "random.qcow2" in info_calls[0], (
        f"qemu-img info should include random.qcow2, got: {info_calls[0]}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Empty snapshot list — nothing to merge
# ──────────────────────────────────────────────────────────────────────────


def test_qemu_img_commit_empty_list_no_op(mock_shell: MockShell, make_vm_config):
    """An empty snapshot list is a no-op with ``outcome="success"``.

    lifecycle-manager spec "Empty snapshot list": the manager returns
    ``CommitResult(success=True, committed_snapshot="",
    outcome="success")`` immediately — ``success=True`` SHALL imply
    ``outcome="success"`` (result-types invariant).
    """
    vm_config = make_vm_config()

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(
        vm_config, [], disk="vda", base_image=vm_config.disks[0].base_image
    )

    assert result.success is True
    assert result.committed_snapshot == ""
    assert result.error is None
    assert result.outcome == "success"

    # No shell commands should have been executed at all.
    assert len(shell.calls) == 0
