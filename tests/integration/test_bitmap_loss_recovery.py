"""Integration tests for bitmap-loss recovery on a real libvirt VM.

All tests in this module require a running libvirt daemon and are marked
``@pytest.mark.integration``.  They use the ``test_vm`` fixture from
``conftest.py`` (disposable 256M qcow2 VM, ``SubprocessShell``).

The tests manufacture a REAL dead checkpoint on the disposable VM using
mechanism (c) from test-plan §5.2: run a normal FULL backup so a
checkpoint + dirty bitmap exist, ``virsh destroy`` the VM, remove the
bitmap directly from the active qcow2 layer with ``qemu-img bitmap
--remove``, then restart the VM.  This deterministically yields
checkpoint-metadata-present + bitmap-absent — exactly the incident
state (bitmap lost after an unclean host shutdown).

Recovery semantics under test (recover-lost-checkpoint-bitmaps):

- A DEAD probe routes ``run_backup`` into recovery: the provider
  falls back to a FULL export (kind ``"full"``; a recovered delta
  would report ``"recovered_delta"``) and deletes the dead checkpoint
  only after the new FULL passes verification.
- A follow-up ``run_backup`` on the still-running VM is a clean delta
  with NO recovery WARNING (acceptance criterion).
- The reactive backstop (design D9): when the probe returns UNKNOWN
  and ``backup-begin`` fails with "checkpoint inconsistent", exactly
  that checkpoint is deleted and the run retries once as a FULL.
- Dry-run on a dead-checkpoint system predicts the recovery outcome
  (``FULL (recovery)``, gate reason) without mutating anything.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_bitmap_loss_recovery.py -v -m integration
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from qsnap.core import BackupAbortError, Core
from qsnap.factory.default import DefaultFactory
from qsnap.interfaces.shell import IShell
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import BackupResult, ShellResult
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from qsnap.utils.nbd_client import LibnbdClient
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

# ── helpers ──────────────────────────────────────────────────────────


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints (full delete, not --metadata)."""
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


