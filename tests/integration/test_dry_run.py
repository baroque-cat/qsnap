"""Integration tests for dry-run predictions and zero-mutation guarantees.

All tests in this module require a running libvirt daemon and are
marked ``@pytest.mark.integration``.  They use the ``test_vm`` and
``test_vm_multi_disk`` fixtures from ``conftest.py``.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_dry_run.py -v -m integration
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.shell import IShell
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False


# ── helpers ──────────────────────────────────────────────────────────


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints for *vm_name*."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        cp = line.strip()
        if cp and cp.startswith("qsnap-"):
            shell.run(
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp],
                timeout=30,
            )


def _snapshot_create(
    shell: SubprocessShell,
    vm_name: str,
    snap_name: str,
    base_image: Path,
    snapshot_dir: Path,
    disk: str = "vda",
) -> SnapshotInfo:
    """Create an external disk-only snapshot and return ``SnapshotInfo``."""
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    result = provider.create(
        VMConfig(
            name=vm_name,
            disks=[DiskConfig(target=disk, base_image=base_image)],
            snapshot_dir=snapshot_dir,
        ),
        snap_name,
        disk,
        snap_path,
    )
    assert result.success, f"Snapshot creation failed: {result.error}"
    return SnapshotInfo(
        name=result.name,
        path=result.path,
        timestamp=datetime.now(),
        allocation=result.new_allocation,
        disk=disk,
    )


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    *,
    disks: list[DiskConfig] | None = None,
    snapshot_chain_length: int = 99,
) -> tuple[Core, VMConfig, InMemoryStateManager]:
    """Build a Core instance with InMemoryStateManager and DefaultFactory."""
    state = InMemoryStateManager()
    disk_configs = disks or [DiskConfig(target="vda", base_image=base_image)]
    vm_config = VMConfig(
        name=vm_name,
        disks=disk_configs,
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=snapshot_chain_length,
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(state_dir="/var/tmp"),
        vms=[vm_config],
        config_path=target_dir / "test_dry_run.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _file_counts_by_extension(directory: Path, ext: str) -> int:
    """Return the number of files matching *ext* in *directory*."""
    return len(list(directory.glob(f"*.{ext}")))


def _zero_mutation_assertions(
    vm_name: str,
    snapshot_dir: Path,
    target_dir: Path,
    state: InMemoryStateManager,
    result,
    initial_snap_count: int,
    initial_target_count: int,
) -> None:
    """Shared assertions for zero-mutation tests."""
    # 1. actions is empty
    assert result.actions == [], f"Expected no executed actions in dry-run, got: {result.actions}"
    # 2. dry_run flag is True
    assert result.dry_run is True, f"Expected dry_run=True, got {result.dry_run}"
    # 3. predictions exist
    assert len(result.predictions) > 0, (
        f"Expected >0 predictions in dry-run, got {len(result.predictions)}"
    )
    # 4. No new snapshot files appeared
    final_snap_count = _file_counts_by_extension(snapshot_dir, "qcow2")
    assert final_snap_count == initial_snap_count, (
        f"Expected {initial_snap_count} snapshot files, got {final_snap_count}"
    )
    # 5. No new backup files appeared in target dir
    final_target_count = _file_counts_by_extension(target_dir, "qcow2")
    assert final_target_count == initial_target_count, (
        f"Expected {initial_target_count} target files, got {final_target_count}"
    )
    # 6. State unchanged — no snapshots recorded
    assert state.get_snapshots(vm_name) == [], "State should have no snapshots after dry-run"
    # 7. State unchanged — no FULL backups recorded
    assert state.get_full_backups(str(target_dir)) == [], (
        "State should have no FULL backups after dry-run"
    )
    # 8. State unchanged — no deferred ops
    assert state.get_deferred_operations(vm_name) == [], (
        "State should have no deferred ops after dry-run"
    )


# ── RecordingShell (for test 7) ───────────────────────────


class RecordingShell(IShell):
    """IShell wrapper that delegates to SubprocessShell and records commands.

    Used to assert that dry-run mode only executes read-only commands.
    """

    def __init__(self, delegate: SubprocessShell) -> None:
        self._delegate = delegate
        self._commands: list[list[str]] = []

    @property
    def commands(self) -> list[list[str]]:
        """The list of recorded command lists (in execution order)."""
        return list(self._commands)

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run(cmd, timeout, check)

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run_with_stall_detection(cmd, output_file, stall_timeout, check)


# ── Read-only command prefixes (allowlist) ──────────────────────────

# Every command recorded during dry-run MUST start with one of these
# prefixes.  Any command that does not match is a test failure — the
# assertion message lists all offenders (none silently ignored).
_READ_ONLY_PREFIXES = (
    "qemu-img info",
    "virsh domstate",
    "virsh dominfo",
    "virsh domblklist",
    "virsh dumpxml",
    "virsh checkpoint-list",
    "virsh --version",
    "test ",
    "which ",
    "find ",
    "du ",
)


def _is_read_only(cmd_list: list[str]) -> bool:
    """Return True if *cmd_list* starts with exactly one allowed prefix.

    Uses an allowlist approach: only commands whose joined string
    starts with a known read-only prefix are accepted.  Anything
    else — even seemingly harmless unknown commands — causes the
    test to fail with all offending commands collected.
    """
    cmd_str = " ".join(cmd_list)
    return any(cmd_str.startswith(prefix) for prefix in _READ_ONLY_PREFIXES)


# ── Test 1: Zero mutation single disk ─────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dry_run_zero_mutation_single_disk(test_vm):
    """Run Core pipeline in dry-run mode against test_vm; assert zero mutations.

    Verifies:
    - No new files appear in snapshot dir or target dir.
    - State is unchanged.
    - ``result.actions == []``.
    - ``result.dry_run is True``.
    - ``len(result.predictions) > 0``.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Snapshot the initial state: count qcow2 files in each directory.
    initial_snap_count = _file_counts_by_extension(snapshot_dir, "qcow2")
    initial_target_count = _file_counts_by_extension(target_dir, "qcow2")

    # The test_vm fixture creates a base_image which is already in tmpdir,
    # not in snapshot_dir.  Count snapshot_dir files for the assertion.
    # Actually the base_image is in tmpdir root; snapshot_dir is separate.

    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)
    core.dry_run = True

    result = core.run(vm_name)

    _zero_mutation_assertions(
        vm_name,
        snapshot_dir,
        target_dir,
        state,
        result,
        initial_snap_count,
        initial_target_count,
    )


