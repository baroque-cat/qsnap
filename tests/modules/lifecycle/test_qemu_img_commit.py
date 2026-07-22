"""Tests for QemuImgCommitManager -- backing chain lifecycle via qemu-img commit.

These tests exercise ``QemuImgCommitManager`` (implements ``ILifecycleManager``)
in complete isolation.  All ``qemu-img`` calls go through a mocked ``IShell``.
The manager does NOT inherit from Core (design D1) and takes only ``IShell``
as a constructor dependency.

Rewired for design D4 (no ``-d`` flag) — per-snapshot algorithm (oldest first):
  1. ``qemu-img commit -b <base> <snap>``
  2. child discovery (find + qemu-img info scan)
  3. ``qemu-img rebase -u -F qcow2 -b <base> <child>`` (if child exists)
  4. ``rm -f <snap>`` (only after successful rebase, or when no child)
Short-circuit on ANY step failure.  ``deep_verify`` runs ``qemu-img check``.
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
) -> SnapshotInfo:
    """Create a ``SnapshotInfo`` with sensible defaults."""
    return SnapshotInfo(
        name=name,
        path=Path(path),
        timestamp=timestamp or datetime(2025, 1, 1, 0, 0, 0),
        allocation=allocation,
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

    - ``qemu-img commit -b <base> <snap>`` returns exit 0.
    - find returns empty (no child overlay — conftest fixture default).
    - No rebase needed.
    - ``rm -f <snap>`` deletes the committed file.
    - Result: ``CommitResult(success=True, committed_snapshot=<snap.name>)``.
    """
    vm_config = make_vm_config()
    snap = _make_snapshot()

    mock_shell.expect("qemu-img commit").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap])

    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == snap.name
    assert result.error is None

    # Verify command sequence: commit → find → rm (no rebase).
    cmds = _cmd_strings(shell.calls)
    _assert_call_order(
        cmds,
        ["qemu-img commit", "find", "rm -f"],
        "D4 commit sequence",
    )
    assert not any("rebase" in c for c in cmds), "rebase was called unexpectedly"


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
    mock_shell.expect("qemu-img commit").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

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
    mock_shell.expect("qemu-img rebase").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # Step d: delete s1
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    # --- Execute ---
    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)
    result = manager.blockcommit(vm_config, [s1])

    # --- Assertions ---
    assert isinstance(result, CommitResult)
    assert result.success is True
    assert result.committed_snapshot == s1.name
    assert result.error is None

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

    mock_shell.expect("qemu-img commit").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

    shell = CountingShell(mock_shell)
    manager = QemuImgCommitManager(shell=shell)

    result = manager.blockcommit(vm_config, [snap])

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

    result = manager.blockcommit(vm_config, [s1, s2])

    assert isinstance(result, CommitResult)
    assert result.success is False
    assert result.committed_snapshot == s1.name
    assert result.error == error_msg

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
    mock_shell.expect("qemu-img commit").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )

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

    result = manager.blockcommit(vm_config, [s1])

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

    result = manager.blockcommit(vm_config, [snap])

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

    result = manager.blockcommit(vm_config, [snap])

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
    mock_shell.expect("qemu-img commit").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    # find returns empty (no child) — conftest default
    # rm succeeds
    mock_shell.expect("rm -f").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
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

    result = manager.blockcommit(vm_config, [snap], deep_verify=True)

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
    assert str(vm_config.base_image) in check_cmd
