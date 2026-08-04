"""Tests for MapChangeDetector — change detection via qemu-img map comparison.

These tests verify that MapChangeDetector correctly detects changes by
comparing a SHA-256 hash of the sorted ``(offset, length)`` tuples from
``qemu-img map --output=json`` against the prior recorded value in
``IStateManager``.

Test scenarios:
    1. Map changed — different JSON than stored → changed=True
    2. Map unchanged — same JSON as stored → changed=False
    3. New region added → changed=True
    4. qemu-img map command fails → fail-safe changed=True
    5. Same total size but different regions → changed=True (key advantage
       over AllocationSizeDetector, which would miss this)
    6. Large JSON (10K+ regions) parses without memory spike
    7. Malformed JSON → fail-safe changed=True

Design D3 verification:
    The detector MUST resolve the active disk path via ``virsh domblklist``,
    NOT ``vm_config.base_image``.  Tests use distinct paths for
    ``base_image`` ("/old/path.qcow2") and domblklist output
    ("/new/active/path.qcow2") to prove the detector uses the domblklist
    path when invoking ``qemu-img map``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from qsnap.models.results import ChangeResult, ShellResult
from qsnap.modules.change.map_detector import MapChangeDetector
from tests.mocks.mock_shell import MockShell

# ── Call-tracking MockShell subclass ──────────────────────────────────────


class CallTrackingShell(MockShell):
    """MockShell subclass that records every ``run()`` call for inspection.

    Used to verify design D3: the detector issues ``virsh domblklist`` and
    passes the resolved path (not ``base_image``) to ``qemu-img map``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        self.calls.append(list(cmd))
        return super().run(cmd, timeout, check)


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


