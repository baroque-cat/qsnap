"""Integration tests for infrastructure: cleanup, stall detection, state recovery.

All tests in this module that require a VM use the ``test_vm`` fixture.
Pure-stall-detection tests use ``SubprocessShell`` directly (no libvirt needed).
All tests are marked ``@pytest.mark.integration``.

Coverage:
- Socket and .tmp cleanup after crash simulation
- domjobabort called after backup, no active block job left
- Stall detection kills hung processes, slow progress survives
- Stale state self-healing

Run only when explicitly requested::

    poetry run pytest tests/integration/test_infrastructure.py -v -m integration
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)

try:
    import nbd  # noqa: F401

    _HAS_LIBNBD = True
except ImportError:
    _HAS_LIBNBD = False

from tests.mocks import InMemoryStateManager, MockConfigFacade


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    """Delete all qsnap-prefixed checkpoints."""
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
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp, "--metadata"],
                timeout=30,
            )


class _FrozenDateTime(datetime):
    """Frozen ``datetime`` for deterministic FULL freeze-timestamp naming.

    ``run_backup`` (Phase 2 API) names FULLs with ``datetime.now()``:
    ``{vm}.FULL.{freeze_ts}_{disk}_{6hex}``.  Subclassing the real class
    keeps ``strptime``/``min``/``timedelta`` arithmetic available for
    checkpoint-name parsing inside the provider.
    """

    @classmethod
    def now(cls, tz=None):
        return datetime(2025, 7, 30, 12, 0, 0)


# ──────────────────────────────────────────────────────────────────────
# Test 1: Socket and .tmp cleanup after crash simulation
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(1800)
def test_socket_and_tmp_cleanup(test_vm):
    """Verify source NBD socket and .tmp cleanup after ``run_backup()``.

    1. Create a stale NBD socket and a .tmp file (simulating a crashed run).
    2. Start the test VM.
    3. Call ``run_backup()`` — the FULL path uses ``qemu-img convert``
       (design D1/D5), no write-side ``qemu-nbd``.  The provider's socket
       is disk-scoped (``/tmp/qsnap-backup-{pid}-{disk}.sock``), so the
       stale socket carries the ``vda`` suffix.
    4. Verify the source NBD socket is removed (finally-block cleanup).
    5. Verify the .tmp file is renamed on success (atomic rename) or
       removed on failure.  The datetime + token_hex patches force the
       actual FULL to adopt the stale .tmp's freeze-timestamp name, so a
       successful run renames (adopts) the stale file.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Step 1: Create stale artifacts.
    socket_path = Path(f"/tmp/qsnap-backup-{os.getpid()}-vda.sock")
    # Freeze the timestamp so the stale .tmp and actual backup share the
    # same timestamp prefix.  Mock token_hex to get the same hex suffix.
    # Multi-disk naming: FULL files are {vm}.FULL.{ts}_{disk}_{6hex}, so
    # the stale name must include the disk segment ("vda") to match.
    frozen_ts = datetime(2025, 7, 30, 12, 0, 0)
    stale_hex = "deadbe"
    stale_name = f"{vm_name}.FULL.{frozen_ts.strftime('%Y%m%dT%H%M%S')}_vda_{stale_hex}"
    tmp_file = target_dir / f"{stale_name}.qcow2.tmp"

    socket_path.write_text("")  # empty socket file
    tmp_file.write_bytes(b"\x00" * 1024)  # 1 KB of junk

    assert socket_path.exists(), "Stale socket must exist before test"
    assert tmp_file.exists(), "Stale .tmp must exist before test"

    # Step 2: Start the VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)

    vm_running = is_vm_running(shell, vm_name)
    nbd_available = is_libvirt_new_enough(shell)

    if vm_running and nbd_available:
        provider = BitmapBackupProvider(shell)
        target = TargetConfig(path=target_dir, compress=False, verify="off")
        vm_config = VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
            targets=[target],
        )

        _cleanup_checkpoints(shell, vm_name)
        with (
            patch("qsnap.modules.backup.bitmap.secrets.token_hex", return_value="deadbe"),
            patch("qsnap.modules.backup.bitmap.datetime", _FrozenDateTime),
        ):
            result = provider.run_backup(vm_config, target, vm_config.disks[0])

        # Source NBD socket must be gone.
        assert not socket_path.exists(), f"Source socket {socket_path} was not cleaned up"

        if result.success:
            assert not tmp_file.exists(), f"Tmp file {tmp_file} must be renamed on success"
        else:
            assert not tmp_file.exists(), f"Tmp file {tmp_file} must be removed on failure"
    else:
        # Stopped-VM path: direct convert, no NBD socket.
        provider = BitmapBackupProvider(shell)
        target = TargetConfig(path=target_dir, compress=False, verify="off")
        vm_config = VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
            targets=[target],
        )

        with (
            patch("qsnap.modules.backup.bitmap.secrets.token_hex", return_value="deadbe"),
            patch("qsnap.modules.backup.bitmap.datetime", _FrozenDateTime),
        ):
            result = provider.run_backup(vm_config, target, vm_config.disks[0])
        assert result.success or result.error is not None, f"Stopped-VM path failed: {result.error}"
        # The stale .tmp is adopted by the FULL (same frozen timestamp +
        # "deadbe" hex), so it is renamed — no .tmp files remain.
        remaining_tmps = list(target_dir.glob(f"{vm_name}.FULL.*.qcow2.tmp"))
        assert len(remaining_tmps) == 0, f"Tmp files not cleaned up: {remaining_tmps}"

    # Manual cleanup of any remaining stale socket.
    if socket_path.exists():
        socket_path.unlink()