# ── Test 2: Zero mutation multi disk ──────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dry_run_zero_mutation_multi_disk(test_vm_multi_disk):
    """Run Core pipeline in dry-run mode against test_vm_multi_disk; assert zero mutations.

    Verifies zero mutation for every configured disk.
    """
    shell: SubprocessShell = test_vm_multi_disk["shell"]
    vm_name: str = test_vm_multi_disk["vm_name"]
    base_images: dict[str, Path] = test_vm_multi_disk["base_images"]
    snapshot_dirs: dict[str, Path] = test_vm_multi_disk["snapshot_dirs"]
    target_dir: Path = test_vm_multi_disk["target_dir"]
    disk_configs: list[DiskConfig] = test_vm_multi_disk["disk_configs"]

    # Count existing qcow2 files per snapshot dir
    initial_counts: dict[str, int] = {}
    for disk, sdir in snapshot_dirs.items():
        initial_counts[disk] = _file_counts_by_extension(sdir, "qcow2")
    initial_target_count = _file_counts_by_extension(target_dir, "qcow2")

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_images["vda"],  # base_image used only when disks=None; we pass explicit disks
        snapshot_dir=snapshot_dirs["vda"],  # VM-level default
        target_dir=target_dir,
        disks=disk_configs,
    )
    core.dry_run = True

    result = core.run(vm_name)

    # Common assertions
    assert result.actions == [], f"Expected no executed actions in dry-run, got: {result.actions}"
    assert result.dry_run is True, f"Expected dry_run=True, got {result.dry_run}"
    assert len(result.predictions) > 0, (
        f"Expected >0 predictions in dry-run, got {len(result.predictions)}"
    )

    # No new files in any snapshot dir
    for disk, sdir in snapshot_dirs.items():
        final_count = _file_counts_by_extension(sdir, "qcow2")
        assert final_count == initial_counts[disk], (
            f"Disk {disk}: expected {initial_counts[disk]} snapshot files, got {final_count}"
        )

    final_target_count = _file_counts_by_extension(target_dir, "qcow2")
    assert final_target_count == initial_target_count, (
        f"Expected {initial_target_count} target files, got {final_target_count}"
    )

    # State unchanged
    assert state.get_snapshots(vm_name) == [], "State should have no snapshots after dry-run"
    assert state.get_full_backups(str(target_dir)) == [], (
        "State should have no FULL backups after dry-run"
    )
    assert state.get_deferred_operations(vm_name) == [], (
        "State should have no deferred ops after dry-run"
    )


