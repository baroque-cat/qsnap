"""Tests for AllocationSizeDetector — change detection via allocation-size comparison.

These tests verify the four scenarios from the change-detection spec
(``openspec/changes/implement-domain-modules/specs/change-detection/spec.md``):

1. Allocation has grown — changes detected (changed=True)
2. Allocation unchanged — no changes (changed=False)
3. First run — no previous state (short-circuit, no shell calls)
4. Command failure — fail-safe (changed=True)

Design D3 verification (``design.md``):
    The detector MUST resolve the active disk path via ``virsh domblklist``,
    NOT ``vm_config.base_image``.  After a snapshot, the active image changes
    (new qcow2 becomes top of chain); ``base_image`` points to the OLD base.
    These tests use distinct paths for ``base_image`` ("/old/path.qcow2") and
    domblklist output ("/new/active/path.qcow2") to prove the detector uses
    the domblklist path when invoking ``qemu-img info``.
"""

from __future__ import annotations

import json

import pytest

from qsnap.models.results import ChangeResult, ShellResult
from qsnap.modules.change.allocation_detector import AllocationSizeDetector
from tests.mocks.mock_shell import MockShell

# ── Call-tracking MockShell subclass ──────────────────────────────────────


class CallTrackingShell(MockShell):
    """MockShell subclass that records every ``run()`` call for inspection.

    Used to verify design D3: the detector issues ``virsh domblklist`` and
    passes the resolved path (not ``base_image``) to ``qemu-img info``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str], timeout: int) -> ShellResult:
        self.calls.append(list(cmd))
        return super().run(cmd, timeout)


@pytest.fixture
def tracking_shell() -> CallTrackingShell:
    """A MockShell that records all ``run()`` calls."""
    return CallTrackingShell()


# ── Shared constants and helpers ──────────────────────────────────────────

#: Path returned by domblklist (the *active* image after a snapshot).
ACTIVE_DISK_PATH = "/new/active/path.qcow2"

#: Path stored in vm_config.base_image (the OLD base — must NOT be used).
OLD_BASE_IMAGE = "/old/path.qcow2"


def _domblklist_stdout() -> str:
    """Canned ``virsh domblklist`` output containing the active disk path."""
    return (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      " + ACTIVE_DISK_PATH + "\n"
    )


def _ok_domblklist_result() -> ShellResult:
    """A successful domblklist ShellResult."""
    return ShellResult(
        success=True,
        stdout=_domblklist_stdout(),
        stderr="",
        returncode=0,
        error=None,
    )


def _ok_qemu_img_result(actual_size: int) -> ShellResult:
    """A successful ``qemu-img info`` ShellResult with the given actual-size."""
    return ShellResult(
        success=True,
        stdout=json.dumps({"actual-size": actual_size}),
        stderr="",
        returncode=0,
        error=None,
    )


def _failed_result() -> ShellResult:
    """A failed ShellResult (non-zero exit code)."""
    return ShellResult(
        success=False,
        stdout="",
        stderr="error: command failed",
        returncode=1,
        error="Command failed",
    )


def _assert_d3_paths(tracking_shell: CallTrackingShell) -> None:
    """Assert design D3: domblklist was called and qemu-img used its path.

    * ``virsh domblklist`` was issued exactly once.
    * ``qemu-img info`` was issued exactly once with the path from domblklist
      output (``ACTIVE_DISK_PATH``), NOT ``OLD_BASE_IMAGE``.
    """
    # domblklist was called
    domblklist_calls = [
        c for c in tracking_shell.calls if "domblklist" in " ".join(c)
    ]
    assert len(domblklist_calls) == 1, (
        "Expected exactly one domblklist call, got "
        f"{len(domblklist_calls)}"
    )

    # qemu-img info was called with the domblklist path, not base_image
    qemu_calls = [
        c for c in tracking_shell.calls if "qemu-img" in " ".join(c)
    ]
    assert len(qemu_calls) == 1, (
        "Expected exactly one qemu-img call, got "
        f"{len(qemu_calls)}"
    )
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert ACTIVE_DISK_PATH in qemu_cmd_str, (
        f"qemu-img info should use the domblklist path "
        f"({ACTIVE_DISK_PATH}), but command was: {qemu_cmd_str}"
    )
    assert OLD_BASE_IMAGE not in qemu_cmd_str, (
        f"qemu-img info must NOT use base_image ({OLD_BASE_IMAGE}), "
        f"but command was: {qemu_cmd_str}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. Allocation has grown — changes detected
# ──────────────────────────────────────────────────────────────────────────


def test_has_changed_allocation_grown(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """Allocation grew from 65536 to 131072 — changed=True.

    Also verifies design D3: the detector resolves the active disk path via
    ``virsh domblklist`` (returning ``/new/active/path.qcow2``), NOT
    ``vm_config.base_image`` (set to ``/old/path.qcow2``).  The ``qemu-img
    info`` expectation is configured to only match the new path; if the
    detector used the old base_image path, the command would not match and
    the test would fail.
    """
    mock_state.set_last_allocation("testvm", 65536)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    # Pattern includes the domblklist path — only matches if D3 is respected
    tracking_shell.expect("qemu-img info.*new/active/path").returns(
        _ok_qemu_img_result(131072)
    )

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = AllocationSizeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config)

    assert result == ChangeResult(
        changed=True,
        last_allocation=65536,
        current_allocation=131072,
    )

    # Design D3: domblklist called, qemu-img used domblklist path
    _assert_d3_paths(tracking_shell)


# ──────────────────────────────────────────────────────────────────────────
# 2. Allocation unchanged — no changes
# ──────────────────────────────────────────────────────────────────────────


def test_has_changed_allocation_unchanged(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """Allocation stayed at 65536 — changed=False.

    Also verifies design D3: the active disk path comes from domblklist
    output (``/new/active/path.qcow2``), not ``base_image``
    (``/old/path.qcow2``).
    """
    mock_state.set_last_allocation("testvm", 65536)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img info.*new/active/path").returns(
        _ok_qemu_img_result(65536)
    )

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = AllocationSizeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config)

    assert result == ChangeResult(
        changed=False,
        last_allocation=65536,
        current_allocation=65536,
    )

    # Design D3: domblklist called, qemu-img used domblklist path
    _assert_d3_paths(tracking_shell)


# ──────────────────────────────────────────────────────────────────────────
# 3. First run — no previous state
# ──────────────────────────────────────────────────────────────────────────


def test_has_changed_first_run_no_state(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """First run: state has no last_allocation (None) — short-circuit.

    The detector returns ``changed=True`` immediately without calling
    any shell commands (domblklist and qemu-img info are NOT executed).
    This guarantees the first snapshot is always created.
    """
    # Do NOT set last_allocation — state returns None for "testvm"

    # Set up expectations anyway — if the detector incorrectly calls
    # a command, these would match, but the calls list would be non-empty
    # and the assertion below would fail.
    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img info").returns(_ok_qemu_img_result(131072))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = AllocationSizeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config)

    assert result == ChangeResult(
        changed=True,
        last_allocation=0,
        current_allocation=0,
    )

    # No shell commands should have been executed — the method returns
    # immediately when state is None.
    assert tracking_shell.calls == [], (
        "No shell commands should be executed on first run (state is None), "
        f"but got: {tracking_shell.calls}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 4. Command fails — fail-safe
# ──────────────────────────────────────────────────────────────────────────


def test_has_changed_command_fails_failsafe(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """domblklist fails (non-zero exit) — fail-safe: changed=True.

    Rather create an unnecessary snapshot than miss changes.  The detector
    should return ``changed=True`` with ``last_allocation`` preserved
    from state and ``current_allocation=0`` (unknown due to failure).
    """
    mock_state.set_last_allocation("testvm", 65536)

    tracking_shell.expect("virsh domblklist").returns(_failed_result())

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = AllocationSizeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config)

    assert result == ChangeResult(
        changed=True,
        last_allocation=65536,
        current_allocation=0,
    )

    # domblklist was called (and failed), qemu-img info was NOT called
    domblklist_calls = [
        c for c in tracking_shell.calls if "domblklist" in " ".join(c)
    ]
    assert len(domblklist_calls) == 1

    qemu_calls = [
        c for c in tracking_shell.calls if "qemu-img" in " ".join(c)
    ]
    assert len(qemu_calls) == 0, (
        "qemu-img info should NOT be called when domblklist fails"
    )


# ──────────────────────────────────────────────────────────────────────────
# 5. Per-disk selection — vdb uses vdb path
# ──────────────────────────────────────────────────────────────────────────


def test_has_changed_per_disk_vdb_uses_vdb_path(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """When ``disk='vdb'`` is passed, the detector uses the vdb path from
    domblklist (not vda).  Mock domblklist with both vda and vdb, verify
    ``qemu-img info`` is called on vdb's path.
    """
    mock_state.set_last_allocation("testvm", 65536)

    vda_path = "/var/lib/libvirt/images/testvm.qcow2"
    vdb_path = "/var/lib/libvirt/images/testvm-disk2.qcow2"

    domblklist_output = (
        " Target   Source\n"
        "------------------------------------\n"
        f" vda      {vda_path}\n"
        f" vdb      {vdb_path}\n"
    )
    tracking_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=domblklist_output,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    tracking_shell.expect("qemu-img info").returns(
        _ok_qemu_img_result(131072)
    )

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = AllocationSizeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, disk="vdb")

    assert result == ChangeResult(
        changed=True,
        last_allocation=65536,
        current_allocation=131072,
    )

    # Verify qemu-img info was called with vdb path, not vda path
    qemu_calls = [
        c for c in tracking_shell.calls if "qemu-img" in " ".join(c)
    ]
    assert len(qemu_calls) == 1
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert vdb_path in qemu_cmd_str, (
        f"qemu-img info should use vdb path ({vdb_path}), "
        f"but command was: {qemu_cmd_str}"
    )
    assert vda_path not in qemu_cmd_str, (
        f"qemu-img info must NOT use vda path ({vda_path}), "
        f"but command was: {qemu_cmd_str}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 6. No disk specified — backward compatible (first disk)
# ──────────────────────────────────────────────────────────────────────────


def test_has_changed_no_disk_uses_first_disk_backward_compatible(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """When ``disk=None`` (default), the detector uses the first disk from
    domblklist (backward compatible with pre-per-disk behaviour).
    """
    mock_state.set_last_allocation("testvm", 65536)

    vda_path = "/var/lib/libvirt/images/testvm.qcow2"
    vdb_path = "/var/lib/libvirt/images/testvm-disk2.qcow2"

    domblklist_output = (
        " Target   Source\n"
        "------------------------------------\n"
        f" vda      {vda_path}\n"
        f" vdb      {vdb_path}\n"
    )
    tracking_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=domblklist_output,
            stderr="",
            returncode=0,
            error=None,
        )
    )
    tracking_shell.expect("qemu-img info").returns(
        _ok_qemu_img_result(65536)
    )

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = AllocationSizeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config)  # disk=None (default)

    assert result == ChangeResult(
        changed=False,
        last_allocation=65536,
        current_allocation=65536,
    )

    # Verify qemu-img info was called with vda path (first disk)
    qemu_calls = [
        c for c in tracking_shell.calls if "qemu-img" in " ".join(c)
    ]
    assert len(qemu_calls) == 1
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert vda_path in qemu_cmd_str, (
        f"qemu-img info should use first disk path ({vda_path}), "
        f"but command was: {qemu_cmd_str}"
    )
    assert vdb_path not in qemu_cmd_str


# ──────────────────────────────────────────────────────────────────────────
# 7. Shared parser imports
# ──────────────────────────────────────────────────────────────────────────


def test_allocation_detector_imports_shared_parsers():
    """Verify ``allocation_detector.py`` imports shared parsers from
    ``qsnap.utils.parsing`` (not local duplicates).
    """
    from qsnap.modules.change import allocation_detector
    from qsnap.utils.parsing import parse_domblklist_disks

    assert hasattr(allocation_detector, "parse_domblklist_disks")
    assert allocation_detector.parse_domblklist_disks is parse_domblklist_disks