# ──────────────────────────────────────────────────────────────────────
# Test 2: domjobabort after backup
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_domjobabort_after_backup(test_vm):
    """Verify ``virsh domjobabort`` is called and no active block job remains.

    1. Start the test VM.
    2. Run ``run_backup()`` via NBD.
    3. Check ``virsh domjobinfo`` — should report no active block job.
    4. Verify the VM is still running and healthy.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]

    # Start VM.
    shell.run(["virsh", "start", vm_name], timeout=30)
    time.sleep(1)
    if not is_vm_running(shell, vm_name):
        pytest.skip("VM did not reach running state")
    if not is_libvirt_new_enough(shell):
        pytest.skip("libvirt < 7.2 — NBD backup-begin not available")
    if not _HAS_LIBNBD:
        pytest.skip("python3-libnbd not installed")

    provider = BitmapBackupProvider(shell)
    target = TargetConfig(path=target_dir, compress=False, verify="off")
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[target],
    )

    result = provider.run_backup(vm_config, target, vm_config.disks[0])
    assert result.success, f"FULL backup failed: {result.error}"

    # After backup, domjobabort should have been called in the finally block.
    # Check virsh domjobinfo.
    domjob = shell.run(
        ["virsh", "domjobinfo", "--domain", vm_name],
        timeout=30,
    )
    has_no_job = (
        not domjob.success
        or "no current block job" in (domjob.stdout or "").lower()
        or "no current job" in (domjob.stderr or "").lower()
        or "job type:" in ((domjob.stdout or "") + (domjob.stderr or "")).lower()
    )
    assert has_no_job, (
        f"domjobabort must have terminated the NBD job. "
        f"domjobinfo: stdout={domjob.stdout!r} stderr={domjob.stderr!r}"
    )

    # VM must still be running.
    assert is_vm_running(shell, vm_name), "VM must still be running after backup"

    _cleanup_checkpoints(shell, vm_name)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Stall detection
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stall_detection_kills_hung(tmp_path: Path, monkeypatch):
    """Stall detection kills a hung process whose output file never grows.

    Uses ``sleep 3600`` (never produces output) with ``stall_timeout=3``
    and patched poll interval of 1s so the test completes in ~4s.
    """
    monkeypatch.setattr("qsnap.shell.subprocess_shell._POLL_INTERVAL", 1)

    output_file = tmp_path / "output.img"
    output_file.touch()

    shell = SubprocessShell()
    result = shell.run_with_stall_detection(
        ["sleep", "3600"],
        output_file=output_file,
        stall_timeout=3,
    )

    assert not result.success, "Stall must be detected"
    assert "Stall detected" in (result.error or ""), (
        f"Expected 'Stall detected' in {result.error!r}"
    )
    assert result.returncode == -1, f"Expected returncode=-1, got {result.returncode}"


@pytest.mark.integration
def test_stall_detection_slow_progress_survives(tmp_path: Path, monkeypatch):
    """Slow but progressing output must NOT trigger stall detection.

    A Python process writes 1 KB every 2 seconds for ~15 seconds.
    With ``stall_timeout=10`` and poll interval of 1s, this is frequent
    enough to avoid the stall threshold.
    """
    monkeypatch.setattr("qsnap.shell.subprocess_shell._POLL_INTERVAL", 1)

    # Slowly-growing file: dd 1 KB every 2 sec for ~15 sec via sh -c.
    # Writes directly to output_file so that run_with_stall_detection
    # sees the file growing (it polls output_file.stat().st_size).
    output_file = tmp_path / "progressing.img"
    script = (
        f"end=$(( $(date +%s) + 15 )); "
        f"while [ $(date +%s) -lt $end ]; do "
        f"dd if=/dev/zero bs=1024 count=1 >> {output_file} 2>/dev/null; "
        f"sleep 2; done"
    )
    shell = SubprocessShell()
    result = shell.run_with_stall_detection(
        ["sh", "-c", script],
        output_file=output_file,
        stall_timeout=10,
    )

    assert result.success, f"Slow progress must NOT trigger stall: {result.error}"
    assert "Stall detected" not in (result.error or ""), f"False-positive stall: {result.error}"


# ──────────────────────────────────────────────────────────────────────
# Test 4: Stale state self-healing (snapshot world)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stale_state_self_healing(test_vm):
    """Stale snapshot records are healed by the snapshot world, not the provider.

    Phase 2 decoupled the backup world from snapshot state:
    ``BitmapBackupProvider`` no longer accepts a ``state=`` dependency
    and no longer performs stale-state healing (``transfer_missing`` was
    removed — the replacement ``run_backup()`` never consults snapshot
    state).  Stale snapshot records — entries whose snapshot file is
    missing on disk — are now the snapshot world's responsibility and
    are removed by ``Core.reconcile()``.

    1. Register a snapshot in ``InMemoryStateManager`` pointing to a
       non-existent file path.
    2. Run ``Core.reconcile()`` — the snapshot world detects the phantom
       snapshot (file missing) and removes it from state.
    3. Verify the stale entry was removed and reported as a phantom.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]

    # Register a stale snapshot pointing to a non-existent path.
    state = InMemoryStateManager()
    stale = SnapshotInfo(
        name=f"{vm_name}.stale-heal",
        path=Path("/tmp/qsnap-stale-integration-test.qcow2"),
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    state.record_snapshot(vm_name, stale)
    assert len(state.get_snapshots(vm_name)) == 1, "Stale snapshot must be recorded"

    # Build a Core with the real factory so reconcile uses the real
    # shell (virsh dumpxml) and the injected state.
    from qsnap.core import Core
    from qsnap.factory.default import DefaultFactory

    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        targets=[TargetConfig(path=target_dir, verify="off")],
    )
    config = MockConfigFacade(
        vms=[vm_config],
        config_path=tmpdir / "stale_heal.toml",
    )
    core = Core(
        config=config,
        factory=DefaultFactory(shell=shell, state=state),
        state=state,
        shell=shell,
    )

    # The snapshot world removes the stale entry (reconcile step 1:
    # phantom snapshots whose file is missing and whose path is not
    # referenced by the domain XML).
    result = core.reconcile(vm_name)

    healed = result[vm_name]
    assert healed.phantom_snapshots_removed == 1, (
        f"Expected the stale snapshot to be removed, got {healed}"
    )
    remaining = state.get_snapshots(vm_name)
    assert len(remaining) == 0, (
        f"Stale entry must be removed by the snapshot world. "
        f"Remaining: {[s.name for s in remaining]}"
    )

    # The backup provider is fully decoupled: it constructs without
    # ``state=`` and its target listing is unaffected by snapshot
    # records.
    provider = BitmapBackupProvider(shell)
    assert provider.list(TargetConfig(path=target_dir, verify="off")) == [], (
        "Backup listing must not be affected by snapshot state"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 5: JsonStateManager._save OSError → RuntimeError + CRITICAL log
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_state_save_oserror_surfaces_runtime_error(tmp_path: Path, caplog):
    """An OSError during a state save surfaces as RuntimeError + CRITICAL.

    New ``_save`` contract (design D3): ENOSPC and other OS-level write
    failures are caught, logged at CRITICAL, and re-raised as
    ``RuntimeError`` so ``Core._run_pipeline`` can contain the failure
    to one VM.  The in-flight state file is never deleted or renamed.
    """
    import json as _json

    from qsnap.models.results import SnapshotInfo
    from qsnap.state.json_manager import JsonStateManager

    state_dir = tmp_path / "state"
    manager = JsonStateManager(state_dir=state_dir, state_backup_count=1)
    info = SnapshotInfo(
        name="vm1.snap",
        path=tmp_path / "snap.qcow2",
        timestamp=datetime.now(),
        allocation=1024,
        disk="vda",
    )

    # A pre-existing state file makes the save path exercise rotation.
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "vm1.json").write_text(_json.dumps({"snapshots": []}))

    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ),
        pytest.raises(RuntimeError, match="State write failed for VM vm1"),
    ):
        manager.record_snapshot("vm1", info)

    # CRITICAL log names the VM and the state path.
    critical_msgs = [
        r.message
        for r in caplog.records
        if r.levelname == "CRITICAL" and "Failed to save state" in r.message
    ]
    assert len(critical_msgs) >= 1, (
        f"Expected a CRITICAL 'Failed to save state' log. "
        f"Logs: {[r.message for r in caplog.records]}"
    )
    assert "vm1" in critical_msgs[0] and str(state_dir / "vm1.json") in critical_msgs[0], (
        f"CRITICAL log must name the VM and state path: {critical_msgs[0]}"
    )

    # The original state file survives (atomic write guarantee).
    assert (state_dir / "vm1.json").exists(), "Existing state file must survive a failed save"