def _ok_map_result(regions: list[dict]) -> ShellResult:
    """A successful ``qemu-img map --output=json`` ShellResult."""
    return ShellResult(
        success=True,
        stdout=json.dumps(regions),
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


def _compute_map_hash(regions: list[dict]) -> int:
    """Compute the map hash exactly as MapChangeDetector does.

    Replicates the detector's hash computation so tests can seed
    ``IStateManager`` with the correct stored value for "unchanged"
    scenarios.
    """
    offsets = sorted((int(r.get("offset", 0)), int(r.get("length", 0))) for r in regions)
    map_hash = int(
        hashlib.sha256(repr(offsets).encode()).hexdigest(),
        16,
    )
    return map_hash % (2**31)


def _assert_d3_paths(tracking_shell: CallTrackingShell) -> None:
    """Assert design D3: domblklist was called and qemu-img map used its path.

    * ``virsh domblklist`` was issued exactly once.
    * ``qemu-img map`` was issued exactly once with the path from domblklist
      output (``ACTIVE_DISK_PATH``), NOT ``OLD_BASE_IMAGE``.
    """
    domblklist_calls = [c for c in tracking_shell.calls if "domblklist" in " ".join(c)]
    assert len(domblklist_calls) == 1, (
        f"Expected exactly one domblklist call, got {len(domblklist_calls)}"
    )

    qemu_calls = [c for c in tracking_shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1, f"Expected exactly one qemu-img call, got {len(qemu_calls)}"
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert ACTIVE_DISK_PATH in qemu_cmd_str, (
        f"qemu-img map should use the domblklist path "
        f"({ACTIVE_DISK_PATH}), but command was: {qemu_cmd_str}"
    )
    assert OLD_BASE_IMAGE not in qemu_cmd_str, (
        f"qemu-img map must NOT use base_image ({OLD_BASE_IMAGE}), but command was: {qemu_cmd_str}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. Map changed — different JSON than stored → changed=True
# ──────────────────────────────────────────────────────────────────────────


def test_map_changed_detected(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """qemu-img map returns different JSON than stored → changed=True.

    The stored hash corresponds to a single 1024-byte region at offset 0.
    The current map returns a single 2048-byte region at offset 0 — a
    different allocation map, so the hash differs and changed=True.

    Also verifies design D3: the detector resolves the active disk path via
    ``virsh domblklist`` (returning ``/new/active/path.qcow2``), NOT
    ``vm_config.base_image`` (set to ``/old/path.qcow2``).
    """
    old_regions = [{"offset": 0, "length": 1024}]
    new_regions = [{"offset": 0, "length": 2048}]

    old_hash = _compute_map_hash(old_regions)
    mock_state.set_last_allocation("testvm", "vda", old_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    # Pattern includes the domblklist path — only matches if D3 is respected
    tracking_shell.expect("qemu-img map.*new/active/path").returns(_ok_map_result(new_regions))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is True
    assert result.last_allocation == old_hash
    assert result.current_allocation == _compute_map_hash(new_regions)

    # Design D3: domblklist called, qemu-img map used domblklist path
    _assert_d3_paths(tracking_shell)

    # Design D5: qemu-img map includes --force-share
    qemu_calls = [c for c in tracking_shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert "--force-share" in qemu_cmd_str


# ──────────────────────────────────────────────────────────────────────────
# 2. Map unchanged — same JSON as stored → changed=False
# ──────────────────────────────────────────────────────────────────────────


def test_map_unchanged_no_changes(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """qemu-img map returns same JSON as stored → changed=False.

    The stored hash and the current map hash are identical (same regions),
    so no change is detected.

    Also verifies design D3: the active disk path comes from domblklist
    output (``/new/active/path.qcow2``), not ``base_image``
    (``/old/path.qcow2``).
    """
    regions = [
        {"offset": 0, "length": 1024},
        {"offset": 2048, "length": 512},
    ]

    stored_hash = _compute_map_hash(regions)
    mock_state.set_last_allocation("testvm", "vda", stored_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map.*new/active/path").returns(_ok_map_result(regions))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is False
    assert result.last_allocation == stored_hash
    assert result.current_allocation == stored_hash

    # Design D3: domblklist called, qemu-img map used domblklist path
    _assert_d3_paths(tracking_shell)

    # Design D5: qemu-img map includes --force-share
    qemu_calls = [c for c in tracking_shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert "--force-share" in qemu_cmd_str


# ──────────────────────────────────────────────────────────────────────────
# 3. New allocated region added → changed=True
# ──────────────────────────────────────────────────────────────────────────


def test_map_changed_new_region(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """A new allocated region is added to the map → changed=True.

    The stored map has one region (offset 0, length 1024).  The current
    map has that region plus a new one (offset 4096, length 2048).  The
    sorted (offset, length) tuples differ, so the hash differs and
    changed=True.
    """
    old_regions = [{"offset": 0, "length": 1024}]
    new_regions = [
        {"offset": 0, "length": 1024},
        {"offset": 4096, "length": 2048},
    ]

    old_hash = _compute_map_hash(old_regions)
    mock_state.set_last_allocation("testvm", "vda", old_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map.*new/active/path").returns(_ok_map_result(new_regions))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is True
    assert result.last_allocation == old_hash
    assert result.current_allocation == _compute_map_hash(new_regions)


# ──────────────────────────────────────────────────────────────────────────
# 4. qemu-img map command fails — fail-safe changed=True
# ──────────────────────────────────────────────────────────────────────────


def test_map_command_fails_failsafe(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """qemu-img map fails (non-zero exit) → fail-safe: changed=True.

    Rather create an unnecessary snapshot than miss changes.  The detector
    returns ``changed=True`` with ``last_allocation`` preserved from state
    and ``current_allocation=0`` (unknown due to failure).
    """
    old_regions = [{"offset": 0, "length": 1024}]
    old_hash = _compute_map_hash(old_regions)
    mock_state.set_last_allocation("testvm", "vda", old_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map").returns(_failed_result())

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is True
    assert result.last_allocation == old_hash
    assert result.current_allocation == 0

    # domblklist was called (and succeeded), qemu-img map was called (and
    # failed) — no further commands should have been issued.
    domblklist_calls = [c for c in tracking_shell.calls if "domblklist" in " ".join(c)]
    assert len(domblklist_calls) == 1

    qemu_calls = [c for c in tracking_shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1

    # Design D5: --force-share is present even when command fails
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert "--force-share" in qemu_cmd_str


# ──────────────────────────────────────────────────────────────────────────
# 5. Same total size but different regions → changed=True
# ──────────────────────────────────────────────────────────────────────────


def test_zero_fill_changes_map_not_size(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """Same total allocation size but different region layout → changed=True.

    This is the key advantage of MapChangeDetector over
    AllocationSizeDetector: it detects *structural* changes in the
    allocation map, not just size growth.

    The stored map has one region (offset 0, length 1024) — total 1024
    bytes.  The current map has two regions (offset 0, length 512 and
    offset 2048, length 512) — also total 1024 bytes.  An
    AllocationSizeDetector would report ``changed=False`` (same size),
    but MapChangeDetector reports ``changed=True`` because the region
    layout differs.
    """
    # Map A: one contiguous region, total 1024 bytes
    old_regions = [{"offset": 0, "length": 1024}]
    # Map B: two fragmented regions, total 1024 bytes — same size,
    # different layout
    new_regions = [
        {"offset": 0, "length": 512},
        {"offset": 2048, "length": 512},
    ]

    old_hash = _compute_map_hash(old_regions)
    mock_state.set_last_allocation("testvm", "vda", old_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map.*new/active/path").returns(_ok_map_result(new_regions))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is True
    assert result.last_allocation == old_hash
    assert result.current_allocation == _compute_map_hash(new_regions)

    # Sanity: the two maps have the same total allocated length
    old_total = sum(r["length"] for r in old_regions)
    new_total = sum(r["length"] for r in new_regions)
    assert old_total == new_total, (
        "Test setup error: old and new maps should have the same total "
        f"length ({old_total} vs {new_total})"
    )

    # Design D5: qemu-img map includes --force-share
    qemu_calls = [c for c in tracking_shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert "--force-share" in qemu_cmd_str


# ──────────────────────────────────────────────────────────────────────────
# 6. Risk: large JSON (10K+ regions) parses without memory spike
# ──────────────────────────────────────────────────────────────────────────


def test_risk_map_large_json_handled(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """10K+ region JSON parses without memory spike or timeout.

    Generates 10,000 allocated regions (one per 4K block) and verifies the
    detector parses, sorts, hashes, and compares correctly.  The stored
    hash matches the current map, so ``changed=False``.

    This is a risk test: if the parsing or hashing algorithm had quadratic
    behaviour or excessive memory usage, this test would be slow or fail
    under the 60s pytest-timeout.
    """
    large_regions = [{"offset": i * 4096, "length": 4096} for i in range(10000)]

    stored_hash = _compute_map_hash(large_regions)
    mock_state.set_last_allocation("testvm", "vda", stored_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map.*new/active/path").returns(_ok_map_result(large_regions))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is False
    assert result.last_allocation == stored_hash
    assert result.current_allocation == stored_hash
    assert isinstance(result, ChangeResult)


# ──────────────────────────────────────────────────────────────────────────
# 7. Risk: malformed JSON → fail-safe changed=True
# ──────────────────────────────────────────────────────────────────────────


def test_risk_map_fallback_on_parse_error(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """Malformed JSON output from qemu-img map → fail-safe: changed=True.

    ``qemu-img map`` returns a successful exit code but the stdout is not
    valid JSON (e.g., truncated output, encoding error).  The detector
    catches ``json.JSONDecodeError`` and returns ``changed=True`` rather
    than crashing or silently reporting no change.
    """
    old_regions = [{"offset": 0, "length": 1024}]
    old_hash = _compute_map_hash(old_regions)
    mock_state.set_last_allocation("testvm", "vda", old_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map").returns(
        ShellResult(
            success=True,
            stdout="not valid json {{{ broken",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    assert result.changed is True
    assert result.last_allocation == old_hash
    assert result.current_allocation == 0


# ──────────────────────────────────────────────────────────────────────────
# 8. Map on running VM uses --force-share (design D5)
# ──────────────────────────────────────────────────────────────────────────


def test_map_on_running_vm_uses_force_share(
    tracking_shell: CallTrackingShell,
    mock_state,
    make_vm_config,
):
    """``qemu-img map`` on a running VM includes ``--force-share`` so it
    can read the allocation map despite the VM holding an exclusive write
    lock (design D5).

    The detector resolves the active disk via ``virsh domblklist``, then
    calls ``qemu-img map --force-share --output=json`` on the resolved
    path.  The command succeeds even though the VM is running.
    """
    regions = [{"offset": 0, "length": 4096}]
    stored_hash = _compute_map_hash(regions)
    mock_state.set_last_allocation("testvm", "vda", stored_hash)

    tracking_shell.expect("virsh domblklist").returns(_ok_domblklist_result())
    tracking_shell.expect("qemu-img map.*new/active/path").returns(_ok_map_result(regions))

    vm_config = make_vm_config(name="testvm", base_image=OLD_BASE_IMAGE)
    detector = MapChangeDetector(shell=tracking_shell, state=mock_state)
    result = detector.has_changed(vm_config, "vda")

    # Command succeeds despite VM holding write lock
    assert result.changed is False
    assert result.last_allocation == stored_hash
    assert result.current_allocation == stored_hash

    # Design D5: qemu-img map includes --force-share
    qemu_calls = [c for c in tracking_shell.calls if "qemu-img" in " ".join(c)]
    assert len(qemu_calls) == 1
    qemu_cmd_str = " ".join(qemu_calls[0])
    assert "--force-share" in qemu_cmd_str, (
        f"qemu-img map command must include --force-share, got: {qemu_cmd_str}"
    )
    assert "--output=json" in qemu_cmd_str
    assert ACTIVE_DISK_PATH in qemu_cmd_str