# ── Test 3: Predictions per disk present ───────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dry_run_predictions_per_disk_present(test_vm_multi_disk):
    """Predictions carry per-disk ``disk`` fields covering each configured disk.

    The multi-disk VM has disks vda and vdb.  After a dry-run, the
    ``snapshot_create`` predictions must include at least one record
    with ``disk="vda"`` and at least one with ``disk="vdb"``.
    """
    shell: SubprocessShell = test_vm_multi_disk["shell"]
    vm_name: str = test_vm_multi_disk["vm_name"]
    base_images: dict[str, Path] = test_vm_multi_disk["base_images"]
    snapshot_dirs: dict[str, Path] = test_vm_multi_disk["snapshot_dirs"]
    target_dir: Path = test_vm_multi_disk["target_dir"]
    disk_configs: list[DiskConfig] = test_vm_multi_disk["disk_configs"]

    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_images["vda"],
        snapshot_dir=snapshot_dirs["vda"],
        target_dir=target_dir,
        disks=disk_configs,
    )
    core.dry_run = True

    result = core.run(vm_name)

    # Filter snapshot_create predictions
    snap_predictions = [p for p in result.predictions if p.action == "snapshot_create"]
    assert snap_predictions, "Expected at least one snapshot_create prediction"

    disk_set = {p.disk for p in snap_predictions}
    configured_disks = {d.target for d in disk_configs}
    for disk_target in configured_disks:
        assert disk_target in disk_set, (
            f"Expected snapshot_create prediction for disk '{disk_target}', got disks: {disk_set}"
        )


# ── Test 4: Deferred queue unchanged ──────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dry_run_deferred_queue_unchanged(test_vm):
    """Seed a deferred blockcommit entry, run dry-run, assert queue unchanged.

    The deferred queue in state must be byte-for-byte unchanged (same
    entries, same order), and no blockcommit was executed.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Build Core, create a real snapshot, record it in state.
    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)

    snap_name = f"{vm_name}.deferred-test"
    snap = _snapshot_create(shell, vm_name, snap_name, base_image, snapshot_dir)
    state.record_snapshot(vm_name, snap)

    # Seed a deferred blockcommit entry
    state.add_deferred_blockcommit(
        vm_name,
        "vda",
        snapshots=[snap.name],
        reason="vm_running",
    )

    # Record the queue before dry-run
    deferred_before = state.get_deferred_operations(vm_name)
    assert len(deferred_before) == 1, "Expected exactly 1 deferred entry"

    # Set dry-run and execute
    core.dry_run = True
    result = core.run(vm_name)

    # Deferred queue must be unchanged
    deferred_after = state.get_deferred_operations(vm_name)
    assert len(deferred_after) == len(deferred_before), (
        f"Deferred queue length changed: {len(deferred_before)} → {len(deferred_after)}"
    )
    for i, (before_entry, after_entry) in enumerate(
        zip(deferred_before, deferred_after, strict=True)
    ):
        assert before_entry.snapshots == after_entry.snapshots, (
            f"Entry {i}: snapshots differ: {before_entry.snapshots} ≠ {after_entry.snapshots}"
        )
        assert before_entry.reason == after_entry.reason, (
            f"Entry {i}: reason changed: {before_entry.reason} ≠ {after_entry.reason}"
        )
        assert before_entry.disk == after_entry.disk, (
            f"Entry {i}: disk changed: {before_entry.disk} ≠ {after_entry.disk}"
        )

    # No blockcommit was executed (actions list is empty in dry-run)
    assert result.actions == [], f"Expected no executed actions, got: {result.actions}"


# ── Test 5: Full prediction has size estimate ──────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_dry_run_full_prediction_has_size_estimate(test_vm):
    """Dry-run on a VM with no backups yet → backup_full prediction with size > 0.

    Uses ``core.backup()`` (not ``core.run()``) with a real snapshot
    already recorded in state so that ``_estimate_chain_size()`` can
    query the actual file.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)

    # Create a real snapshot and record it in state
    snap_name = f"{vm_name}.full-pred-test"
    snap = _snapshot_create(shell, vm_name, snap_name, base_image, snapshot_dir)
    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)
    state.record_snapshot(vm_name, snap)

    # Assert no FULLs in state yet
    fulls_before = state.get_full_backups(str(target_dir))
    assert len(fulls_before) == 0, "Expected no FULLs before dry-run"

    # Dry-run backup only (no snapshot-create step, so no simulated snapshots).
    core.dry_run = True
    result = core.backup(vm_name)

    # Must have a backup_full prediction with size > 0
    full_preds = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_preds) >= 1, (
        f"Expected at least one backup_full prediction, got {len(full_preds)}. "
        f"All predictions: {[(p.action, p.name) for p in result.predictions]}"
    )
    for pred in full_preds:
        assert pred.size > 0, (
            f"Expected size > 0 for backup_full prediction '{pred.name}', got size={pred.size}"
        )

    _cleanup_checkpoints(shell, vm_name)


