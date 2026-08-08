"""E2E tests: full pipeline from a TOML config file (default 48 floor).

``test_full_pipeline_from_config`` runs the complete qsnap pipeline twice
via the real CLI, driven by a TOML config file with NO explicit
``snapshot_preserve_min`` (so the facade resolves the global default 48),
verifying:

- 2 snapshot runs produce exactly 2 snapshot files
- NO blockcommit occurs under the 48-snapshot floor (chain_length
  default 24 would otherwise remove snapshots)
- ``qsnap check`` passes on the backing chain
- 2 runs produce exactly 2 backups (1 FULL + 1 delta) and exit 0

``test_config_to_restore_after_recovered_delta`` exercises the full
journey including bitmap-loss recovery: FULL + delta are created
normally, the newest checkpoint's dirty bitmap is removed from the top
layer (mechanism (c), ``qemu-img bitmap --remove``), the next run
detects the dead bitmap, logs a recovery warning, exits 0 and puts a
recovery FULL on the target, and the newest backup is restored to a
second VM which then boots.

Marked ``@pytest.mark.e2e`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from qsnap.cli.app import main
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running
from qsnap.utils.parsing import parse_timestamp
from tests.e2e.test_restore import _qemu_img_check_ok


@pytest.mark.e2e
@pytest.mark.timeout(3600)
def test_full_pipeline_from_config(e2e_vm):
    """Run ``qsnap run`` twice from a default TOML; no commit under 48."""
    shell: SubprocessShell = e2e_vm["shell"]
    vm_name: str = e2e_vm["vm_name"]
    config_path: Path = e2e_vm["config_path"]
    snapshot_dir: Path = e2e_vm["snapshot_dir"]
    target_dir: Path = e2e_vm["target_dir"]

    # Start the VM (external disk-only snapshots require an active domain).
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert is_vm_running(shell, vm_name), "VM should be running"

    # Run 1 and run 2 through the real CLI.
    rc1 = main(["--config", str(config_path), "run"])
    rc2 = main(["--config", str(config_path), "run"])

    # Exactly 2 snapshots were created (one per run).
    snap_files = sorted(snapshot_dir.glob("*.qcow2"))
    assert len(snap_files) == 2, (
        f"Expected 2 snapshot files after 2 runs, got {len(snap_files)}: "
        f"{[p.name for p in snap_files]}"
    )

    # Default floor (48) dominates chain_length (24): NO blockcommit, so
    # both snapshot files still exist (the floor is the whole point).
    for p in snap_files:
        assert p.exists(), f"Snapshot file must survive the 48 floor: {p}"

    # The second run must not duplicate the first run's transfer work:
    # exactly one FULL plus one delta appear on the target.
    backups = sorted(target_dir.glob("*.qcow2"))
    assert len(backups) == 2, (
        "Expected exactly 1 FULL + 1 incremental after 2 runs (no duplicate "
        f"transfers). Got {len(backups)}: {[p.name for p in backups]}"
    )

    # Backing chain intact.
    check_rc = main(["--config", str(config_path), "check"])
    assert check_rc == 0, f"qsnap check must pass on the backing chain, got rc={check_rc}"

    # Exit code 0 — no space errors, no failures.
    assert rc1 == 0, f"Run 1 must exit 0, got rc={rc1}"
    assert rc2 == 0, f"Run 2 must exit 0, got rc={rc2}"

    shell.run(["virsh", "destroy", vm_name], timeout=30)


def _active_layer_path(shell: SubprocessShell, vm_name: str) -> Path:
    """Return the active (top) layer of *vm_name*'s disk chain.

    ``virsh domblklist`` reports the current source file of each disk,
    which is the top of the qcow2 backing chain (the layer a fresh
    checkpoint's dirty bitmap is stored in).
    """
    result = shell.run(["virsh", "domblklist", "--domain", vm_name], timeout=30)
    assert result.success, f"virsh domblklist failed: {result.error}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "vda":
            return Path(parts[-1])
    raise AssertionError(f"cannot resolve active layer for vda from: {result.stdout!r}")


@pytest.mark.e2e
@pytest.mark.timeout(3600)
def test_config_to_restore_after_recovered_delta(e2e_vm, caplog):
    """Full journey incl. bitmap-loss recovery, then restore to a 2nd VM.

    1. Two ``qsnap run`` invocations create a FULL + a delta normally.
    2. The newest checkpoint's dirty bitmap is removed from the active
       (top) layer via ``qemu-img bitmap --remove`` (mechanism (c)).
    3. The next ``qsnap run`` detects the dead bitmap, logs a recovery
       warning, exits 0 and puts a fresh recovery backup on the target.
    4. The newest backup is restored to a second VM (via ``qsnap
       restore`` against a second domain/config) which then boots —
       reusing the ``tests/e2e/test_restore.py`` helpers.
    """
    shell: SubprocessShell = e2e_vm["shell"]
    vm_name: str = e2e_vm["vm_name"]
    config_path: Path = e2e_vm["config_path"]
    snapshot_dir: Path = e2e_vm["snapshot_dir"]
    target_dir: Path = e2e_vm["target_dir"]
    tmpdir: Path = e2e_vm["tmpdir"]

    # ── 1. Start the source VM ──────────────────────────────────────────
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert is_vm_running(shell, vm_name), "VM should be running"

    # ── 2. Create FULL + delta normally (two runs) ─────────────────────
    rc1 = main(["--config", str(config_path), "run"])
    assert rc1 == 0, f"Run 1 must exit 0, got rc={rc1}"
    # The checkpoint "newest" selection and superseded-checkpoint cleanup
    # compare second-resolution timestamps only (the 6-hex name suffix is
    # not a tiebreaker), so two checkpoints created within the same second
    # are ambiguous: the older one may be selected and the dead bitmap in
    # the top layer missed.  Space the runs out to keep this deterministic.
    time.sleep(1.1)
    rc2 = main(["--config", str(config_path), "run"])
    assert rc2 == 0, f"Run 2 must exit 0, got rc={rc2}"

    backups_after_run2 = sorted(target_dir.glob("*.qcow2"))
    assert len(backups_after_run2) == 2, (
        f"Expected 1 FULL + 1 delta after 2 runs, got {len(backups_after_run2)}: "
        f"{[p.name for p in backups_after_run2]}"
    )
    assert any(".FULL." in p.name for p in backups_after_run2), "FULL backup expected on target"
    assert any(".FULL." not in p.name for p in backups_after_run2), (
        "delta backup expected on target"
    )

    snap_files = sorted(snapshot_dir.glob("*.qcow2"))
    assert len(snap_files) == 2, (
        f"Expected 2 snapshot files after 2 runs, got {len(snap_files)}: "
        f"{[p.name for p in snap_files]}"
    )

    # ── 3. Manufacture a dead bitmap (mechanism (c)) ────────────────────
    # The newest checkpoint's dirty bitmap lives in the active layer.
    top_layer = _active_layer_path(shell, vm_name)
    assert top_layer.exists(), f"active layer missing: {top_layer}"

    cp_result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    assert cp_result.success, f"virsh checkpoint-list failed: {cp_result.error}"
    checkpoints = [
        line.strip()
        for line in cp_result.stdout.strip().splitlines()
        if line.strip().startswith("qsnap-")
    ]
    assert len(checkpoints) == 1, f"Expected exactly 1 checkpoint after 2 runs, got {checkpoints}"
    bitmap_name = checkpoints[0]

    # ``qemu-img bitmap --remove`` needs exclusive access — stop the VM.
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)
    assert not is_vm_running(shell, vm_name), "VM must be stopped for bitmap removal"

    rm = shell.run(
        ["qemu-img", "bitmap", "--remove", str(top_layer), bitmap_name],
        timeout=30,
    )
    assert rm.success, f"qemu-img bitmap --remove failed: {rm.error}"

    # ── 4. Recovery run: dead bitmap detected → warning + exit 0 + backup ──
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    assert start.success, f"VM restart failed: {start.error}"
    time.sleep(1)
    assert is_vm_running(shell, vm_name), "VM should be running for the recovery run"

    caplog.set_level(logging.WARNING)
    caplog.clear()
    rc3 = main(["--config", str(config_path), "run"])
    assert rc3 == 0, f"Recovery run must exit 0, got rc={rc3}"

    recovery_warned = any(
        "dead-bitmap checkpoint detected" in r.getMessage()
        or "bitmap is DEAD" in r.getMessage()
        or "entering recovery" in r.getMessage()
        for r in caplog.records
    )
    assert recovery_warned, (
        "A recovery warning must be logged when the dead bitmap is detected; "
        f"records: {[r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]}"
    )

    backups_after_run3 = sorted(target_dir.glob("*.qcow2"))
    assert len(backups_after_run3) == 3, (
        f"Expected a 3rd backup after the recovery run, got {len(backups_after_run3)}: "
        f"{[p.name for p in backups_after_run3]}"
    )
    newest_backup = max(backups_after_run3, key=lambda p: parse_timestamp(p.stem, p))
    assert newest_backup not in backups_after_run2, "Recovery run must create a new backup"
    # Current recovery implementation routes a dead bitmap to a FULL
    # fallback on the target (the recovered-delta path is the planned
    # successor) — either kind is the accepted recovery outcome.
    assert ".FULL." in newest_backup.name, (
        f"Expected a recovery FULL on target, got {newest_backup.name}"
    )

    # ── 5. Restore the newest backup to a SECOND VM and boot it ────────
    # (reuses test_restore.py helpers: _qemu_img_check_ok + the
    # stop → restore → verify → start → assert-running flow)
    shell.run(["virsh", "destroy", vm_name], timeout=30)
    time.sleep(0.5)

    second_vm = "qsnap-e2e-restore-vm"
    # Pre-cleanup: a crashed earlier run may have left the domain behind.
    shell.run(["virsh", "destroy", second_vm], timeout=30)
    shell.run(["virsh", "undefine", second_vm], timeout=30)

    second_disk = tmpdir / f"{second_vm}.qcow2"
    create2 = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(second_disk), "256M"],
        timeout=30,
    )
    assert create2.success, f"second VM disk create failed: {create2.error}"

    xml2 = (
        f'<domain type="qemu">\n'
        f"  <name>{second_vm}</name>\n"
        f"  <memory unit='KiB'>262144</memory>\n"
        f"  <vcpu placement='static'>1</vcpu>\n"
        f"  <os>\n"
        f"    <type arch='x86_64' machine='pc'>hvm</type>\n"
        f'    <boot dev="hd"/>\n'
        f"  </os>\n"
        f"  <devices>\n"
        f'    <disk type="file" device="disk">\n'
        f'      <driver name="qemu" type="qcow2"/>\n'
        f'      <source file="{second_disk}"/>\n'
        f'      <target dev="vda" bus="virtio"/>\n'
        f"    </disk>\n"
        f"  </devices>\n"
        f"</domain>\n"
    )
    xml2_path = tmpdir / f"{second_vm}.xml"
    xml2_path.write_text(xml2)
    define2 = shell.run(["virsh", "define", str(xml2_path)], timeout=30)
    assert define2.success, f"second VM define failed: {define2.error}"

    # The second VM gets its own config pointing at the same target; the
    # ``qsnap restore`` resolver discovers the backup by scanning it.
    snapshots2 = tmpdir / "snapshots2"
    snapshots2.mkdir(parents=True, exist_ok=True)
    restore_config = tmpdir / "qsnap-restore.toml"
    restore_config.write_text(
        f"[global]\n"
        f'state_dir = "{tmpdir / "state2"}"\n'
        f'lockfile = "off"\n'
        f"\n"
        f"[[vm]]\n"
        f'name = "{second_vm}"\n'
        f'snapshot_dir = "{snapshots2}"\n'
        f"\n"
        f"  [[vm.disk]]\n"
        f'  target = "vda"\n'
        f'  base_image = "{second_disk}"\n'
        f"\n"
        f"  [[vm.target]]\n"
        f'  path = "{target_dir}"\n'
    )

    restore_rc = main(["--config", str(restore_config), "restore", newest_backup.stem, "--yes"])
    assert restore_rc == 0, f"qsnap restore must succeed, got rc={restore_rc}"
    assert _qemu_img_check_ok(shell, second_disk), (
        f"Restored second VM disk must pass qemu-img check: {second_disk}"
    )

    start2 = shell.run(["virsh", "start", second_vm], timeout=30)
    assert start2.success, f"Second VM must boot after restore, got: {start2.error}"
    time.sleep(1)
    assert is_vm_running(shell, second_vm), "Second VM should be running after restore"

    # ── 6. Cleanup ──────────────────────────────────────────────────────
    shell.run(["virsh", "destroy", second_vm], timeout=30)
    shell.run(["virsh", "undefine", second_vm], timeout=30)