def _cleanup_snapshots(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all external snapshots for *vm_name* (--metadata only)."""
    result = shell.run(
        ["virsh", "snapshot-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        snap = line.strip()
        if snap:
            shell.run(
                ["virsh", "snapshot-delete", "--domain", vm_name, snap, "--metadata"],
                timeout=30,
            )


def _get_qsnap_checkpoints(shell: SubprocessShell, vm_name: str) -> list[str]:
    """Return the qsnap-prefixed checkpoints of *vm_name* (newest last)."""
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return []
    return [
        cp.strip() for cp in (result.stdout or "").splitlines() if cp.strip().startswith("qsnap-")
    ]


def _start_vm(shell: SubprocessShell, vm_name: str) -> bool:
    """Start *vm_name* and wait briefly.  Returns True when running."""
    shell.run(["virsh", "start", vm_name], timeout=60)
    time.sleep(2)
    return is_vm_running(shell, vm_name)


class _FailingQmpProbeShell(IShell):
    """IShell wrapper that fails every ``qemu-monitor-command`` call.

    Delegates everything else to the wrapped ``SubprocessShell``.  Used
    to force the bitmap health probe to return UNKNOWN (design D2) so
    the reactive backstop (design D9) is exercised against the real
    libvirt: ``backup-begin`` then fails with "checkpoint inconsistent:
    missing or broken bitmap", the checkpoint is deleted, and the run
    retries once as a FULL.
    """

    def __init__(self, delegate: SubprocessShell) -> None:
        self._delegate = delegate

    def run(self, cmd: list[str], timeout: int, check: bool = False) -> ShellResult:
        if "qemu-monitor-command" in cmd:
            return ShellResult(
                success=False,
                stdout="",
                stderr="QMP command failed",
                returncode=1,
                error="QMP command failed",
            )
        return self._delegate.run(cmd, timeout, check)

    def run_with_stall_detection(
        self,
        cmd: list[str],
        output_file: Path | None = None,
        stall_timeout: int = 1800,
        check: bool = False,
    ) -> ShellResult:
        return self._delegate.run_with_stall_detection(cmd, output_file, stall_timeout, check)

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat: Callable[[int], None],
        check: bool = False,
    ) -> ShellResult:
        return self._delegate.run_with_heartbeat(
            cmd, timeout, heartbeat_seconds, on_heartbeat, check
        )


class _RecordingShell(IShell):
    """IShell wrapper that delegates to SubprocessShell and records commands.

    Used to assert the dry-run zero-mutation invariant: no mutating
    ``virsh`` command (backup-begin, checkpoint-delete/create,
    domjobabort) may appear in the recorded command list.
    """

    def __init__(self, delegate: SubprocessShell) -> None:
        self._delegate = delegate
        self._commands: list[list[str]] = []

    @property
    def commands(self) -> list[list[str]]:
        """The recorded command lists, in execution order."""
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

    def run_with_heartbeat(
        self,
        cmd: list[str],
        timeout: int,
        heartbeat_seconds: int,
        on_heartbeat: Callable[[int], None],
        check: bool = False,
    ) -> ShellResult:
        self._commands.append(list(cmd))
        return self._delegate.run_with_heartbeat(
            cmd, timeout, heartbeat_seconds, on_heartbeat, check
        )


def _make_vm_config(vm_name: str, base_image: Path, snapshot_dir: Path) -> VMConfig:
    """Build the minimal VMConfig used by the provider-level tests."""
    return VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
    )


def _manufacture_dead_checkpoint(
    shell: SubprocessShell,
    vm_config: VMConfig,
    target: TargetConfig,
) -> tuple[BitmapBackupProvider, BackupResult, str]:
    """Create a REAL dead checkpoint via mechanism (c) from test-plan §5.2.

    1. ``run_backup`` (FULL, no checkpoint yet) creates a checkpoint
       whose dirty bitmap tracks the export's freeze point.
    2. ``virsh destroy`` the VM (unclean teardown — the in-use flag of
       the persistent bitmap is never cleared).
    3. ``qemu-img bitmap --remove <active-layer> <checkpoint-name>`` —
       removes the checkpoint-named bitmap directly from the qcow2.
    4. ``virsh start`` — libvirt checkpoint metadata survives while the
       bitmap is gone; the next probe reports DEAD.

    Returns ``(provider, full_result, checkpoint_name)``.  Callers must
    verify the probe reports DEAD afterwards and ``pytest.skip`` when a
    libvirt version behaves differently (checkpoint not preserved).
    """
    provider = BitmapBackupProvider(shell, nbd=LibnbdClient())
    full_result = provider.run_backup(vm_config, target, vm_config.disks[0])
    assert full_result.success, f"FULL backup failed: {full_result.error}"
    assert full_result.kind == "full", (
        f"First backup with no checkpoint must be a FULL, got {full_result.kind!r}"
    )
    cp_name = full_result.checkpoint
    assert cp_name is not None, "FULL backup must create a checkpoint"
    assert cp_name in _get_qsnap_checkpoints(shell, vm_config.name), (
        f"Checkpoint {cp_name!r} must be listed after the FULL backup"
    )

    # Mechanism (c): stop the VM, remove the bitmap, restart.
    shell.run(["virsh", "destroy", vm_config.name], timeout=30)
    time.sleep(1)
    shell.run(
        ["qemu-img", "bitmap", "--remove", str(vm_config.disks[0].base_image), cp_name], timeout=60
    )
    if not _start_vm(shell, vm_config.name):
        pytest.skip("VM did not reach running state after dead-checkpoint manufacture")
    return provider, full_result, cp_name


def _seed_full_state(
    state: InMemoryStateManager,
    target_dir: Path,
    full_result: BackupResult,
    disk: str = "vda",
) -> None:
    """Record a FULL backup file in state (as Core would after a run)."""
    state.record_full_backup(
        str(target_dir),
        f"{full_result.snapshot_name}.qcow2",
        datetime.now(),
        disk=disk,
    )


# ──────────────────────────────────────────────────────────────────────
# Test 1: REAL dead checkpoint → recovery heals → clean delta next run
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_real_dead_checkpoint_recovered_delta_heals(test_vm, caplog):
    """A real dead checkpoint is recovered and the next run is a clean delta.

    Acceptance criterion (test-plan §5.2): probe returns DEAD on the
    real VM; recovery produces ``kind == "recovered_delta"`` or a FULL
    fallback; exit 0; the dead checkpoint is gone; the next run is a
    clean delta with NO recovery WARNING.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed — required for delta transfer")

    if not _start_vm(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    vm_config = _make_vm_config(vm_name, base_image, snapshot_dir)
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    # ── Step 1: FULL backup → checkpoint + bitmap ────────────────────
    provider, full_result, cp_name = _manufacture_dead_checkpoint(shell, vm_config, target)
    full_path = full_result.target_path
    assert full_path.exists(), f"FULL backup file missing: {full_path}"

    # ── Step 2: verify the incident state — checkpoint present, DEAD ──
    assert cp_name in _get_qsnap_checkpoints(shell, vm_name), (
        f"Checkpoint metadata must survive the restart: {_get_qsnap_checkpoints(shell, vm_name)}"
    )
    probe = provider._probe_checkpoint_bitmap(vm_name, cp_name, "vda", True, base_image)
    if probe != "dead":
        pytest.skip(
            f"Mechanism (c) did not produce a DEAD probe on this libvirt "
            f"(got {probe!r}) — bitmap survived the restart"
        )

    # ── Step 3: recovery run — heals the dead checkpoint ─────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        recovery = provider.run_backup(vm_config, target, vm_config.disks[0])
    assert recovery.success, f"Recovery backup failed: {recovery.error}"
    assert recovery.kind in ("full", "recovered_delta"), (
        f"Recovery must report kind 'full' or 'recovered_delta', got {recovery.kind!r}"
    )
    dead_warnings = [r.message for r in caplog.records if "bitmap is DEAD" in r.message]
    assert len(dead_warnings) >= 1, (
        f"Recovery must log the DEAD-bitmap WARNING. Logs: {[r.message for r in caplog.records]}"
    )
    # The dead checkpoint is deleted only AFTER the new backup passes
    # verification (design D8) — it must be gone now.
    assert cp_name not in _get_qsnap_checkpoints(shell, vm_name), (
        f"Dead checkpoint {cp_name!r} must be deleted after successful recovery"
    )
    assert recovery.target_path.exists(), f"Recovery backup file missing: {recovery.target_path}"

    # ── Step 4: follow-up run — clean delta, no recovery WARNING ─────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        delta = provider.run_backup(vm_config, target, vm_config.disks[0])
    assert delta.success, f"Follow-up delta failed: {delta.error}"
    assert delta.kind == "delta", (
        f"Follow-up run after recovery must be a plain delta, got {delta.kind!r}"
    )
    assert not any("bitmap is DEAD" in r.message for r in caplog.records), (
        f"Follow-up delta must not re-enter recovery. Logs: {[r.message for r in caplog.records]}"
    )

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Recovery FULL fallback retires the superseded generation
# ──────────────────────────────────────────────────────────────────────


def _build_retention_core(
    shell: SubprocessShell,
    vm_config: VMConfig,
    target: TargetConfig,
    state: InMemoryStateManager,
    tmpdir: Path,
) -> tuple[Core, VMConfig]:
    """Build a Core whose target keep_generations=1 would retire the old chain.

    Returns ``(core, vm_config_with_target)`` — the rebuilt VM config
    carries the retention-tuned target, ready for ``_backup_target`` /
    ``_evaluate_backup_retention`` calls.
    """
    from qsnap.models.config import GlobalConfig

    vm_config_with_target = VMConfig(
        name=vm_config.name,
        disks=vm_config.disks,
        snapshot_dir=vm_config.snapshot_dir,
        snapshot_chain_length=999,  # prevent blockcommit from interfering
        targets=[
            TargetConfig(
                path=target.path,
                compress=False,
                verify="off",
                target_keep_generations=1,
                backup_retry_max=0,
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(
            state_dir="/var/tmp",
            full_verify_before_delete="check",
        ),
        vms=[vm_config_with_target],
        config_path=tmpdir / "recovery_retire.toml",
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config_with_target


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_recovery_full_fallback_retires_generation_real(test_vm, caplog):
    """Recovery FULL passes M1/M2; the superseded generation is retired.

    1. Create FULL#1 (old generation) + checkpoint; record in state.
    2. Manufacture a dead checkpoint (mechanism c).
    3. Recovery ``run_backup`` produces a FULL (kind "full") and deletes
       the dead checkpoint.
    4. Per-chain retention with keep_generations=1 retires the old
       generation in the same run — after the verify-before-delete gate
       (M1 metadata + M2 check) passes on the surviving FULLs.
    5. The next run is a clean delta.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    if not _start_vm(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    vm_config = _make_vm_config(vm_name, base_image, snapshot_dir)
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    # ── Steps 1-2: old generation + dead checkpoint ──────────────────
    provider, full1_result, cp1 = _manufacture_dead_checkpoint(shell, vm_config, target)
    full1_path = full1_result.target_path
    assert full1_path.exists(), f"Old generation FULL missing: {full1_path}"

    probe = provider._probe_checkpoint_bitmap(vm_name, cp1, "vda", True, base_image)
    if probe != "dead":
        pytest.skip(f"Mechanism (c) did not produce a DEAD probe (got {probe!r})")

    # ── Step 3: recovery run (provider level — real recovery path) ───
    caplog.clear()
    with caplog.at_level(logging.INFO):
        recovery = provider.run_backup(vm_config, target, vm_config.disks[0])
    assert recovery.success, f"Recovery FULL failed: {recovery.error}"
    assert recovery.kind == "full", (
        f"Dead-bitmap recovery must fall back to a FULL in this run, got {recovery.kind!r}"
    )
    assert any("bitmap is DEAD" in r.message for r in caplog.records), (
        "Recovery must log the DEAD-bitmap WARNING"
    )
    assert cp1 not in _get_qsnap_checkpoints(shell, vm_name), (
        f"Dead checkpoint {cp1!r} must be deleted after the recovery FULL"
    )
    new_full_path = recovery.target_path
    assert new_full_path.exists() and new_full_path != full1_path

    # ── Step 4: retention retires the superseded generation ──────────
    state = InMemoryStateManager()
    _seed_full_state(state, target_dir, full1_result)
    _seed_full_state(state, target_dir, recovery)
    core, vm_config_with_target = _build_retention_core(shell, vm_config, target, state, tmpdir)
    backup_target = vm_config_with_target.targets[0]

    backups, retention_result = core._evaluate_backup_retention(
        vm_config_with_target, backup_target
    )
    assert retention_result is not None
    assert full1_result.snapshot_name in retention_result.remove, (
        f"Old generation must be a deletion candidate under keep_generations=1. "
        f"remove={retention_result.remove}"
    )
    assert recovery.snapshot_name in retention_result.keep, (
        f"New recovery FULL must be kept. keep={retention_result.keep}"
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        core._cleanup_backups(vm_config_with_target, backup_target, backups, retention_result)
    assert not full1_path.exists(), (
        f"Old generation {full1_path.name} must be retired after the "
        f"recovery FULL passes the verify-before-delete gate"
    )
    assert new_full_path.exists(), "New recovery FULL must survive cleanup"

    # ── Step 5: next run is a clean delta ────────────────────────────
    caplog.clear()
    with caplog.at_level(logging.INFO):
        delta = provider.run_backup(vm_config, target, vm_config.disks[0])
    assert delta.success, f"Follow-up delta failed: {delta.error}"
    assert delta.kind == "delta", f"Follow-up run must be a delta, got {delta.kind!r}"

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Failed verification of the recovery FULL preserves everything
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_recovery_full_failed_verification_preserves_old_generation_real(test_vm, caplog):
    """A corrupt recovery FULL aborts; the old generation and dead checkpoint remain.

    Mechanism (c) + simulated corruption of the new FULL transfer: the
    recovery FULL's verification is forced to fail (as if the new file
    were corrupt).  The run must report failure via the abort path
    (``BackupAbortError``), the dead checkpoint must NOT be deleted
    (deletion only happens after a verified FULL), and the old
    generation must be preserved (verify-before-delete gate holds).
    """
    from unittest.mock import patch

    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    if not _start_vm(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    vm_config = _make_vm_config(vm_name, base_image, snapshot_dir)
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    # ── Old generation + dead checkpoint ─────────────────────────────
    provider, full1_result, cp1 = _manufacture_dead_checkpoint(shell, vm_config, target)
    full1_path = full1_result.target_path
    assert full1_path.exists()

    probe = provider._probe_checkpoint_bitmap(vm_name, cp1, "vda", True, base_image)
    if probe != "dead":
        pytest.skip(f"Mechanism (c) did not produce a DEAD probe (got {probe!r})")

    state = InMemoryStateManager()
    _seed_full_state(state, target_dir, full1_result)

    # Drive the recovery through Core._backup_target so the abort path
    # (BackupAbortError → backup_failed → exit 10) is exercised.  The
    # dead checkpoint is probed INSIDE run_backup (startup validation
    # is not part of _backup_target), so the recovery FULL is attempted.
    # ``_force_full_targets`` is set so Core treats the run as a FULL
    # attempt — the CRITICAL "old generations preserved" abort path is
    # only reachable when the failed backup is classified as a FULL.
    core, vm_config_with_target = _build_retention_core(shell, vm_config, target, state, tmpdir)
    core._force_full_targets.add(str(target_dir))
    backup_target = vm_config_with_target.targets[0]

    caplog.clear()
    with (
        caplog.at_level(logging.CRITICAL),
        patch(
            "qsnap.modules.backup.bitmap.verify_full_backup",
            return_value="verification failed: image is corrupt (simulated M1 failure)",
        ),
        pytest.raises(BackupAbortError),
    ):
        core._backup_target(vm_config_with_target, backup_target)

    # ── Old generation preserved (verify-before-delete gate holds) ───
    assert full1_path.exists(), (
        f"Old generation must NOT be deleted when the recovery FULL fails: {full1_path}"
    )

    # ── Dead checkpoint preserved — recovery never verified, so the
    #    post-verification deletion (design D8) did not run ───────────
    assert cp1 in _get_qsnap_checkpoints(shell, vm_name), (
        f"Dead checkpoint {cp1!r} must remain when the recovery FULL fails "
        f"(deletion only occurs after verification). Got: "
        f"{_get_qsnap_checkpoints(shell, vm_name)}"
    )

    # ── No new FULL file left behind (partial cleaned up) ────────────
    new_full_files = [p for p in target_dir.glob("*.FULL.*.qcow2") if p.name != full1_path.name]
    assert new_full_files == [], (
        f"No new FULL may remain after failed verification: {[p.name for p in new_full_files]}"
    )

    # ── Abort path logged ────────────────────────────────────────────
    all_logs = " ".join(r.message for r in caplog.records)
    assert "old generations preserved" in all_logs, (
        f"Abort must log that old generations are preserved: {all_logs[:500]}"
    )

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Reactive backstop heals when the probe returns UNKNOWN
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_backstop_heals_when_probe_unknown_real(test_vm, caplog):
    """UNKNOWN probe → "checkpoint inconsistent" → backstop deletes and retries.

    The wrapped shell fails the QMP ``query-named-block-nodes`` probe
    (UNKNOWN, design D2), so ``run_backup`` attempts a delta.  libvirt's
    ``backup-begin`` then fails with "checkpoint inconsistent: missing
    or broken bitmap" because the bitmap is really gone; the reactive
    backstop (design D9) deletes exactly that checkpoint and retries
    once — as a FULL — healing the chain in a single run.
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

    if not _start_vm(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    vm_config = _make_vm_config(vm_name, base_image, snapshot_dir)
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    # ── Dead checkpoint (mechanism c) ────────────────────────────────
    provider, _, cp_name = _manufacture_dead_checkpoint(shell, vm_config, target)
    probe = provider._probe_checkpoint_bitmap(vm_name, cp_name, "vda", True, base_image)
    if probe != "dead":
        pytest.skip(f"Mechanism (c) did not produce a DEAD probe (got {probe!r})")

    # ── UNKNOWN probe via the wrapped shell → backstop path ──────────
    wrapped = _FailingQmpProbeShell(shell)
    provider_unknown = BitmapBackupProvider(wrapped, nbd=LibnbdClient())

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = provider_unknown.run_backup(vm_config, target, vm_config.disks[0])
    assert result.success, f"Backstop recovery must succeed: {result.error}"
    assert result.kind == "full", (
        f"Backstop retry must produce a FULL (checkpoint was deleted), got {result.kind!r}"
    )

    # The backstop WARNING names the checkpoint-inconsistent failure.
    backstop_logs = [r.message for r in caplog.records if "checkpoint inconsistent" in r.message]
    assert len(backstop_logs) >= 1, (
        f"Backstop must log the checkpoint-inconsistent failure. Logs: "
        f"{[r.message for r in caplog.records]}"
    )

    # Exactly the dead checkpoint was deleted; a successor + FULL exist.
    assert cp_name not in _get_qsnap_checkpoints(shell, vm_name), (
        f"Backstop must delete the inconsistent checkpoint {cp_name!r}"
    )
    assert len(_get_qsnap_checkpoints(shell, vm_name)) >= 1, (
        "A successor checkpoint must exist after the backstop retry"
    )
    assert result.target_path.exists(), f"Backstop FULL file missing: {result.target_path}"

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 5: Dry-run on a dead-checkpoint system predicts, mutates nothing
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_dry_run_on_dead_checkpoint_predicts_recovery_no_mutation(test_vm, caplog):
    """Dry-run on a dead checkpoint predicts recovery with zero mutations.

    The dry-run consumes the read-only baseline assessment
    (``assess_baseline``): the dead bitmap yields a
    ``FULL (recovery)`` prediction with the failed gate reason.  The
    libvirt checkpoint set must be identical before/after, no target
    file may appear, state must be byte-identical, and no mutating
    command (``backup-begin``, ``checkpoint-delete``,
    ``checkpoint-create``, ``domjobabort``) may be issued.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    if not _start_vm(shell, vm_name):
        pytest.skip("VM did not reach running state")

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)

    vm_config = _make_vm_config(vm_name, base_image, snapshot_dir)
    target = TargetConfig(path=target_dir, compress=False, verify="off")

    # ── Dead checkpoint + seeded FULL state (so needs_full=False and
    #    the prediction flows through the recovery assessment) ────────
    provider, full1_result, cp_name = _manufacture_dead_checkpoint(shell, vm_config, target)
    probe = provider._probe_checkpoint_bitmap(vm_name, cp_name, "vda", True, base_image)
    if probe != "dead":
        pytest.skip(f"Mechanism (c) did not produce a DEAD probe (got {probe!r})")

    state = InMemoryStateManager()
    _seed_full_state(state, target_dir, full1_result)

    rec_shell = _RecordingShell(shell)
    vm_config_with_target = VMConfig(
        name=vm_name,
        disks=vm_config.disks,
        snapshot_dir=snapshot_dir,
        targets=[target],
    )
    config = MockConfigFacade(
        vms=[vm_config_with_target],
        config_path=tmpdir / "dry_recovery.toml",
    )
    core = Core(
        config=config,
        factory=DefaultFactory(shell=rec_shell, state=state),
        state=state,
        shell=rec_shell,
    )
    core.dry_run = True

    checkpoints_before = _get_qsnap_checkpoints(shell, vm_name)
    target_files_before = sorted(p.name for p in target_dir.glob("*.qcow2"))
    state_fulls_before = state.get_full_backups(str(target_dir))

    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = core.backup(vm_name)

    # ── Prediction names the recovery outcome with the gate reason ───
    assert result.success, "Dry-run must report success"
    assert len(result.predictions) > 0, "Dry-run must emit predictions"
    assert result.actions == [], "Dry-run must not record executed mutations"
    pred_logs = [r.message for r in caplog.records if "[dry-run] Would create" in r.message]
    assert any("FULL (recovery)" in m for m in pred_logs), (
        f"Dead checkpoint must be predicted as a recovery FULL. Logs: {pred_logs}"
    )
    assert any("recovery gate failed" in m and "G1" in m for m in pred_logs), (
        f"Prediction must name the failed recovery gate. Logs: {pred_logs}"
    )

    # ── Zero-mutation invariants ─────────────────────────────────────
    assert _get_qsnap_checkpoints(shell, vm_name) == checkpoints_before, (
        "Dry-run must not change the libvirt checkpoint set"
    )
    assert sorted(p.name for p in target_dir.glob("*.qcow2")) == target_files_before, (
        "Dry-run must not create or delete target files"
    )
    assert state.get_full_backups(str(target_dir)) == state_fulls_before, (
        "Dry-run must not modify FULL state"
    )
    assert state.get_snapshots(vm_name) == [], "Dry-run must not record snapshots"
    assert state.get_deferred_operations(vm_name) == [], "Dry-run must not record deferred ops"

    # ── No mutating shell commands may be issued ─────────────────────
    mutating_subcommands = ("backup-begin", "checkpoint-delete", "checkpoint-create", "domjobabort")
    offenders = [
        " ".join(cmd)
        for cmd in rec_shell.commands
        if any(sub in cmd for sub in mutating_subcommands)
    ]
    assert offenders == [], (
        f"Dry-run must only issue read-only commands, found mutating: {offenders}"
    )

    _cleanup_checkpoints(shell, vm_name)
    _cleanup_snapshots(shell, vm_name)