# ── Test 5b: First-run FULL prediction falls back to base_image ──────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dry_run_first_run_full_prediction_base_image_fallback(test_vm, caplog):
    """Fresh VM dry-run: FULL size estimate falls back to the base_image chain.

    1. Fresh ``test_vm`` with zero state snapshots — the FULL source is
       the *simulated* snapshot whose file does not exist on disk.
    2. Run ``core.run(vm_name)`` in dry-run mode.
    3. Assert at least one ``backup_full`` prediction with ``pred.size > 0``
       (derived from the ``base_image`` backing chain — design D3 fallback).
    4. Assert no ERROR/WARNING record mentions the simulated snapshot path
       or "Cannot estimate FULL size" — the missing-file probe is a
       ``check=True`` DEBUG probe (design D4), never an ERROR.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Zero state snapshots — the FULL source will be the simulated snapshot.
    core, vm_config, state = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)
    assert state.get_snapshots(vm_name) == [], "Test requires zero state snapshots"
    core.dry_run = True

    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.run(vm_name)

    # The simulated snapshot (FULL source) must not exist on disk.
    simulated_paths = [p.path for p in result.predictions if p.action == "snapshot_create"]
    assert simulated_paths, (
        f"Expected snapshot_create predictions (simulated FULL source), got none. "
        f"All predictions: {[(p.action, p.name) for p in result.predictions]}"
    )
    for sp in simulated_paths:
        assert not sp.exists(), f"Simulated snapshot path must not exist on disk: {sp}"

    # At least one backup_full prediction with a positive size estimate,
    # derived from the base_image chain via the fallback (design D3).
    full_preds = [p for p in result.predictions if p.action == "backup_full"]
    assert len(full_preds) >= 1, (
        f"Expected at least one backup_full prediction, got {len(full_preds)}. "
        f"All predictions: {[(p.action, p.name) for p in result.predictions]}"
    )
    for pred in full_preds:
        assert pred.size > 0, (
            f"Expected size > 0 for backup_full prediction '{pred.name}' "
            f"(base_image chain fallback), got size={pred.size}"
        )

    # No ERROR/WARNING from the size-estimation probe: the missing
    # simulated snapshot path must never surface as an ERROR/WARNING and
    # the "Cannot estimate FULL size" message must not be emitted.
    offending = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and (
            "Cannot estimate FULL size" in r.message
            or any(str(sp) in r.message for sp in simulated_paths)
        )
    ]
    assert offending == [], (
        "Size-estimation probe must not log ERROR/WARNING mentioning the "
        "simulated snapshot path or 'Cannot estimate FULL size'. Offending "
        f"records: {[(r.levelname, r.message) for r in offending]}"
    )


# ── Test 6: Incremental predictions approximate ────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_dry_run_incremental_predictions_approximate(test_vm):
    """After a real run creates a FULL, do another real snapshot, then
    dry-run: backup_transfer predictions exist with positive sizes.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    # Start VM
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)

    # Create S1 and run real pipeline to create a FULL backup
    s1_name = f"{vm_name}.incr-test-s1"
    s1 = _snapshot_create(shell, vm_name, s1_name, base_image, snapshot_dir)

    core1, vm_config1, state1 = _build_core(shell, vm_name, base_image, snapshot_dir, target_dir)
    state1.record_snapshot(vm_name, s1)
    core1.run(vm_name)

    # Verify a FULL was created
    full_files = sorted(target_dir.glob("*.FULL.*.qcow2"))
    assert len(full_files) >= 1, f"Expected at least one FULL backup file, got {len(full_files)}"

    # Corrected behavior: the recorded FULL name carries the ``.qcow2``
    # extension, so the dry-run's phantom filter sees the anchor and
    # predicts a delta — no re-recording needed.
    recorded_fulls = state1.get_full_backups(str(target_dir))
    assert recorded_fulls, "Expected at least one recorded FULL backup"
    assert recorded_fulls[0].name.endswith(".qcow2"), (
        f"Recorded FULL name must carry the .qcow2 extension, got {recorded_fulls[0].name}"
    )
    assert recorded_fulls[0].path.exists(), (
        f"Recorded FULL path must exist: {recorded_fulls[0].path}"
    )

    # Create S2 (a new snapshot that needs incremental transfer)
    time.sleep(1.1)
    s2_name = f"{vm_name}.incr-test-s2"
    s2 = _snapshot_create(shell, vm_name, s2_name, base_image, snapshot_dir)
    # Record S2 in the existing state manager used by core1
    state1.record_snapshot(vm_name, s2)

    # Dry-run backup on the same Core instance with existing state
    core1.dry_run = True
    result = core1.backup(vm_name)

    # Must have backup_transfer predictions with size > 0
    transfer_preds = [p for p in result.predictions if p.action == "backup_transfer"]
    assert len(transfer_preds) >= 1, (
        f"Expected at least one backup_transfer prediction, got {len(transfer_preds)}. "
        f"All predictions: {[(p.action, p.name) for p in result.predictions]}"
    )
    for pred in transfer_preds:
        assert isinstance(pred.size, int), (
            f"Expected int size on transfer prediction '{pred.name}', got {type(pred.size).__name__}"
        )
        assert pred.size > 0, (
            f"Expected size > 0 for transfer prediction '{pred.name}', got size={pred.size}"
        )

    _cleanup_checkpoints(shell, vm_name)