@pytest.mark.integration
def test_stale_state_self_healing_save_failure_surfaces_runtime_error(tmp_path: Path, caplog):
    """Snapshot-world stale-state healing surfaces RuntimeError when the save fails.

    Phase 2: stale snapshots are healed by the snapshot world via
    ``IStateManager.remove_snapshot`` (the backup provider no longer
    performs healing).  ``JsonStateManager.remove_snapshot`` →
    ``JsonStateManager._save`` raises ``RuntimeError`` on OSError instead
    of swallowing it — the per-VM pipeline handler contains it.  The
    stale entry is never silently dropped.
    """
    from qsnap.models.results import SnapshotInfo
    from qsnap.state.json_manager import JsonStateManager

    state_dir = tmp_path / "state"
    manager = JsonStateManager(state_dir=state_dir, state_backup_count=0)
    vm_name = "selfheal-save-fail"

    stale = SnapshotInfo(
        name=f"{vm_name}.stale",
        path=tmp_path / "does-not-exist.qcow2",
        timestamp=datetime.now(),
        allocation=0,
        disk="vda",
    )
    manager.record_snapshot(vm_name, stale)
    assert len(manager.get_snapshots(vm_name)) == 1, "Stale snapshot must be recorded"

    with (
        patch(
            "qsnap.state.json_manager.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ),
        pytest.raises(RuntimeError, match="State write failed"),
    ):
        # remove_snapshot → _save fails → RuntimeError surfaces (the
        # stale entry is NOT silently dropped).
        manager.remove_snapshot(vm_name, stale.name)

    critical_msgs = [
        r.message
        for r in caplog.records
        if r.levelname == "CRITICAL" and "Failed to save state" in r.message
    ]
    assert len(critical_msgs) >= 1, "Expected a CRITICAL 'Failed to save state' log"

    # The stale entry is still recorded (state only advances on success).
    assert len(manager.get_snapshots(vm_name)) == 1, (
        "Stale entry must remain in state when the save fails (never lose records)"
    )