# ── Test 7: Shell calls are all read only ──────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dry_run_shell_calls_are_all_read_only(test_vm):
    """Capture all shell commands during dry-run and assert every one is read-only.

    Uses an allowlist approach: every recorded command must start with
    one of the ``_READ_ONLY_PREFIXES`` (``qemu-img info``, ``virsh
    domstate``, ``virsh dominfo``, ``virsh domblklist``, ``virsh dumpxml``,
    ``virsh checkpoint-list``, ``virsh --version``, ``test``, ``which``,
    ``find``, ``du``).  Any command that does not match fails the test;
    all offenders are collected and printed together for quick triage.
    """
    real_shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Wrap the real shell in a recording proxy
    recording = RecordingShell(real_shell)

    # Build a snapshot to populate state so the dry-run has something
    # to predict against (Full prediction needs a real file for chain-size
    # estimate, and we need to be able to assert the predictions are
    # non-empty).
    snap_name = f"{vm_name}.readonly-test"
    snap = _snapshot_create(real_shell, vm_name, snap_name, base_image, snapshot_dir)

    state = InMemoryStateManager()
    state.record_snapshot(vm_name, snap)

    core, vm_config, _ = _build_core(
        recording,  # inject the recording shell
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
    )
    # Rebuild core with our state
    core = Core(
        config=MockConfigFacade(
            global_config=GlobalConfig(state_dir="/var/tmp"),
            vms=[vm_config],
            config_path=target_dir / "test_dry_run.toml",
        ),
        factory=DefaultFactory(shell=recording, state=state),
        state=state,
        shell=recording,
    )
    core.dry_run = True

    # Use core.backup() so we get read-only backup predictions against
    # a real snapshot (no simulated snapshots that need mutation).
    result = core.backup(vm_name)

    # We must have predictions for the test to be meaningful
    assert len(result.predictions) > 0, (
        "Test requires >0 predictions to verify read-only commands; got empty predictions list"
    )

    # Check every recorded command
    commands = recording.commands
    assert len(commands) > 0, "No shell commands were recorded"

    bad_commands: list[list[str]] = []
    for cmd in commands:
        if not _is_read_only(cmd):
            bad_commands.append(cmd)

    if bad_commands:
        bad_strs = [" ".join(c) for c in bad_commands]
        pytest.fail(
            f"Commands outside read-only allowlist found during dry-run "
            f"({len(bad_commands)}):\n" + "\n".join(f"  - {c}" for c in bad_strs)
        )
